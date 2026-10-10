"""Builds the ready-made app (Python, yt-dlp, ffmpeg and Deno included) for the system it runs on.

    python -m pip install -r requirements.txt pyinstaller
    python build.py          # -> dist/YouTube-MP3/ and dist/YouTube-MP3-<system>.zip (.tar.gz on Linux)
    python build.py --test   # runs the built app's self-test

GitHub Actions runs this on Windows, macOS and Linux for every push and attaches the
archives to the release when a version tag (vX.Y) is pushed.
"""

import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "YouTube-MP3"
EXE = ".exe" if os.name == "nt" else ""
SYSTEM = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
BUILD, DIST = ROOT / "build", ROOT / "dist"
APP_DIR = DIST / NAME
ARCHIVE = DIST / (f"{NAME}-{SYSTEM}.tar.gz" if SYSTEM == "linux" else f"{NAME}-{SYSTEM}.zip")

READ_ME = {
    "windows": f"""YouTube -> MP3

Double-click {NAME}.exe. A black window opens (keep it open while the app works)
and the app opens in your web browser. Close the black window to quit.

The first time, Windows may say "Windows protected your PC" because the app isn't
signed by a paid certificate: click "More info", then "Run anyway".
""",
    "macos": f"""YouTube -> MP3

The first time only: macOS blocks apps downloaded from the internet that aren't
signed by a paid Apple certificate. To allow this one:
  1. Open Terminal (Applications > Utilities).
  2. Type:  xattr -dr com.apple.quarantine   (with a space at the end)
  3. Drag this {NAME} folder into the Terminal window and press Enter.

Then double-click {NAME}. A Terminal window opens (keep it open while the app
works) and the app opens in your web browser. Close the window to quit.
This build is for Apple Silicon Macs (M1 or newer).
""",
    "linux": f"""YouTube -> MP3

Run ./{NAME} in this folder. The app opens in your web browser; keep the terminal
open while it works and press Ctrl+C to quit. For the "Choose..." folder buttons,
install zenity or kdialog (or type the folder path).
""",
}


def build():
    import deno
    import imageio_ffmpeg

    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.rmtree(DIST, ignore_errors=True)
    # Stage ffmpeg and Deno under fixed names; app.py looks for them in <app>/bin
    stage = BUILD / "bin"
    stage.mkdir(parents=True)
    tools = {"ffmpeg": imageio_ffmpeg.get_ffmpeg_exe(), "deno": deno.find_deno_bin()}
    for name, source in tools.items():
        shutil.copy2(source, stage / f"{name}{EXE}")
    sep = os.pathsep
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--console", "--name", NAME,
        "--add-data", f"{ROOT / 'index.html'}{sep}.",
        "--add-binary", f"{stage / ('ffmpeg' + EXE)}{sep}bin",
        "--add-binary", f"{stage / ('deno' + EXE)}{sep}bin",
        "--distpath", str(DIST), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD),
        str(ROOT / "app.py"),
    ], check=True)
    (APP_DIR / "READ ME FIRST.txt").write_text(READ_ME[SYSTEM], encoding="utf-8", newline="\r\n" if SYSTEM == "windows" else "\n")

    # Archive: ditto keeps macOS symlinks and permissions, tar keeps Linux permissions
    if SYSTEM == "macos":
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(APP_DIR), str(ARCHIVE)], check=True)
    elif SYSTEM == "linux":
        with tarfile.open(ARCHIVE, "w:gz") as tar:
            tar.add(APP_DIR, arcname=NAME)
    else:
        with zipfile.ZipFile(ARCHIVE, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(APP_DIR.rglob("*")):
                zf.write(path, Path(NAME) / path.relative_to(APP_DIR))
    print(f"Built {ARCHIVE} ({ARCHIVE.stat().st_size / 1e6:.0f} MB)")


def test(extra):
    result = subprocess.run([str(APP_DIR / f"{NAME}{EXE}"), "--self-test", *extra])
    sys.exit(result.returncode)


if __name__ == "__main__":
    if "--test" in sys.argv:
        test([a for a in sys.argv[1:] if a == "--online"])
    else:
        build()
