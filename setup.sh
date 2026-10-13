#!/usr/bin/env bash
# One-time setup for a new machine / teammate. Safe to run again.
set -e
cd "$(dirname "$0")"

echo "== Content studio setup =="
command -v python3 >/dev/null || { echo "Install Python 3.10+ first."; exit 1; }
if ! command -v ffmpeg >/dev/null; then
  echo "ffmpeg is missing. Install it:"
  echo "  Arch:    sudo pacman -S ffmpeg"
  echo "  Ubuntu:  sudo apt install ffmpeg fonts-noto-core"
  echo "  macOS:   brew install ffmpeg"
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env - open it and paste your GEMINI_API_KEY, ELEVEN_API_KEY and SARVAM_API_KEY."
fi
chmod +x studio
mkdir -p products creators runs scripts

./studio doctor || true
echo
echo "Next: ./studio --help   (or open this folder in Claude Code and just describe what you want)"
