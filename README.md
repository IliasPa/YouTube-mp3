# YouTube → MP3

Saves the audio of your YouTube uploads as music files (MP3 320 kbps by default), with title,
upload date, artist, album and cover art filled in. Runs on Windows, macOS and Linux.

**Website and downloads: <https://iliaspa.github.io/YouTube-mp3/>** · [Releases](https://github.com/IliasPa/YouTube-mp3/releases) · [Changes](CHANGES.md)

Made for downloading your own uploads. Only download videos you have the rights to.

## Get the app

Download the ready-made app for your system from the
[latest release](https://github.com/IliasPa/YouTube-mp3/releases/latest). Python, yt-dlp, ffmpeg
and Deno are included, so there is nothing else to install.

| System | Download | Start it |
| --- | --- | --- |
| Windows | `YouTube-MP3-windows.zip` | Unzip, double-click `YouTube-MP3.exe`. If Windows says "Windows protected your PC", click **More info** → **Run anyway**. |
| macOS (Apple Silicon) | `YouTube-MP3-macos.zip` | Unzip. The first time, open Terminal, type `xattr -dr com.apple.quarantine ` and drag the `YouTube-MP3` folder in, press Enter. Then double-click `YouTube-MP3`. |
| Linux (x86-64) | `YouTube-MP3-linux.tar.gz` | Extract, run `./YouTube-MP3`. Install `zenity` for the folder picker. |

A small window stays open while the app works (close it to quit) and the app opens in your browser.

## Use it

1. Paste your channel link, e.g. `https://www.youtube.com/@yourname`, and press **Find videos**.
   Or open **Paste links** and paste the links of the videos or playlists you want, one per line
   (a whole message works too). Unlisted videos work there, as long as you have their link.
   For whole albums, open **Album links** and paste YouTube Music album links: each album gets
   its own folder (`Artist - Album/01 - Track.mp3`) with track numbers, album tags and cover.
2. Check **Artist**, **Album** and **Save to** in Settings. Tick **Use the song's own details**
   to tag songs that YouTube Music knows with their real title, artist, album and release date
   (it's off by default because YouTube can link a cover version to the original song).
3. Press **Download**. A big channel takes hours. You can press **Stop** (or close the app) and
   continue another day: songs that are already saved are skipped.

### Private/unlisted videos, or the best quality: Google Takeout

The YouTube tab only sees public videos, and YouTube serves compressed audio. Google Takeout gives you
your own uploaded files, private and unlisted ones included.

1. Go to <https://takeout.google.com>, press **Deselect all**, tick **YouTube and YouTube Music**,
   and under **All YouTube data included** keep only **videos**. Create the export.
2. When Google emails you, download the .zip file(s) and unzip them.
3. In the app, open the **From video files on this computer** tab, choose the unzipped folder,
   press **Find files**, then **Convert**.

### Where things are

- Songs: the `Music/YouTube MP3` folder in your home folder unless you change it. A hidden file
  `.downloaded.txt` in that folder remembers which videos are saved. Delete it only if you want
  everything downloaded again.
- Settings, and the Python environment when you run from the source code: `.youtube_to_mp3_deps`
  in your home folder. Deleting it is safe.

### If something goes wrong

- **Many songs fail with "Sign in to confirm you're not a bot"**: YouTube is limiting your computer.
  Wait an hour and press Download again. Or, under Settings → *If YouTube blocks the downloads*,
  choose the browser where you are signed in to YouTube.
- **Downloads stopped working after a while**: YouTube changes often. Get the newest release, or,
  when running from the source code, just start it again: it updates yt-dlp on every start.
- Open **Details** at the bottom of the page to see the full log, or run the app with
  `--self-test` to check its tools.

## Run from the source code

Needs [Python](https://www.python.org/downloads/) 3.10 or newer (on Windows, tick
"Add python.exe to PATH" during setup). Download or clone this repository, then double-click:

- Windows: `Start.bat`
- macOS: `Start.command` (the first time: right-click → Open)
- Linux: `start.sh`

The first start creates a Python environment in `.youtube_to_mp3_deps` in your home folder and
installs everything from `requirements.txt`; later starts update yt-dlp.

## For developers

- `app.py` is the local web server and the download/convert logic; `index.html` is the page.
- `python app.py --self-test [--online]` checks ffmpeg, the JavaScript runtime and the conversion
  (`--online` also downloads a short public video).
- `python build.py` builds the ready-made app for the current system with PyInstaller
  (`pip install -r requirements.txt pyinstaller` first); `python build.py --test` self-tests it.
- GitHub Actions ([build.yml](.github/workflows/build.yml)) builds and tests on Windows, macOS
  and Linux for every push. The website in `docs/` is published by
  [pages.yml](.github/workflows/pages.yml).

**Releasing a new version:** set `APP_VERSION` in `app.py`, add a section to the top of
[CHANGES.md](CHANGES.md), commit, then tag and push, e.g. `git tag v0.1 && git push origin main v0.1`.
GitHub builds the apps and publishes the release with that section as its notes.

## Third-party software

The ready-made apps include [Python](https://www.python.org) (PSF License),
[yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense) with yt-dlp-ejs, [Deno](https://deno.com) (MIT),
and an [ffmpeg](https://ffmpeg.org) build from [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg)
(GPL; source code at <https://ffmpeg.org/download.html>), plus their Python dependencies.
Not affiliated with YouTube or Google.
