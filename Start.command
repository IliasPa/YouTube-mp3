#!/bin/bash
# Double-click to start YouTube → MP3 on macOS. Close this window to quit.
exec "$(dirname "$0")/start.sh" "$@"
