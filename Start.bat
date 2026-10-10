@echo off
rem Starts YouTube -> MP3 from the source code on Windows. Close this window to quit.
rem Needs Python 3.10+; everything else is installed on the first start.
rem (The ready-made app from the GitHub releases needs nothing installed.)
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "VENV=%USERPROFILE%\.youtube_to_mp3_deps\venv"
set "VPY=%VENV%\Scripts\python.exe"

"%VPY%" -c "" >nul 2>&1
if not errorlevel 1 goto deps

echo First start: setting things up (about a minute)...
set "PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=python"
if not defined PY goto nopython
if exist "%VENV%" rmdir /s /q "%VENV%"
%PY% -m venv "%VENV%" || goto failed

:deps
"%VPY%" -c "import yt_dlp, imageio_ffmpeg, deno" >nul 2>&1
if errorlevel 1 goto install
rem YouTube changes often; a current yt-dlp is what keeps downloads working
echo Checking for yt-dlp updates...
"%VPY%" -m pip install --quiet --upgrade "yt-dlp[default]" >nul 2>&1 || echo Couldn't update yt-dlp; using the installed version.
goto run

:install
echo Installing what the app needs...
"%VPY%" -m pip install --quiet --upgrade pip -r requirements.txt || goto failed

:run
"%VPY%" app.py %*
set "RC=%errorlevel%"
if not "%RC%"=="0" pause
exit /b %RC%

:nopython
echo Python 3.10 or newer is needed. Get it from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup. Then start this again.
echo Or download the ready-made app from the GitHub releases instead.
pause
exit /b 1

:failed
echo Setup failed. Check your internet connection and try again.
pause
exit /b 1
