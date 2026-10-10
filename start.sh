#!/bin/bash
# Starts YouTube → MP3 from the source code on macOS or Linux (Start.command calls this on macOS).
# Needs Python 3.10+; everything else is installed on the first start.
# The Python environment lives in your home folder, outside any synced folder like OneDrive.

cd "$(dirname "$0")" || exit 1
VENV="$HOME/.youtube_to_mp3_deps/venv"

pause_and_exit() {
  echo "$1"
  read -r -p "Press Enter to close."
  exit 1
}

if ! "$VENV/bin/python" -c "" 2>/dev/null; then
  echo "First start: setting things up (about a minute)…"
  PYTHON=""
  for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$candidate" >/dev/null && "$candidate" -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>/dev/null; then
      PYTHON="$candidate"; break
    fi
  done
  [ -n "$PYTHON" ] || pause_and_exit "Python 3.10 or newer is needed: https://www.python.org/downloads/ (or download the ready-made app from the GitHub releases)."
  rm -rf "$VENV"
  "$PYTHON" -m venv "$VENV" || pause_and_exit "Couldn't create the Python environment. On Ubuntu/Debian run: sudo apt install python3-venv"
fi

if ! "$VENV/bin/python" -c "import yt_dlp, imageio_ffmpeg, deno" 2>/dev/null; then
  echo "Installing what the app needs…"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip -r requirements.txt ||
    pause_and_exit "Setup failed. Check your internet connection and try again."
else
  # YouTube changes often; a current yt-dlp is what keeps downloads working
  echo "Checking for yt-dlp updates…"
  "$VENV/bin/python" -m pip install --quiet --upgrade "yt-dlp[default]" 2>/dev/null ||
    echo "(Couldn't update yt-dlp; using the installed version.)"
fi

exec "$VENV/bin/python" app.py "$@"
