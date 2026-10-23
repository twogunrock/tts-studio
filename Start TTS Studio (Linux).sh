#!/usr/bin/env bash
# Double-click (or run) to set up TTS Studio the first time, then start it.
# Everything goes inside this folder: uv, its own Python, the voice model. To remove TTS Studio, delete the folder.
set -u
cd "$(dirname "$0")" || exit 1
HERE="$(pwd)"
UV="$HERE/tools/uv/uv"
export UV_PYTHON_INSTALL_DIR="$HERE/tools/python" UV_CACHE_DIR="$HERE/tools/cache"
PY="$HERE/.venv/bin/python"

failed() {
  echo
  echo "  Setup didn't finish. Check your internet connection, then run this file again:"
  echo "  it picks up where it stopped. If it keeps failing, copy the messages above into an issue at"
  echo "  https://github.com/twogunrock/tts-studio/issues"
  echo
  read -r -p "  Press Enter to close. " _
  exit 1
}

if [ ! -f "$HERE/.venv/setup-done.txt" ]; then
  echo
  echo "  Setting up TTS Studio. This happens once and takes a few minutes."
  echo "  Everything is installed inside this folder. To remove TTS Studio, delete the folder."
  echo
  if [ ! -x "$UV" ]; then
    echo "  [1 of 3] Downloading the installer, uv 0.13.5 ..."
    curl -LsSf https://astral.sh/uv/0.13.5/install.sh | env UV_UNMANAGED_INSTALL="$HERE/tools/uv" sh || failed
    [ -x "$UV" ] || failed
  fi
  echo "  [2 of 3] Getting Python 3.12 for TTS Studio ..."
  "$UV" venv --python 3.12 --managed-python --quiet "$HERE/.venv" || failed
  echo "  [3 of 3] Installing the voice engine, Kokoro ..."
  "$UV" pip install --quiet --python "$PY" -r "$HERE/requirements.txt" || failed
  echo "Set up by the start script" > "$HERE/.venv/setup-done.txt"
  echo
  echo "  Setup finished."
fi

echo
"$PY" "$HERE/app.py" --open
[ $? -eq 3 ] && exit 0  # it was already running; the browser has been opened
echo
echo "  TTS Studio has stopped. You can close this window."
