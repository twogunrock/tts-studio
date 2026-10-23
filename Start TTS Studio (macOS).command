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
  echo "  Setup didn't finish. If TTS Studio is open in your browser, close its tab and wait two minutes first."
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

# A "TTS Studio" launcher in the app menu and on the desktop (Linux only), made once per folder: deleting it is
# respected, moving the folder makes a new one.
make_launcher() {
  local entry="$HOME/.local/share/applications/tts-studio.desktop" desk
  mkdir -p "$(dirname "$entry")"
  printf '%s\n' "[Desktop Entry]" "Type=Application" "Name=TTS Studio" "Comment=Turn a script into narration" \
    "Exec=\"$HERE/Start TTS Studio (Linux).sh\"" "Icon=$HERE/static/icon-256.png" "Terminal=false" \
    "Categories=AudioVideo;Audio;" > "$entry"
  chmod +x "$entry"
  desk="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
  if [ -d "$desk" ] && [ "$desk" != "$HOME" ]; then
    cp "$entry" "$desk/" && chmod +x "$desk/tts-studio.desktop"
    gio set "$desk/tts-studio.desktop" metadata::trusted true 2>/dev/null  # GNOME: allow launching without asking
  fi
  echo "$HERE" > "$HERE/tools/shortcut-made.txt"
  echo "  TTS Studio is now in your app menu$([ -d "$desk" ] && [ "$desk" != "$HOME" ] && echo " and on your desktop")."
}
if [ "$(uname)" = "Linux" ] && [ "$(cat "$HERE/tools/shortcut-made.txt" 2>/dev/null)" != "$HERE" ]; then
  make_launcher
fi

echo
echo "  Opening TTS Studio in your browser. You can close this window."
echo "  The studio stops on its own a minute or two after you close its browser tab."
# In the background, detached from this window; messages go to studio.log.
nohup "$PY" "$HERE/app.py" --open --quit-when-closed >> "$HERE/studio.log" 2>&1 < /dev/null &
sleep 2
