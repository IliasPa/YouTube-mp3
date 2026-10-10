#!/usr/bin/env python3
"""YouTube → MP3

Bulk-saves the audio of your YouTube uploads as music files (MP3 or the
original audio), with title, date, artist, album and cover art filled in.
It can also convert video files you already have, e.g. a Google Takeout export.

Start it with Start.command (macOS), Start.bat (Windows) or start.sh (Linux), or
use the packaged app from the GitHub releases. It serves a small page on
http://127.0.0.1:<port> and does the work in a background thread; closing the
console window stops it. Re-running is safe: songs already saved are skipped.

  app.py --self-test [--online]   check that ffmpeg, the JavaScript runtime and
                                  the conversion work (--online: also YouTube)
"""

import glob
import json
import os
import random
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import webbrowser
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yt_dlp
from yt_dlp.postprocessor import PostProcessor
from yt_dlp.utils import DownloadCancelled, DownloadError, sanitize_filename

APP_VERSION = "0.4"  # keep in step with CHANGES.md and the git tag
FROZEN = getattr(sys, "frozen", False)  # running as the packaged app (PyInstaller)
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
BUNDLED_BIN = HERE / "bin"  # the packaged app ships ffmpeg and deno here
EXE = ".exe" if os.name == "nt" else ""
TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}  # subprocess output may contain any language
DATA_DIR = Path.home() / ".youtube_to_mp3_deps"
SETTINGS_FILE = DATA_DIR / "settings.json"
ARCHIVE_NAME = ".downloaded.txt"  # yt-dlp download archive inside the output folder
PORTS = range(8765, 8776)
RELEASES_API = "https://api.github.com/repos/IliasPa/YouTube-mp3/releases/latest"
RELEASES_PAGE = "https://github.com/IliasPa/YouTube-mp3/releases/latest"
UPDATE = {}  # {"version", "url"} when GitHub has a newer release (filled in the background)
PAUSE_BETWEEN = (3, 8)  # seconds between YouTube downloads, so a long run isn't rate-limited
CHANNEL_TABS = {"videos": "Videos", "streams": "Live", "shorts": "Shorts"}
MEDIA_EXTS = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".flv", ".3gp", ".mpg", ".mpeg",
              ".wmv", ".m4a", ".aac", ".wav", ".aif", ".aiff", ".flac", ".ogg", ".opus"}
AUDIO_EXTS = (".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav")
# Audio codec -> file extension when keeping the original audio without re-encoding
COPY_EXT = {"aac": "m4a", "alac": "m4a", "mp3": "mp3", "opus": "opus", "vorbis": "ogg", "flac": "flac"}
BLOCKED_SIGNS = ("not a bot", "HTTP Error 429", "rate-limit", "rate limit")
# A YouTube link anywhere in pasted text: videos, youtu.be, Shorts, live, playlists, channels, YouTube Music
LINK_RE = re.compile(r"(?<![\w.-])(?:https?://)?(?:[\w-]+\.)?(?:youtube\.com|youtu\.be)/[^\s<>\"'()\[\]{}]+", re.I)
SCAN_WORKERS = 4  # reading one video link takes a second or two, so read a few at once
ALBUM_PREFIX_RE = re.compile(r"^(?:Album|EP|Single) - ", re.I)  # YouTube Music album titles start like this
CHANNEL_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?youtube\.com/(@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)/?", re.I)

DEFAULT_SETTINGS = {
    "url": "",
    "links": "",
    "albums": "",
    "tabs": ["videos", "streams"],
    "folder": "",
    "out": str(Path.home() / "Music" / "YouTube MP3"),
    "format": "mp3_320",
    "artist": "",
    "album": "YouTube Archive",
    "date_prefix": True,
    "square_cover": True,
    "song_tags": False,  # off: covers can be linked to the original song and get its artist
    "split_chapters": False,
    "cookies": "",
}


class UserError(Exception):
    """A problem the user can fix; shown on the page as-is."""


# ---------- tools ----------

def find_ffmpeg():
    """The packaged app's ffmpeg, else one on the PATH, else the copy in the imageio-ffmpeg package."""
    for path in (BUNDLED_BIN / f"ffmpeg{EXE}", shutil.which("ffmpeg")):
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return str(path)
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # not installed, or no binary for this system
        return None


def find_js_runtime():
    """yt-dlp needs a JavaScript runtime to solve YouTube's download challenges.
    Prefers the bundled/pip-installed Deno, which is the one yt-dlp recommends."""
    bundled = BUNDLED_BIN / f"deno{EXE}"
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return "deno", str(bundled)
    try:
        import deno
        return "deno", deno.find_deno_bin()
    except Exception:
        pass
    for name in ("deno", "node", "bun"):
        if path := shutil.which(name):
            return name, path
    # nvm installs Node outside the PATH a double-clicked app gets
    nodes = glob.glob(str(Path.home() / ".nvm/versions/node/v*/bin/node"))
    if nodes:
        return "node", max(nodes, key=lambda p: [int(n) for n in re.findall(r"\d+", Path(p).parents[1].name)])
    return None


def version_tuple(text):
    return tuple(int(n) for n in re.findall(r"\d+", text))


def check_for_update():
    """Asks GitHub for the newest release so the page can offer it: YouTube changes often, and
    a packaged app keeps working only with a recent yt-dlp. Offline or rate-limited: no notice."""
    try:
        import certifi  # the packaged app can't always find the system's certificates
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    request = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"YouTube-MP3/{APP_VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=10, context=context) as response:
            release = json.load(response)
        latest = release["tag_name"].lstrip("v")
        if version_tuple(latest) > version_tuple(APP_VERSION):
            UPDATE.update(version=latest, url=release.get("html_url") or RELEASES_PAGE)
    except Exception:
        pass


FFMPEG = find_ffmpeg()
FFPROBE = FFMPEG and shutil.which("ffprobe", path=str(Path(FFMPEG).parent))
JS_RUNTIME = find_js_runtime()


# ---------- settings & archive ----------

def load_settings():
    try:
        return {**DEFAULT_SETTINGS, **json.loads(SETTINGS_FILE.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)


def save_settings(changes):
    settings = load_settings()
    settings.update({k: v for k, v in changes.items() if k in DEFAULT_SETTINGS})
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False))
    return settings


def out_dir(settings):
    out = (settings.get("out") or "").strip()
    if not out:
        raise UserError("Choose a folder to save the songs in.")
    return Path(out).expanduser()


def archive_id(line):
    parts = line.split()
    return parts[1] if len(parts) == 2 and parts[0] == "youtube" else None


def read_archive(out):
    try:
        lines = (Path(out) / ARCHIVE_NAME).read_text().splitlines()
    except OSError:
        return set()
    return {archive_id(line) for line in lines} - {None}


def forget_in_archive(out, ids):
    """Lets yt-dlp fetch these again; it still skips any whose file is already there."""
    path = Path(out) / ARCHIVE_NAME
    keep = [line for line in path.read_text().splitlines() if archive_id(line) not in ids]
    path.write_text("".join(line + "\n" for line in keep))


def clean_error(err):
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(err)).replace("ERROR: ", "").strip()
    return re.sub(r"^\[[\w:]+\] [\w-]+: ", "", msg)


# ---------- job (one at a time) ----------

class Job:
    def __init__(self, mode, items):
        self.mode = mode
        self.items = items
        self.lock = threading.Lock()
        self.results = {}  # item id -> "working" | "ok" | "skipped" | "failed" | "stopped"
        self.failed = []
        self.log = deque(maxlen=300)
        self.index = 0
        self.current = ""
        self.stage = ""
        self.percent = 0.0
        self.message = ""
        self.cancel = False
        self.finished = False

    def set_result(self, item, status, reason=""):
        with self.lock:
            self.results[item["id"]] = status
            if status == "failed":
                self.failed.append({"title": display_name(item), "reason": reason})

    def add_log(self, line):
        line = re.sub(r"\x1b\[[0-9;]*m", "", line).rstrip()
        if line:
            print(line, flush=True)
            with self.lock:
                self.log.append(line)

    def snapshot(self, with_items=False):
        with self.lock:
            counts = Counter(self.results.values())
            return {
                "mode": self.mode, "items": self.items if with_items else None, "total": len(self.items),
                "index": self.index, "current": self.current, "stage": self.stage,
                "percent": round(self.percent, 1), "ok": counts["ok"], "skipped": counts["skipped"],
                "failed": list(self.failed), "results": dict(self.results),
                "message": self.message, "cancelled": self.cancel, "finished": self.finished,
                "log": list(self.log)[-80:],
            }

    def pause(self, seconds):
        end = time.time() + seconds
        while time.time() < end and not self.cancel:
            time.sleep(0.2)


JOB = {"current": None}


def display_name(item):
    """The title the user sees and the file gets: their edited title, else the video's."""
    return (item.get("name") or "").strip() or item["title"]


class JobLogger:
    """Routes yt-dlp's messages into the job log instead of the terminal."""

    def __init__(self, job):
        self.job = job

    def debug(self, msg):
        if not msg.startswith("[debug] "):
            self.job.add_log(msg)

    info = debug

    def warning(self, msg):
        self.job.add_log("WARNING: " + msg)

    def error(self, msg):
        self.job.add_log(msg)


# ---------- YouTube ----------

class SetTags(PostProcessor):
    """Sets the tags of the video being downloaded; the job updates the fields per video.
    FFmpegMetadata gives meta_* fields priority over what yt-dlp found."""

    def __init__(self, artist="", album="", song_tags=False):
        super().__init__()
        self.artist, self.album, self.song_tags = artist, album, song_tags
        self.album_artist = ""  # album mode: the album's artist
        self.track = ""         # album mode: "3/12"
        self.title = ""         # a title the user edited; wins over everything else

    def run(self, info):
        artists = info.get("artists") or ([info["artist"]] if info.get("artist") else [])
        # A song YouTube Music knows comes with its own title, artist, album and release date
        use_song = bool(self.track) or (self.song_tags and bool(info.get("track") and artists))
        if self.track:  # album mode: the album's title and artist; each track keeps its own artist
            info["meta_track"] = self.track
            if self.album:
                info["meta_album"] = self.album
            if self.album_artist:
                info["meta_album_artist"] = self.album_artist
                if not artists:
                    info["meta_artist"] = self.album_artist
        elif use_song:  # the song's own details; the Album setting only fills a missing album
            info["meta_album_artist"] = info.get("album_artist") or artists[0]
            if not info.get("album") and self.album:
                info["meta_album"] = self.album
        else:  # the video's own title, with artist and album from the settings
            info["meta_title"] = info.get("title")
            if self.artist:
                info["meta_artist"] = info["meta_album_artist"] = self.artist
            if self.album:
                info["meta_album"] = self.album
        # Songs are dated by their release, videos by their upload
        date = (use_song and (info.get("release_date") or info.get("release_year"))) or info.get("upload_date")
        date = str(date or "")
        if len(date) == 8:
            info["meta_date"] = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        elif date:
            info["meta_date"] = date
        if self.title:
            info["meta_title"] = self.title
        return [], info


class SplitChapters(PostProcessor):
    """Cuts a video with chapters (e.g. a live set) into one file per chapter, in a folder named
    after the video: numbered tracks titled by chapter, with the video's title as the album.
    Runs last, so every part keeps the finished file's tags and cover."""

    def __init__(self):
        super().__init__()
        self.album = ""  # the job sets the video's (possibly edited) title per video

    def run(self, info):
        path = Path(info.get("filepath") or "")
        chapters = [c for c in info.get("chapters") or [] if c.get("end_time", 0) > c.get("start_time", 0)]
        if len(chapters) < 2 or not path.is_file():
            return [], info
        split_into_chapters(path, chapters, self.album or info.get("title") or path.stem)
        return [], info


def split_into_chapters(path, chapters, album):
    """Writes "<file name>/01 - <chapter>.<ext>" for each chapter and removes the full file."""
    folder = path.with_suffix("")
    folder.mkdir(exist_ok=True)
    width = max(2, len(str(len(chapters))))
    for number, chapter in enumerate(chapters, 1):
        title = (chapter.get("title") or "").strip() or f"Part {number}"
        dest = folder / f"{str(number).zfill(width)} - {folder_name(title)}{path.suffix}"
        start, end = chapter["start_time"], chapter["end_time"]
        subprocess.run(
            [FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", str(path),
             "-t", f"{end - start:.3f}", "-map", "0", "-c", "copy", "-map_chapters", "-1",
             "-metadata", f"title={title}", "-metadata", f"track={number}/{len(chapters)}",
             "-metadata", f"album={album}", str(dest)],
            check=True, capture_output=True)
    path.unlink()
    return folder


class SquareCover(PostProcessor):
    """Center-crops the downloaded thumbnail to a square so it looks right as album art."""

    def run(self, info):
        for thumb in info.get("thumbnails") or []:
            path = thumb.get("filepath")
            if not path or not os.path.exists(path):
                continue
            tmp = path + ".square.jpg"
            result = subprocess.run(
                [FFMPEG, "-nostdin", "-y", "-loglevel", "error", "-i", path,
                 "-vf", "crop='min(iw,ih)':'min(iw,ih)'", "-q:v", "2", tmp],
                capture_output=True)
            if result.returncode == 0:
                os.replace(tmp, path)
            elif os.path.exists(tmp):
                os.remove(tmp)
        return [], info


def base_args(settings):
    args = ["--ignore-config", "--color", "never"]
    if FFMPEG:
        args += ["--ffmpeg-location", FFMPEG]
    if JS_RUNTIME:
        args += ["--js-runtimes", f"{JS_RUNTIME[0]}:{JS_RUNTIME[1]}"]
    if settings.get("cookies"):
        args += ["--cookies-from-browser", settings["cookies"]]
    return args


def expand_sources(text, tabs):
    """Turns the pasted text into URLs; a bare channel link becomes one URL per chosen tab."""
    sources = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("@"):
            line = "https://www.youtube.com/" + line
        elif not re.match(r"https?://", line):
            line = "https://" + line
        if m := CHANNEL_RE.fullmatch(line.split("?")[0]):
            sources += [(f"https://www.youtube.com/{m.group(1)}/{tab}", CHANNEL_TABS[tab]) for tab in tabs]
        else:
            sources.append((line, "Link"))
    return sources


def find_links(text):
    """Picks the YouTube links out of pasted text: one per line, or mixed into a longer message."""
    links = []
    for match in LINK_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?")
        if not re.match(r"https?://", url, re.I):
            url = "https://" + url
        if url not in links:
            links.append(url)
    return links


def video_entries(info):
    if info.get("_type") in ("playlist", "multi_video"):
        for entry in info.get("entries") or []:
            if entry:
                yield from video_entries(entry)
    elif info.get("ie_key") in (None, "Youtube") and info.get("id"):
        yield info


def read_source(source, settings):
    """Lists what one link points to (a video, a playlist or a channel tab) without downloading."""
    url, label = source
    opts = yt_dlp.parse_options(base_args(settings)).ydl_opts
    # ignoreerrors=False: the CLI default would return None for a failed link instead of raising.
    # noplaylist: a video link that also names a playlist (watch?v=…&list=…) means just that video.
    opts.update(extract_flat="in_playlist", skip_download=True, quiet=True, no_warnings=True,
                ignoreerrors=False, noplaylist=True)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return url, label, ydl.extract_info(url, download=False), None
    except DownloadError as err:
        return url, label, None, err


def scan_sources(sources, settings):
    archive = read_archive(out_dir(settings))
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:  # keeps the links' order
        results = list(pool.map(lambda source: read_source(source, settings), sources))
    items, seen, channels, problems = [], set(), [], []
    for url, label, info, err in results:
        if err:
            if "does not have a" not in str(err):  # a channel without Shorts or Live isn't a problem
                problems.append(f"{url}: {clean_error(err)}")
            continue
        if not info:
            continue
        if owner := info.get("channel") or info.get("uploader"):
            channels.append(owner)
        if label == "Link" and info.get("_type") == "playlist":
            label = info.get("title") or "Playlist"
        for entry in video_entries(info):
            if entry["id"] in seen:
                continue
            seen.add(entry["id"])
            items.append({
                "id": entry["id"],
                "title": entry.get("title") or entry["id"],
                "url": entry.get("webpage_url") or entry.get("url") or f"https://www.youtube.com/watch?v={entry['id']}",
                "duration": entry.get("duration"),
                "section": label,
                "done": entry["id"] in archive,
            })
    if not items and problems:
        raise UserError(f"Couldn't read {'that link' if len(problems) == 1 else 'those links'}. {problems[0]}")
    # Same title twice (e.g. two takes of one song): add the video id so neither file overwrites the other
    counts = Counter(i["title"].casefold() for i in items)
    for item in items:
        item["dup"] = counts[item["title"].casefold()] > 1
    # Suggest an artist name only when everything comes from one channel
    channel = channels[0] if len(set(channels)) == 1 else ""
    return {"channel": channel, "items": items, "problems": problems}


def scan_youtube(body):
    settings = save_settings(body)
    tabs = [t for t in settings["tabs"] if t in CHANNEL_TABS]
    sources = expand_sources(settings["url"], tabs)
    if not sources:
        if settings["url"].strip():  # only a channel link, with no tab ticked
            raise UserError("Tick at least one of Videos, Live streams or Shorts.")
        raise UserError("Paste your channel link first, e.g. https://www.youtube.com/@yourname")
    return scan_sources(sources, settings)


def folder_name(text):
    """A folder name that works on Windows, macOS and Linux."""
    return sanitize_filename(text).strip().rstrip(". ")[:120] or "Album"


def scan_albums(body):
    """Lists the tracks of YouTube Music albums (any YouTube playlist link works too)."""
    settings = save_settings(body)
    links = find_links(settings["albums"])
    if not links:
        raise UserError("No album links found. Paste YouTube Music album links like "
                        "https://music.youtube.com/playlist?list=OLAK5uy_… one per line.")
    archive = read_archive(out_dir(settings))
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        results = list(pool.map(lambda url: read_source((url, "Album"), settings), links))
    items, seen, albums, problems = [], set(), set(), []
    for url, _, info, err in results:
        if err:
            problems.append(f"{url}: {clean_error(err)}")
            continue
        if not info or info.get("id") in albums:
            continue
        if info.get("_type") != "playlist":
            problems.append(f"{url}: this is a single video, not an album. Use Paste links for it.")
            continue
        albums.add(info["id"])
        tracks = list(video_entries(info))
        album = ALBUM_PREFIX_RE.sub("", info.get("title") or "").strip() or "Unknown album"
        # The album page names no artist; its tracks do (an auto-made "… - Topic" channel counts too)
        owners = Counter(e.get("channel") or e.get("uploader") for e in tracks)
        owners.pop(None, None)
        artist = info.get("channel") or info.get("uploader") or (owners.most_common(1)[0][0] if owners else "")
        artist = re.sub(r" - Topic$", "", artist)
        for number, entry in enumerate(tracks, 1):
            if entry["id"] in seen:
                continue
            seen.add(entry["id"])
            items.append({
                "id": entry["id"],
                "title": entry.get("title") or entry["id"],
                "url": entry.get("url") or f"https://www.youtube.com/watch?v={entry['id']}",
                "duration": entry.get("duration"),
                "section": f"{artist} – {album}" if artist else album,
                "done": entry["id"] in archive,
                "album": album,
                "album_artist": artist,
                "track": number,
                "tracks": len(tracks),
            })
    if not items and problems:
        raise UserError(f"Couldn't read {'that link' if len(problems) == 1 else 'those links'}. {problems[0]}")
    return {"channel": "", "items": items, "problems": problems}


def scan_links(body):
    settings = save_settings(body)
    links = find_links(settings["links"])
    if not links:
        raise UserError("No YouTube links found. Paste links like https://www.youtube.com/watch?v=… "
                        "or https://youtu.be/…, one per line.")
    # A channel link pasted here brings all of its videos, live streams and Shorts
    return scan_sources(expand_sources("\n".join(links), list(CHANNEL_TABS)), settings)


def run_youtube_job(job, settings):
    out = out_dir(settings)
    out.mkdir(parents=True, exist_ok=True)
    args = base_args(settings) + [
        "--no-playlist", "-f", "bestaudio/best", "-x",
        "--embed-metadata", "--embed-thumbnail", "--convert-thumbnails", "jpg",
        "--download-archive", str(out / ARCHIVE_NAME), "-P", str(out),
        "--windows-filenames", "--trim-filenames", "120",
        "--retries", "10", "--fragment-retries", "10",
    ]
    if settings["format"] in ("mp3_320", "mp3_192"):
        args += ["--audio-format", "mp3", "--audio-quality", "320K" if settings["format"] == "mp3_320" else "192K"]
    # "original": -x alone keeps YouTube's own audio stream (Opus or AAC) without re-encoding

    def progress(d):
        if job.cancel:
            raise DownloadCancelled("Stopped")
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            job.stage = "Downloading"
            if total:
                job.percent = min(99.0, d.get("downloaded_bytes", 0) * 100 / total)
        elif d["status"] == "finished":
            job.stage, job.percent = "Converting", 100.0

    # Songs saved earlier that were ticked again (maybe the file was deleted): fetch them again
    archive = read_archive(out)
    forced = {item["id"] for item in job.items if item.get("force")} & archive
    if forced:
        forget_in_archive(out, forced)
        archive -= forced

    opts = yt_dlp.parse_options(args).ydl_opts
    # ignoreerrors=False so a failed video raises and is reported, instead of silently counting as saved
    opts.update(logger=JobLogger(job), noprogress=True, progress_hooks=[progress], ignoreerrors=False)
    albums = job.mode == "albums"
    date_prefix = "%(upload_date>%Y-%m-%d)s - " if settings["date_prefix"] else ""
    # Two videos that would get the same file name (e.g. titles edited alike) get the video id added
    name_counts = Counter(display_name(i).casefold() for i in job.items)
    # Albums take artist and album from each album; the other tabs from the settings
    tags = SetTags() if albums else SetTags(settings["artist"].strip(), settings["album"].strip(), settings["song_tags"])
    splitter = SplitChapters() if settings["split_chapters"] and not albums and FFMPEG else None

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.add_post_processor(tags, when="pre_process")
        if settings["square_cover"] and FFMPEG:
            ydl.add_post_processor(SquareCover(), when="before_dl")  # runs after the jpg conversion
        if splitter:
            ydl.add_post_processor(splitter, when="post_process")  # after the tags and cover are in
        downloaded_any = False
        for job.index, item in enumerate(job.items, 1):
            if job.cancel:
                break
            job.current, job.stage, job.percent = display_name(item), "Starting", 0.0
            if item["id"] in archive:
                job.set_result(item, "skipped")
                continue
            if downloaded_any:
                job.stage = "Pausing a few seconds (keeps YouTube happy)"
                job.pause(random.uniform(*PAUSE_BETWEEN))
                if job.cancel:
                    break
            job.set_result(item, "working")
            custom = (item.get("name") or "").strip()  # a title the user edited
            tags.title = custom
            title = folder_name(custom).replace("%", "%%") if custom else None
            if albums:  # "Artist - Album/03 - Track.mp3"
                tags.album, tags.album_artist = item.get("album", ""), item.get("album_artist", "")
                tags.track = f"{item['track']}/{item['tracks']}"
                folder = folder_name(" - ".join(filter(None, (tags.album_artist, tags.album))))
                number = str(item["track"]).zfill(max(2, len(str(item["tracks"]))))
                template = f"{folder.replace('%', '%%')}/{number} - {title or '%(track,title)s'}.%(ext)s"
            else:
                dup = item.get("dup") or name_counts[display_name(item).casefold()] > 1
                template = date_prefix + (title or "%(title)s") + (" [%(id)s]" if dup else "") + ".%(ext)s"
            if splitter:
                splitter.album = display_name(item)
            ydl.params["outtmpl"]["default"] = template
            try:
                ydl.download([item["url"]])
                job.set_result(item, "ok")
                downloaded_any = True
            except DownloadCancelled:
                job.set_result(item, "stopped")
                break
            except Exception as err:  # one bad video shouldn't stop the whole run
                reason = clean_error(err)
                job.set_result(item, "failed", reason)
                if any(sign in reason for sign in BLOCKED_SIGNS):
                    job.message = ("YouTube is temporarily blocking downloads from this computer. "
                                   "Wait an hour and press Download again (saved songs are skipped), or "
                                   "pick your browser under \"If YouTube blocks the downloads\".")
                    break


# ---------- local files (e.g. Google Takeout) ----------

def audio_codec(path):
    """Codec of the first audio track, e.g. "aac". Uses ffprobe when there is one (the
    bundled ffmpeg comes without it), otherwise reads ffmpeg's own description of the file."""
    if FFPROBE:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
             "-of", "default=nw=1:nk=1", str(path)], capture_output=True, **TEXT)
        return result.stdout.strip() or None
    result = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(path)], capture_output=True, **TEXT)
    match = re.search(r"Stream #\d+:\d+\S*: Audio: (\w+)", result.stderr)
    return match.group(1) if match else None


def already_converted(out, stem):
    return any((out / (stem + ext)).exists() for ext in AUDIO_EXTS)


def scan_folder(body):
    settings = save_settings(body)
    folder = (settings.get("folder") or "").strip()
    if not folder:
        raise UserError("Choose the folder that contains your video files.")
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise UserError(f"Folder not found: {root}")
    out = out_dir(settings)
    items = []
    for path in sorted(root.rglob("*"), key=lambda p: str(p).casefold()):
        if (path.suffix.lower() not in MEDIA_EXTS or path.name.startswith(".") or not path.is_file()
                or out in path.parents):
            continue
        items.append({"id": str(path), "title": path.stem, "size": path.stat().st_size,
                      "section": str(path.parent.relative_to(root)) if path.parent != root else "",
                      "done": already_converted(out, path.stem)})
    if not items:
        raise UserError("No video or audio files found in that folder. "
                        "If it's a Google Takeout .zip, unzip it first.")
    return {"channel": "", "items": items, "problems": []}


def run_folder_job(job, settings):
    out = out_dir(settings)
    out.mkdir(parents=True, exist_ok=True)
    artist, album = settings["artist"].strip(), settings["album"].strip()
    tags = []
    if artist:
        tags += ["-metadata", f"artist={artist}", "-metadata", f"album_artist={artist}"]
    if album:
        tags += ["-metadata", f"album={album}"]

    for job.index, item in enumerate(job.items, 1):
        if job.cancel:
            break
        src = Path(item["id"])
        job.current, job.stage, job.percent = display_name(item), "Converting", 0.0
        stem = folder_name(item["name"]) if (item.get("name") or "").strip() else src.stem
        if settings["format"] == "original":
            codec = audio_codec(src)
            if codec and codec.startswith("pcm_"):
                ext, codec_args = "wav", ["-c:a", "copy"]
            elif codec in COPY_EXT:
                ext, codec_args = COPY_EXT[codec], ["-c:a", "copy"]
            else:  # unusual codec: keep it lossless
                ext, codec_args = "flac", ["-c:a", "flac"]
        else:
            ext = "mp3"
            codec_args = ["-c:a", "libmp3lame", "-b:a", "320k" if settings["format"] == "mp3_320" else "192k"]
        dest = out / f"{stem}.{ext}"
        if dest.exists() and not item.get("force"):
            job.set_result(item, "skipped")
            continue
        job.set_result(item, "working")
        tmp = out / f"{stem}.part.{ext}"  # renamed when complete, so a stopped run leaves no half file
        cmd = [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
               "-map", "0:a:0", "-map_metadata", "-1", *codec_args,  # drop the video container's own tags
               "-metadata", f"title={display_name(item)}", *tags, str(tmp)]
        job.add_log("Converting " + src.name)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **TEXT)
        while proc.poll() is None:
            if job.cancel:
                proc.terminate()
            time.sleep(0.2)
        err = proc.stderr.read().strip()
        if proc.returncode == 0:
            os.replace(tmp, dest)
            job.set_result(item, "ok")
        else:
            tmp.unlink(missing_ok=True)
            if job.cancel:
                job.set_result(item, "stopped")
                break
            reason = "This file has no audio track." if "matches no streams" in err else (err.splitlines() or ["ffmpeg failed"])[-1]
            job.add_log(f"ERROR: {src.name}: {reason}")
            job.set_result(item, "failed", reason)


# ---------- API ----------

def start_job(body):
    current = JOB["current"]
    if current and not current.finished:
        raise UserError("A download is already running.")
    if not FFMPEG:
        raise UserError("ffmpeg is missing. Start the app again with Start.command, Start.bat or start.sh to install it.")
    mode = body.get("mode")
    items = [i for i in body.get("items") or [] if isinstance(i, dict) and i.get("id")]
    if mode not in ("youtube", "links", "albums", "folder") or not items:
        raise UserError("Select at least one item.")
    settings = save_settings(body.get("settings") or {})
    if settings["format"] not in ("mp3_320", "mp3_192", "original"):
        raise UserError("Unknown format.")
    out_dir(settings)
    job = Job(mode, items)
    JOB["current"] = job
    runner = run_folder_job if mode == "folder" else run_youtube_job

    def work():
        try:
            runner(job, settings)
        except Exception as err:
            traceback.print_exc()
            job.message = f"Something went wrong: {clean_error(err)}"
        finally:
            job.stage, job.finished = "", True

    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


def cancel_job(_body):
    if job := JOB["current"]:
        job.cancel = True
    return {"ok": True}


def applescript_string(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def choose_folder(body):
    """Shows the system's own folder picker."""
    prompt = str(body.get("prompt") or "Choose a folder")
    start = Path(str(body.get("start") or "")).expanduser()
    start = str(start) if start.is_dir() else ""
    env = None
    if sys.platform == "darwin":
        script = f"POSIX path of (choose folder with prompt {applescript_string(prompt)}"
        if start:
            script += f" default location (POSIX file {applescript_string(start)} as alias)"
        cmd = ["osascript", "-e", "activate", "-e", script + ")"]
    elif os.name == "nt":
        # Prompt and start folder go through the environment, so no quoting is needed
        script = ("Add-Type -AssemblyName System.Windows.Forms;"
                  "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
                  "$d.Description = $env:YTMP3_PROMPT; $d.SelectedPath = $env:YTMP3_START;"
                  "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true};"
                  "[Console]::OutputEncoding = [Text.Encoding]::UTF8;"
                  "if ($d.ShowDialog($owner) -eq 'OK') { $d.SelectedPath }")
        cmd = ["powershell", "-NoProfile", "-STA", "-Command", script]
        env = dict(os.environ, YTMP3_PROMPT=prompt, YTMP3_START=start)
    elif shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory", f"--title={prompt}"]
        if start:
            cmd.append(f"--filename={start}/")
    elif shutil.which("kdialog"):
        cmd = ["kdialog", "--getexistingdirectory", start or str(Path.home()), "--title", prompt]
    else:
        raise UserError("No folder picker found on this computer. Type or paste the folder path instead.")
    result = subprocess.run(cmd, capture_output=True, env=env, **TEXT)
    path = result.stdout.strip()  # empty when the user pressed Cancel
    return {"path": (path.rstrip("/\\") or path) or None}


def open_folder(body):
    path = Path(str(body.get("path") or "")).expanduser()
    if not path.is_dir():
        raise UserError("That folder doesn't exist yet. It's created when the first song is saved.")
    if os.name == "nt":
        os.startfile(path)
    else:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
    return {"ok": True}


def get_state(with_items):
    job = JOB["current"]
    return {
        "settings": load_settings(),
        "tools": {
            "version": APP_VERSION,
            "ffmpeg": bool(FFMPEG),
            "js": JS_RUNTIME[0] if JS_RUNTIME else None,
            "ytdlp": yt_dlp.version.__version__,
        },
        "job": job.snapshot(with_items) if job else None,
        "update": UPDATE or None,
    }


ROUTES = {
    "/api/scan_youtube": scan_youtube,
    "/api/scan_links": scan_links,
    "/api/scan_albums": scan_albums,
    "/api/scan_folder": scan_folder,
    "/api/start": start_job,
    "/api/cancel": cancel_job,
    "/api/choose_folder": choose_folder,
    "/api/open_folder": open_folder,
}
ALLOWED_HOSTS = set()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the Terminal window readable
        pass

    def send_body(self, code, body, content_type="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # Only answer requests addressed to this machine (blocks DNS-rebinding tricks from websites)
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            return self.send_body(403, {"error": "Forbidden"})
        if self.path == "/":
            return self.send_body(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if self.path in ("/api/state", "/api/state?items=1"):  # items: only when the page (re)loads
            return self.send_body(200, get_state(self.path.endswith("items=1")))
        self.send_body(404, {"error": "Not found"})

    def do_POST(self):
        # A JSON content type forces a CORS preflight, so other websites can't trigger actions here
        if (self.headers.get("Host") not in ALLOWED_HOSTS
                or not self.headers.get("Content-Type", "").startswith("application/json")):
            return self.send_body(403, {"error": "Forbidden"})
        route = ROUTES.get(self.path)
        if not route:
            return self.send_body(404, {"error": "Not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.send_body(400, {"error": "Bad request"})
        try:
            self.send_body(200, route(body))
        except UserError as err:
            self.send_body(400, {"error": str(err)})
        except Exception as err:
            traceback.print_exc()
            self.send_body(500, {"error": f"Unexpected error: {clean_error(err)}"})


def self_test(online):
    """Checks the tools and the conversion pipeline on this computer. CI runs it on every system."""
    print(f"YouTube → MP3 {APP_VERSION} self-test: {sys.platform}, Python {sys.version.split()[0]}, "
          f"yt-dlp {yt_dlp.version.__version__}, packaged={FROZEN}")
    print(f"  ffmpeg:     {FFMPEG}\n  ffprobe:    {FFPROBE or '(none, ffmpeg is used instead)'}\n  JS runtime: {JS_RUNTIME}")
    failures = []

    def check(ok, label):
        print(("  PASS " if ok else "  FAIL ") + label)
        if not ok:
            failures.append(label)

    check(FFMPEG and JS_RUNTIME, "ffmpeg and a JavaScript runtime found")
    if failures:
        return 1
    js = subprocess.run([JS_RUNTIME[1], "--version"], capture_output=True, **TEXT)
    check(js.returncode == 0, f"{JS_RUNTIME[0]} runs: {(js.stdout.splitlines() or ['?'])[0]}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        video = tmp / "Δοκιμή ήχου.mp4"  # non-ASCII on purpose: song titles often are
        thumb = tmp / "thumb.jpg"
        lavfi = [FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i"]
        subprocess.run([*lavfi, "sine=frequency=440:duration=2", "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=2",
                        "-shortest", "-c:v", "mpeg4", "-c:a", "aac", str(video)], check=True)
        subprocess.run([*lavfi, "color=c=red:s=1280x720", "-frames:v", "1", str(thumb)], check=True)

        SquareCover().run({"thumbnails": [{"filepath": str(thumb)}]})
        probe = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(thumb)], capture_output=True, **TEXT)
        check(", 720x720" in probe.stderr, "cover art cropped to a square")
        check(audio_codec(video) == "aac", f"audio codec detected: {audio_codec(video)}")

        for fmt, ext in (("mp3_320", "mp3"), ("original", "m4a")):
            job = Job("folder", [{"id": str(video), "title": video.stem}])
            run_folder_job(job, dict(DEFAULT_SETTINGS, out=str(tmp / fmt), format=fmt, artist="Test"))
            check(job.results.get(str(video)) == "ok" and (tmp / fmt / f"{video.stem}.{ext}").is_file(),
                  f"video file converted to .{ext}")

        # A "live set" with three chapters becomes three numbered tracks
        live = tmp / "Live set.mp3"
        subprocess.run([*lavfi, "sine=frequency=330:duration=6", "-c:a", "libmp3lame", "-b:a", "128k",
                        "-metadata", "artist=Test", str(live)], check=True)
        chapters = [{"start_time": 0, "end_time": 2, "title": "Intro"},
                    {"start_time": 2, "end_time": 4, "title": "Σπάνιο: τραγούδι?"},
                    {"start_time": 4, "end_time": 6, "title": ""}]
        folder = split_into_chapters(live, chapters, "Live set")
        parts = sorted(p.name for p in folder.iterdir())
        probe = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(folder / parts[1])],
                               capture_output=True, **TEXT).stderr if len(parts) == 3 else ""
        check(len(parts) == 3 and parts[0] == "01 - Intro.mp3" and parts[2] == "03 - Part 3.mp3"
              and not live.exists() and re.search(r"track\s*: 2/3", probe) and re.search(r"album\s*: Live set", probe),
              f"live set split into chapters: {parts}")

        if online:  # YouTube sometimes blocks cloud machines, so CI treats this part as optional
            item = {"id": "jNQXAC9IVRw", "title": "Me at the zoo", "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}
            job = Job("youtube", [item])
            run_youtube_job(job, dict(DEFAULT_SETTINGS, out=str(tmp / "youtube")))
            check(job.results.get(item["id"]) == "ok" and any((tmp / "youtube").glob("*.mp3")),
                  f"YouTube video downloaded as MP3 {job.failed or ''}")

    print("Self-test " + ("FAILED: " + "; ".join(failures) if failures else "passed"))
    return 1 if failures else 0


def main():
    for stream in (sys.stdout, sys.stderr):  # a Windows console may not be able to show every character
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass
    if "--version" in sys.argv:
        print(APP_VERSION)
        return
    if "--self-test" in sys.argv:
        sys.exit(self_test(online="--online" in sys.argv))

    for port in PORTS:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    else:
        raise RuntimeError(f"Ports {PORTS.start}-{PORTS.stop - 1} are all busy. Is the app already open?")
    ALLOWED_HOSTS.update({f"127.0.0.1:{port}", f"localhost:{port}"})
    url = f"http://127.0.0.1:{port}/"
    # flush: the packaged app ignores PYTHONUNBUFFERED, and a log or pipe should see this line at once
    print(f"\n  YouTube → MP3 {APP_VERSION} is running at {url}\n"
          f"  Keep this window open while it works. Close it (or press Ctrl+C) to quit.\n", flush=True)
    if not FFMPEG:
        print("  ! ffmpeg not found. Run Start.command / Start.bat / start.sh again to install it.\n")
    if not JS_RUNTIME:
        print("  ! No JavaScript runtime found; YouTube downloads may fail.\n")
    threading.Thread(target=check_for_update, daemon=True).start()
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Bye!")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        if FROZEN and sys.stdin and sys.stdin.isatty():  # keep a double-clicked app's window open
            input("\nSomething went wrong (details above). Press Enter to close.")
        sys.exit(1)
