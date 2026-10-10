# Changes

All notable changes to YouTube → MP3, newest first. Each version has a matching git tag and a
[GitHub release](https://github.com/IliasPa/YouTube-mp3/releases) with the ready-made apps.

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
