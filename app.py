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
import math
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

APP_VERSION = "0.6"  # keep in step with CHANGES.md and the git tag
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
FORMATS = ("mp3_320", "mp3_192", "flac", "wav", "original")
COVER_EXTS = ("mp3", "flac", "m4a")  # containers ffmpeg can put a cover picture in
SQUARE_CROP = "crop='min(iw,ih)':'min(iw,ih)'"  # centre square of a picture
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"  # -14 LUFS, true peak -1.5 dB: what streaming services use
SILENCE = "silencedetect=noise=-50dB:d=0.5"  # half a second or more below -50 dB counts as silence
EXTRACT_AUDIO_ARGS = "extractaudio+ffmpeg_o"  # where yt-dlp looks for extra ffmpeg options when converting
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
    "normalize": False,  # even out the volume
    "cover": "",         # the user's own cover image; empty: the video thumbnail
    "lyrics": False,     # the video description as lyrics
    "trim_silence": False,  # cut silence at the start and end of each song
    "notify": True,      # sound and notification when a run finishes
    "lang": "",          # "en" or "el"; empty: the browser's language
    "cookies": "",
}


class UserError(Exception):
    """A problem the user can fix; shown on the page as-is."""


# ---------- language ----------
# The page sends its language with every request (X-Lang); messages made here follow it.

MESSAGES_EL = {
    "Choose a folder to save the songs in.": "Επιλέξτε φάκελο για να αποθηκευτούν τα τραγούδια.",
    "Tick at least one of Videos, Live streams or Shorts.":
        "Επιλέξτε τουλάχιστον ένα από τα Βίντεο, Ζωντανές μεταδόσεις ή Shorts.",
    "Paste your channel link first, e.g. https://www.youtube.com/@yourname":
        "Επικολλήστε πρώτα τον σύνδεσμο του καναλιού σας, π.χ. https://www.youtube.com/@yourname",
    "Couldn't read that link. {problem}": "Δεν ήταν δυνατή η ανάγνωση του συνδέσμου. {problem}",
    "Couldn't read those links. {problem}": "Δεν ήταν δυνατή η ανάγνωση των συνδέσμων. {problem}",
    "No album links found. Paste YouTube Music album links like "
    "https://music.youtube.com/playlist?list=OLAK5uy_… one per line.":
        "Δεν βρέθηκαν σύνδεσμοι άλμπουμ. Επικολλήστε συνδέσμους άλμπουμ του YouTube Music, όπως "
        "https://music.youtube.com/playlist?list=OLAK5uy_…, έναν ανά γραμμή.",
    "{url}: this is a single video, not an album. Use the Links tab for it.":
        "{url}: αυτό είναι ένα μεμονωμένο βίντεο, όχι άλμπουμ. Χρησιμοποιήστε την καρτέλα «Σύνδεσμοι».",
    "No YouTube links found. Paste links like https://www.youtube.com/watch?v=… or https://youtu.be/…, one per line.":
        "Δεν βρέθηκαν σύνδεσμοι YouTube. Επικολλήστε συνδέσμους όπως https://www.youtube.com/watch?v=… "
        "ή https://youtu.be/…, έναν ανά γραμμή.",
    "Choose the folder that contains your video files.": "Επιλέξτε τον φάκελο με τα αρχεία βίντεο.",
    "Folder not found: {path}": "Ο φάκελος δεν βρέθηκε: {path}",
    "No video or audio files found in that folder. If it's a Google Takeout .zip, unzip it first.":
        "Δεν βρέθηκαν αρχεία βίντεο ή ήχου σε αυτόν τον φάκελο. Αν πρόκειται για αρχείο .zip του "
        "Google Takeout, αποσυμπιέστε το πρώτα.",
    "A download is already running.": "Μια λήψη βρίσκεται ήδη σε εξέλιξη.",
    "ffmpeg is missing. Start the app again with Start.command, Start.bat or start.sh to install it.":
        "Λείπει το ffmpeg. Ανοίξτε ξανά την εφαρμογή με το Start.command, το Start.bat ή το start.sh "
        "για να εγκατασταθεί.",
    "Select at least one item.": "Επιλέξτε τουλάχιστον ένα στοιχείο.",
    "Unknown format.": "Άγνωστη μορφή.",
    "The cover image wasn't found: {path}": "Η εικόνα εξωφύλλου δεν βρέθηκε: {path}",
    "No file picker found on this computer. Type or paste the path instead.":
        "Δεν βρέθηκε παράθυρο επιλογής σε αυτόν τον υπολογιστή. Πληκτρολογήστε ή επικολλήστε τη διαδρομή.",
    "That folder doesn't exist yet. It's created when the first song is saved.":
        "Ο φάκελος δεν υπάρχει ακόμη. Θα δημιουργηθεί μόλις αποθηκευτεί το πρώτο τραγούδι.",
    "YouTube is temporarily blocking downloads from this computer. Wait an hour and press Download again "
    "(saved songs are skipped), or pick your browser in Settings (⚙) under \"If YouTube blocks the downloads\".":
        "Το YouTube μπλοκάρει προσωρινά τις λήψεις από αυτόν τον υπολογιστή. Περιμένετε μία ώρα και πατήστε "
        "ξανά «Λήψη» (τα αποθηκευμένα τραγούδια παραλείπονται) ή επιλέξτε τον περιηγητή σας στις "
        "Ρυθμίσεις (⚙), στο «Αν το YouTube μπλοκάρει τις λήψεις».",
    "This file has no audio track.": "Αυτό το αρχείο δεν έχει ήχο.",
    "Something went wrong: {error}": "Κάτι πήγε στραβά: {error}",
    "Unexpected error: {error}": "Απρόσμενο σφάλμα: {error}",
}
LANGUAGE = threading.local()  # .lang for the request or job running on this thread


def tr(text, **values):
    """The message in the page's language (English is the key), with {placeholders} filled in."""
    if getattr(LANGUAGE, "lang", "en") == "el":
        text = MESSAGES_EL.get(text, text)
    return text.format(**values) if values else text


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
        raise UserError(tr("Choose a folder to save the songs in."))
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
        self.started = time.time()
        self.done_at = 0.0  # seconds from the start when the last song was saved or failed (for the time left)

    def set_result(self, item, status, reason=""):
        with self.lock:
            self.results[item["id"]] = status
            if status in ("ok", "failed"):
                self.done_at = time.time() - self.started
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
                "elapsed": round(time.time() - self.started, 1), "done_at": round(self.done_at, 1),
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
                 "-vf", SQUARE_CROP, "-q:v", "2", tmp],
                capture_output=True)
            if result.returncode == 0:
                os.replace(tmp, path)
            elif os.path.exists(tmp):
                os.remove(tmp)
        return [], info


def measure_audio(path, loudness=True, silence=False):
    """One pass over a file that measures its loudness and/or finds the silence at its start and end.
    Returns {"loudness": filter that brings it to -14 LUFS, the level of streaming services, in one
    even volume change (linear) so the music keeps its dynamics, "rate": its sample rate,
    "trim": (start, end or None) of the part to keep}. A key is missing when it doesn't apply
    (e.g. the file is silence, or has no silence to cut)."""
    filters = ([SILENCE] if silence else []) + ([LOUDNORM + ":print_format=json"] if loudness else [])
    result = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(path), "-vn",
                             "-af", ",".join(filters), "-f", "null", "-"], capture_output=True, **TEXT)
    found = {}
    if result.returncode:
        return found
    if loudness and (match := re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", result.stderr)):
        try:
            values = json.loads(match.group(0))
            m = {k: float(values[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
        except (KeyError, ValueError):
            m = {}
        if m and all(math.isfinite(v) for v in m.values()):
            rate = re.search(r"Audio: .*?, (\d+) Hz", result.stderr)  # loudnorm itself outputs 192 kHz
            found["loudness"] = (f"{LOUDNORM}:measured_I={m['input_i']}:measured_TP={m['input_tp']}"
                                 f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
                                 f":offset={m['target_offset']}:linear=true")
            found["rate"] = int(rate.group(1)) if rate else 48000
    if silence and (trim := part_to_keep(result.stderr)):
        found["trim"] = trim
    return found


def part_to_keep(log):
    """Reads silencedetect's report: (start, end) of the audio without the silence at its start and
    end; end is None when the audio doesn't end in silence. None when there's nothing to cut."""
    periods = []
    for kind, value in re.findall(r"silence_(start|end): (-?[\d.]+)", log):
        if kind == "start":
            periods.append([max(0.0, float(value)), None])
        elif periods:
            periods[-1][1] = float(value)
    if not periods:
        return None
    times = re.findall(r"time=(\d+):(\d+):([\d.]+)", log)  # ffmpeg's last progress line: the audio's length
    length = int(times[-1][0]) * 3600 + int(times[-1][1]) * 60 + float(times[-1][2]) if times else None
    first, last = periods[0], periods[-1]
    start = first[1] if first[0] <= 0.1 and first[1] is not None else 0.0
    ends_silent = last[1] is None or (length is not None and last[1] >= length - 0.1)
    end = last[0] if ends_silent else None
    if end is not None and end - start < 1:  # (almost) all silence: leave it alone
        return None
    if start == 0.0 and end is None:
        return None
    return round(start, 3), (round(end, 3) if end is not None else None)


def conversion_args(found):
    """ffmpeg options that apply what measure_audio found while converting: the cut, then the volume."""
    filters, rate = [], []
    if trim := found.get("trim"):
        start, end = trim
        # Even-sized blocks after the cut: FLAC can't start with the few samples left of a cut block
        filters.append(f"atrim=start={start}" + (f":end={end}" if end is not None else "")
                       + ",asetpts=PTS-STARTPTS,asetnsamples=n=4096:p=0")
    if found.get("loudness"):
        filters.append(found["loudness"])
        rate = ["-ar", str(found["rate"])]
    return ["-af", ",".join(filters), *rate] if filters else []


def shift_chapters(chapters, start, end):
    """Chapter times after the audio was cut to start…end (end None: to the end)."""
    shifted = []
    for chapter in chapters:
        first = max(chapter.get("start_time", 0), start)
        last = chapter.get("end_time", 0) if end is None else min(chapter.get("end_time", 0), end)
        if last > first:
            shifted.append({**chapter, "start_time": first - start, "end_time": last - start})
    return shifted


class PrepareAudio(PostProcessor):
    """Runs just before yt-dlp's audio conversion: measures the download (loudness, silence at the
    start and end) and hands the matching filters to the converter, so each song is encoded only once."""

    def __init__(self, job, normalize, trim):
        super().__init__()
        self.job, self.normalize, self.trim = job, normalize, trim

    def run(self, info):
        args = self._downloader.params.setdefault("postprocessor_args", {})
        args.pop(EXTRACT_AUDIO_ARGS, None)
        path = info.get("filepath")
        if path and os.path.isfile(path):
            self.job.stage = "analysing"
            found = measure_audio(path, loudness=self.normalize, silence=self.trim)
            if options := conversion_args(found):
                args[EXTRACT_AUDIO_ARGS] = options
            if found.get("trim") and info.get("chapters"):  # chapters move with the cut (for splitting)
                info["chapters"] = shift_chapters(info["chapters"], *found["trim"])
            self.job.stage = "converting"
        return [], info


class OwnCover(PostProcessor):
    """Puts the user's own image in place of the video thumbnail, before it's embedded."""

    def __init__(self, image):
        super().__init__()
        self.image = image

    def run(self, info):
        for thumb in info.get("thumbnails") or []:
            path = thumb.get("filepath")
            if not path or not os.path.exists(path):
                continue
            tmp = path + ".own.jpg"
            result = subprocess.run([FFMPEG, "-nostdin", "-y", "-loglevel", "error", "-i", self.image,
                                     "-frames:v", "1", "-q:v", "2", tmp], capture_output=True)
            if result.returncode == 0:
                os.replace(tmp, path)
        return [], info


def write_lyrics(path, text):
    """Saves text in the file's lyrics tag (shown as lyrics in Apple Music and most players).
    WAV files have no lyrics tag that players read, so they're left alone."""
    import mutagen
    from mutagen.id3 import ID3, USLT, ID3NoHeaderError
    suffix = Path(path).suffix.lower()
    if suffix == ".mp3":
        try:
            id3 = ID3(path)
        except ID3NoHeaderError:
            id3 = ID3()
        id3.delall("USLT")
        id3.add(USLT(encoding=3, lang="eng", desc="", text=text))
        id3.save(path)
    elif suffix in (".m4a", ".flac", ".opus", ".ogg"):
        audio = mutagen.File(path)
        audio["\xa9lyr" if suffix == ".m4a" else "LYRICS"] = [text]
        audio.save()


class DescriptionLyrics(PostProcessor):
    """Saves the video's description (e.g. your own lyrics or credits) as the song's lyrics."""

    def run(self, info):
        text, path = (info.get("description") or "").strip(), info.get("filepath")
        if text and path and os.path.isfile(path):
            write_lyrics(path, text)
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
        raise UserError(tr("Couldn't read that link. {problem}" if len(problems) == 1
                           else "Couldn't read those links. {problem}", problem=problems[0]))
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
            raise UserError(tr("Tick at least one of Videos, Live streams or Shorts."))
        raise UserError(tr("Paste your channel link first, e.g. https://www.youtube.com/@yourname"))
    return scan_sources(sources, settings)


def folder_name(text):
    """A folder name that works on Windows, macOS and Linux."""
    return sanitize_filename(text).strip().rstrip(". ")[:120] or "Album"


def scan_albums(body):
    """Lists the tracks of YouTube Music albums (any YouTube playlist link works too)."""
    settings = save_settings(body)
    links = find_links(settings["albums"])
    if not links:
        raise UserError(tr("No album links found. Paste YouTube Music album links like "
                           "https://music.youtube.com/playlist?list=OLAK5uy_… one per line."))
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
            problems.append(tr("{url}: this is a single video, not an album. Use the Links tab for it.", url=url))
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
        raise UserError(tr("Couldn't read that link. {problem}" if len(problems) == 1
                           else "Couldn't read those links. {problem}", problem=problems[0]))
    return {"channel": "", "items": items, "problems": problems}


def scan_links(body):
    settings = save_settings(body)
    links = find_links(settings["links"])
    if not links:
        raise UserError(tr("No YouTube links found. Paste links like https://www.youtube.com/watch?v=… "
                           "or https://youtu.be/…, one per line."))
    # A channel link pasted here brings all of its videos, live streams and Shorts
    return scan_sources(expand_sources("\n".join(links), list(CHANNEL_TABS)), settings)


def run_youtube_job(job, settings):
    out = out_dir(settings)
    out.mkdir(parents=True, exist_ok=True)
    args = base_args(settings) + [
        "--no-playlist", "-f", "bestaudio/best", "-x", "--embed-metadata",
        "--download-archive", str(out / ARCHIVE_NAME), "-P", str(out),
        "--windows-filenames", "--trim-filenames", "120",
        "--retries", "10", "--fragment-retries", "10",
    ]
    fmt = settings["format"]
    if fmt in ("mp3_320", "mp3_192"):
        args += ["--audio-format", "mp3", "--audio-quality", "320K" if fmt == "mp3_320" else "192K"]
    elif fmt in ("flac", "wav"):
        args += ["--audio-format", fmt]
    # "original": -x alone keeps YouTube's own audio stream (Opus or AAC) without re-encoding
    if fmt != "wav":  # WAV files can't hold a cover picture
        args += ["--embed-thumbnail", "--convert-thumbnails", "jpg"]

    def progress(d):
        if job.cancel:
            raise DownloadCancelled("Stopped")
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            job.stage = "downloading"
            if total:
                job.percent = min(99.0, d.get("downloaded_bytes", 0) * 100 / total)
        elif d["status"] == "finished":
            job.stage, job.percent = "converting", 100.0

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
        if settings["cover"]:
            ydl.add_post_processor(OwnCover(settings["cover"]), when="before_dl")  # after the jpg conversion
        if settings["square_cover"] and FFMPEG:
            ydl.add_post_processor(SquareCover(), when="before_dl")
        if (settings["normalize"] or settings["trim_silence"]) and fmt != "original" and FFMPEG:  # "original" isn't re-encoded
            prepare = PrepareAudio(job, settings["normalize"], settings["trim_silence"])
            prepare.set_downloader(ydl)
            ydl._pps["post_process"].insert(0, prepare)  # must run before yt-dlp's own audio conversion
        if settings["lyrics"]:
            ydl.add_post_processor(DescriptionLyrics(), when="post_process")  # after tags and cover
        if splitter:
            ydl.add_post_processor(splitter, when="post_process")  # after the tags and cover are in
        downloaded_any = False
        for job.index, item in enumerate(job.items, 1):
            if job.cancel:
                break
            job.current, job.stage, job.percent = display_name(item), "starting", 0.0
            if item["id"] in archive:
                job.set_result(item, "skipped")
                continue
            if downloaded_any:
                job.stage = "pausing"
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
                    job.message = tr("YouTube is temporarily blocking downloads from this computer. "
                                     "Wait an hour and press Download again (saved songs are skipped), or "
                                     "pick your browser in Settings (⚙) under \"If YouTube blocks the downloads\".")
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
        raise UserError(tr("Choose the folder that contains your video files."))
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise UserError(tr("Folder not found: {path}", path=root))
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
        raise UserError(tr("No video or audio files found in that folder. "
                           "If it's a Google Takeout .zip, unzip it first."))
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

    cover = settings["cover"]
    crop = ["-vf", SQUARE_CROP] if settings["square_cover"] else []

    for job.index, item in enumerate(job.items, 1):
        if job.cancel:
            break
        src = Path(item["id"])
        job.current, job.stage, job.percent = display_name(item), "converting", 0.0
        stem = folder_name(item["name"]) if (item.get("name") or "").strip() else src.stem
        fmt = settings["format"]
        codec = audio_codec(src) if fmt in ("original", "wav") else None
        if fmt == "original":
            if codec and codec.startswith("pcm_"):
                ext, codec_args = "wav", ["-c:a", "copy"]
            elif codec in COPY_EXT:
                ext, codec_args = COPY_EXT[codec], ["-c:a", "copy"]
            else:  # unusual codec: keep it lossless
                ext, codec_args = "flac", ["-c:a", "flac"]
        elif fmt == "wav":  # an original that is already PCM keeps its bit depth; anything else: CD quality
            ext, codec_args = "wav", ["-c:a", codec if codec and codec.startswith("pcm_") else "pcm_s16le"]
        elif fmt == "flac":
            ext, codec_args = "flac", ["-c:a", "flac"]
        else:
            ext, codec_args = "mp3", ["-c:a", "libmp3lame", "-b:a", "320k" if fmt == "mp3_320" else "192k"]
        dest = out / f"{stem}.{ext}"
        if dest.exists() and not item.get("force"):
            job.set_result(item, "skipped")
            continue
        job.set_result(item, "working")
        filters = []
        if (settings["normalize"] or settings["trim_silence"]) and "copy" not in codec_args:  # copying can't change the audio
            job.stage = "analysing"
            filters = conversion_args(measure_audio(src, loudness=settings["normalize"], silence=settings["trim_silence"]))
            job.stage = "converting"
        picture_in, picture = [], []
        if cover and ext in COVER_EXTS:
            picture_in = ["-i", cover]
            picture = ["-map", "1:v:0", "-c:v", "mjpeg", *crop, "-disposition:v:0", "attached_pic"]
        tmp = out / f"{stem}.part.{ext}"  # renamed when complete, so a stopped run leaves no half file
        cmd = [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), *picture_in,
               "-map", "0:a:0", *picture, "-map_metadata", "-1", *codec_args, *filters,  # no video-container tags
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
            reason = tr("This file has no audio track.") if "matches no streams" in err else (err.splitlines() or ["ffmpeg failed"])[-1]
            job.add_log(f"ERROR: {src.name}: {reason}")
            job.set_result(item, "failed", reason)


# ---------- API ----------

def start_job(body):
    current = JOB["current"]
    if current and not current.finished:
        raise UserError(tr("A download is already running."))
    if not FFMPEG:
        raise UserError(tr("ffmpeg is missing. Start the app again with Start.command, Start.bat or start.sh to install it."))
    mode = body.get("mode")
    items = [i for i in body.get("items") or [] if isinstance(i, dict) and i.get("id")]
    if mode not in ("youtube", "links", "albums", "folder") or not items:
        raise UserError(tr("Select at least one item."))
    settings = save_settings(body.get("settings") or {})
    if settings["format"] not in FORMATS:
        raise UserError(tr("Unknown format."))
    out_dir(settings)
    cover = (settings.get("cover") or "").strip()
    if cover:
        if not Path(cover).expanduser().is_file():
            raise UserError(tr("The cover image wasn't found: {path}", path=cover))
        settings["cover"] = str(Path(cover).expanduser())
    job = Job(mode, items)
    JOB["current"] = job
    runner = run_folder_job if mode == "folder" else run_youtube_job
    lang = getattr(LANGUAGE, "lang", "en")

    def work():
        LANGUAGE.lang = lang  # messages made during the job follow the page that started it
        try:
            runner(job, settings)
        except Exception as err:
            traceback.print_exc()
            job.message = tr("Something went wrong: {error}", error=clean_error(err))
        finally:
            job.stage, job.finished = "", True

    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


def update_settings(body):
    """Saves settings changed on the page right away (e.g. the language)."""
    save_settings(body)
    return {"ok": True}


def cancel_job(_body):
    if job := JOB["current"]:
        job.cancel = True
    return {"ok": True}


def applescript_string(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def choose_folder(body):
    """Shows the system's own folder picker, or with kind "image" its picker for a picture file."""
    image = body.get("kind") == "image"
    prompt = str(body.get("prompt") or "Choose")
    start = Path(str(body.get("start") or "")).expanduser()
    start = start.parent if image and start.is_file() else start
    start = str(start) if start.is_dir() else ""
    env = None
    if sys.platform == "darwin":
        what = 'file of type {"public.image"}' if image else "folder"
        script = f"POSIX path of (choose {what} with prompt {applescript_string(prompt)}"
        if start:
            script += f" default location (POSIX file {applescript_string(start)} as alias)"
        cmd = ["osascript", "-e", "activate", "-e", script + ")"]
    elif os.name == "nt":
        # Prompt and start folder go through the environment, so no quoting is needed
        dialog = ("$d = New-Object System.Windows.Forms.OpenFileDialog; $d.Title = $env:YTMP3_PROMPT;"
                  "$d.Filter = 'Images|*.jpg;*.jpeg;*.png;*.webp;*.bmp;*.gif'; $d.InitialDirectory = $env:YTMP3_START;"
                  if image else
                  "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
                  "$d.Description = $env:YTMP3_PROMPT; $d.SelectedPath = $env:YTMP3_START;")
        script = ("Add-Type -AssemblyName System.Windows.Forms;" + dialog +
                  "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true};"
                  "[Console]::OutputEncoding = [Text.Encoding]::UTF8;"
                  "if ($d.ShowDialog($owner) -eq 'OK') { if ($d.FileName) { $d.FileName } else { $d.SelectedPath } }")
        cmd = ["powershell", "-NoProfile", "-STA", "-Command", script]
        env = dict(os.environ, YTMP3_PROMPT=prompt, YTMP3_START=start)
    elif shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", f"--title={prompt}"]
        cmd += ["--file-filter=Images | *.jpg *.jpeg *.png *.webp *.bmp *.gif"] if image else ["--directory"]
        if start:
            cmd.append(f"--filename={start}/")
    elif shutil.which("kdialog"):
        cmd = (["kdialog", "--getopenfilename", start or str(Path.home()), "*.jpg *.jpeg *.png *.webp *.bmp *.gif"]
               if image else ["kdialog", "--getexistingdirectory", start or str(Path.home())])
        cmd += ["--title", prompt]
    else:
        raise UserError(tr("No file picker found on this computer. Type or paste the path instead."))
    result = subprocess.run(cmd, capture_output=True, env=env, **TEXT)
    path = result.stdout.strip()  # empty when the user pressed Cancel
    return {"path": (path.rstrip("/\\") or path) or None}


def open_folder(body):
    path = Path(str(body.get("path") or "")).expanduser()
    if not path.is_dir():
        raise UserError(tr("That folder doesn't exist yet. It's created when the first song is saved."))
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
    "/api/settings": update_settings,
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
        LANGUAGE.lang = "el" if self.headers.get("X-Lang") == "el" else "en"
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
            self.send_body(500, {"error": tr("Unexpected error: {error}", error=clean_error(err))})


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

    def loudness(path):
        found = measure_audio(path) if path.is_file() else {}
        level = re.search(r"measured_I=(-?[\d.]+)", found.get("loudness", ""))
        return float(level.group(1)) if level else None

    def duration(path):
        if not path.is_file():
            return None
        log = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(path), "-f", "null", "-"],
                             capture_output=True, **TEXT).stderr
        times = re.findall(r"time=(\d+):(\d+):([\d.]+)", log)
        return round(int(times[-1][0]) * 3600 + int(times[-1][1]) * 60 + float(times[-1][2]), 2) if times else None

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

        # Lossless formats; a quiet song evened out to -14 LUFS with the user's own (square) cover
        for fmt in ("flac", "wav"):
            job = Job("folder", [{"id": str(video), "title": video.stem}])
            run_folder_job(job, dict(DEFAULT_SETTINGS, out=str(tmp / fmt), format=fmt))
            check(job.results.get(str(video)) == "ok" and (tmp / fmt / f"{video.stem}.{fmt}").is_file(),
                  f"video file converted to .{fmt}")
        quiet = tmp / "Quiet song.m4a"
        subprocess.run([*lavfi, "sine=frequency=440:duration=6", "-af", "volume=-30dB", "-c:a", "aac", str(quiet)],
                       check=True)
        job = Job("folder", [{"id": str(quiet), "title": quiet.stem}])
        run_folder_job(job, dict(DEFAULT_SETTINGS, out=str(tmp / "even"), normalize=True, cover=str(thumb)))
        even = tmp / "even" / "Quiet song.mp3"
        check(abs((loudness(even) or 0) + 14) < 1.5, f"volume evened out to -14 LUFS: {loudness(even)}")
        probe = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", str(even)], capture_output=True, **TEXT).stderr
        check(re.search(r"Video: mjpeg.*, 720x720", probe) is not None, "own cover embedded, square")

        # 2 s of silence, 2 s of music, 3 s of silence: only the music is kept, also when evened out
        gap = tmp / "Silent ends.m4a"
        subprocess.run([*lavfi, "sine=frequency=440:duration=7", "-af", "volume='between(t,2,4)':eval=frame",
                        "-c:a", "aac", str(gap)], check=True)
        for fmt, normalize in (("flac", False), ("mp3_320", True)):
            ext = "mp3" if fmt == "mp3_320" else fmt
            job = Job("folder", [{"id": str(gap), "title": gap.stem}])
            run_folder_job(job, dict(DEFAULT_SETTINGS, out=str(tmp / f"trim-{ext}"), format=fmt,
                                     trim_silence=True, normalize=normalize))
            song = tmp / f"trim-{ext}" / f"{gap.stem}.{ext}"
            length = duration(song)
            check(length is not None and abs(length - 2) < 0.15, f"silence trimmed in .{ext}: {length} s left of 7 s")
            if normalize:
                check(abs((loudness(song) or 0) + 14) < 1.5, f"trimmed and evened out: {loudness(song)} LUFS")
        job = Job("folder", [{"id": str(quiet), "title": quiet.stem}])  # no silence: nothing is cut
        run_folder_job(job, dict(DEFAULT_SETTINGS, out=str(tmp / "no-trim"), format="flac", trim_silence=True))
        length = duration(tmp / "no-trim" / "Quiet song.flac")
        check(length is not None and abs(length - 6) < 0.1, f"song without silence keeps its length: {length} s")
        moved = shift_chapters([{"start_time": 0, "end_time": 3}, {"start_time": 3, "end_time": 9}], 2, 8)
        check(moved == [{"start_time": 0, "end_time": 1}, {"start_time": 1, "end_time": 6}], f"chapters follow the cut: {moved}")
        for suffix in (".mp3", ".flac", ".m4a"):
            song = tmp / f"lyrics{suffix}"
            subprocess.run([*lavfi, "sine=duration=1", str(song)], check=True)
            write_lyrics(song, "Line one\nLine two")
            import mutagen
            audio = mutagen.File(song)
            text = (str(audio.tags.getall("USLT")[0]) if suffix == ".mp3"
                    else (audio.tags.get("\xa9lyr" if suffix == ".m4a" else "LYRICS") or [""])[0])
            check(text == "Line one\nLine two", f"lyrics saved in {suffix}")

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
