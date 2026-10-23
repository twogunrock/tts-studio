#!/usr/bin/env bash
# Double-click (or run) to set up TTS Studio (first time, or after an update), then start it.
# Everything goes inside this folder: uv, its own Python, the voice model. To remove TTS Studio, delete the folder.
set -u
cd "$(dirname "$0")" || exit 1
HERE="$(pwd)"
UV="$HERE/tools/uv/uv"
export UV_PYTHON_INSTALL_DIR="$HERE/tools/python" UV_CACHE_DIR="$HERE/tools/cache"
PY="$HERE/.venv/bin/python"
# The set-up is current when its copy of requirements.txt matches this one; a changed list means install again.
DONE="$HERE/.venv/installed-requirements.txt"

failed() {
  echo
  echo "  Setup didn't finish. If TTS Studio is open in another window, close that window first."
  echo "  Check your internet connection, then run this file again: it picks up where it stopped."
  echo "  If it keeps failing, copy the messages above into an issue at"
  echo "  https://github.com/twogunrock/tts-studio/issues"
  echo
  read -r -p "  Press Enter to close. " _
  exit 1
}

if ! cmp -s "$HERE/requirements.txt" "$DONE"; then
  echo
  echo "  Setting up TTS Studio. The first time takes a few minutes; after an update, less."
  echo "  Everything is installed inside this folder. To remove TTS Studio, delete the folder."
  echo
  if [ ! -x "$UV" ]; then
    echo "  [1 of 3] Downloading the installer, uv 0.13.5 ..."
    curl -LsSf https://astral.sh/uv/0.13.5/install.sh | env UV_UNMANAGED_INSTALL="$HERE/tools/uv" sh || failed
    [ -x "$UV" ] || failed
  fi
  if [ ! -x "$PY" ]; then
    echo "  [2 of 3] Getting Python 3.12 for TTS Studio ..."
    "$UV" venv --python 3.12 --managed-python --quiet "$HERE/.venv" || failed
  fi
  echo "  [3 of 3] Installing the voice engine, Kokoro ..."
  "$UV" pip install --quiet --python "$PY" -r "$HERE/requirements.txt" || failed
  cp "$HERE/requirements.txt" "$DONE"
  echo
  echo "  Setup finished."
fi

echo
"$PY" "$HERE/app.py" --open
[ $? -eq 3 ] && exit 0  # it was already running; the browser has been opened
echo
echo "  TTS Studio has stopped. You can close this window."
