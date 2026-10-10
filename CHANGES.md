# Changes

All notable changes to YouTube → MP3, newest first. Each version has a matching git tag and a
[GitHub release](https://github.com/IliasPa/YouTube-mp3/releases) with the ready-made apps.

## v0.4 — 2026-10-10

### Added

- **Update notice:** the app checks GitHub when it starts and shows "Version … is available"
  when there is a newer release. YouTube changes often, and a downloaded app keeps working only
  with a recent yt-dlp. The notice can be closed until the next version.
- **Automatic yt-dlp updates for the apps:** every week GitHub checks for a new yt-dlp and, if
  there is one, publishes a patch version (e.g. v0.4.1) built with it.
- **Split videos with chapters into songs** (setting): a live stream or concert with chapters
  becomes numbered tracks in a folder named after the video, each titled by its chapter, with
  the video's title as the album.
- **Tidy titles** in the list: removes extras like "(Official Video)", "[4K]", "| Live at …",
  hashtags and your artist name in front. **Edit titles** lets you rename any song before
  downloading; **Undo title changes** brings the originals back. The file name and title tag
  follow your titles.

## v0.3 — 2026-10-10

### Added

- Setting **Use the song's own details when YouTube Music knows the song**: such songs are
  tagged with their own title, artist, album, album artist and release date instead of the
  video's title, your Artist and Album settings and the upload date. Other videos keep the
  video's details. It's off by default, because YouTube can link a cover version to the
  original song and its artist.

### Changed

- With that setting off, the title tag is always the video's title. Before, it silently became
  the song title when YouTube knew the song.

## v0.2 — 2026-10-10

### Added

- **Album links** tab: paste YouTube Music album links (`music.youtube.com/playlist?list=OLAK5uy_…`
  or `music.youtube.com/browse/MPREb_…`), one per line. Every album is listed with its tracks
  in order.
- Each album is saved in its own folder, `Artist - Album/01 - Track.mp3`, with album title,
  album artist, track number (e.g. 3/12), the release date and the album cover.
- Other YouTube playlists work as albums too.

### Changed

- In the Album links tab, artist, album and year come from each album, so those settings are
  hidden there.

## v0.1 — 2026-10-10

### Added

- **Paste links** tab: paste YouTube links (videos, Shorts, playlists, YouTube Music) one per
  line, or a whole message or list; the links are picked out of the text and counted as you
  paste. Repeats are removed, and a video link that also names a playlist means just that video.
- Unlisted videos can be downloaded from the Paste links tab, as long as you have their link.
- Videos from a pasted playlist are marked with the playlist's name in the list.

### Changed

- The channel tab now takes just your channel link; lists of links go in the new tab.
- Links are read four at a time, so long lists are found faster.
- The artist name is only filled in for you when all videos come from the same channel.

## v0.0 — 2026-10-10

First release.

### Download from YouTube

- Paste a channel link to list all its public videos, live streams and Shorts (each can be
  included or left out). Playlist and single-video links work too.
- Pick songs with checkboxes, filter by title, or select only the ones not saved yet.
- Save as MP3 320 kbps, MP3 192 kbps, or the original audio without re-encoding.
- Every file gets title, upload date, artist, album artist, album and the video thumbnail as
  cover art, cropped to a square.
- File names can start with the upload date. Two videos with the same title don't overwrite
  each other.
- Saved songs are remembered and skipped, so a long run can be stopped and continued later.
  Ticking a saved song again downloads it again only if its file is gone.
- Pauses a few seconds between videos. If YouTube starts blocking, the app stops and explains
  what to do; it can optionally use the YouTube login from your browser.

### Convert video files (e.g. Google Takeout)

- Convert a folder of video files, such as an unzipped Google Takeout export with your private
  and unlisted videos, to MP3 or to the original audio (M4A, WAV, FLAC, …), with artist and
  album tags.

### Apps and platforms

- Ready-made apps for Windows, macOS (Apple Silicon) and Linux with Python, yt-dlp, ffmpeg and
  Deno included: nothing else to install.
- Run from the source code with `Start.bat` (Windows), `Start.command` (macOS) or `start.sh`
  (Linux). They set everything up on the first start and update yt-dlp on every start.
- The app runs on your own computer and opens in your browser; other websites can't control it.
- `--self-test` checks the tools and the conversion; GitHub runs it on all three systems for
  every change.
- Project website on GitHub Pages with the downloads and this list of changes.
