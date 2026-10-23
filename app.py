"""TTS Studio: turn a script into narration on your own computer.

    python app.py              ->  http://127.0.0.1:7870
    python app.py --preview    ->  the page with no voice model loaded (Create audio plays a test tone)
    python app.py --port 7871

Kokoro is built in and runs on any computer. Chatterbox is optional: the user installs it themselves, then points
the studio at that install, and the studio runs it through chatterbox_worker.py in that install's own Python.
Only one model is held in memory at a time. Settings live next to this file: config.json (paths), preset.json
(voice settings per engine), overrides.json (pronunciations). Saved takes go to exports/.
Standard library only, plus kokoro-onnx and soundfile for real Kokoro audio.
"""
import io, json, math, os, re, string, struct, subprocess, sys, tempfile, threading, time, wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import urlopen

import text_rules as tr

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG, PRESET = os.path.join(HERE, "config.json"), os.path.join(HERE, "preset.json")
WORKER, LOG = os.path.join(HERE, "chatterbox_worker.py"), os.path.join(HERE, "chatterbox.log")
DEFAULT_CONFIG = {"model_dir": os.path.join(HERE, "models"), "exports_dir": os.path.join(HERE, "exports"),
                  "overrides": os.path.join(HERE, "overrides.json"), "voices_dir": os.path.join(HERE, "voices"),
                  "chatterbox_python": "", "chatterbox_device": "auto", "chatterbox_check": None}
KOKORO_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
BUILTIN = "(built-in voice)"
STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".woff2": "font/woff2"}
DEFAULTS = {
    "kokoro": {"voices": [["af_heart", 0.5], ["af_nova", 0.25], ["af_sarah", 0.25]], "speed": 1.0,
               "gap_line": 0.25, "gap_head": 0.6, "fixes": True},
    "chatterbox": {"ref": BUILTIN, "exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8, "seed": 42,
                   "gap_line": 0.5, "gap_head": 1.0, "fixes": True},
}
# Shown when the voices file isn't there yet. The real list comes from voices-v1.0.bin.
PREVIEW_VOICES = ["af_bella", "af_heart", "af_kore", "af_nicole", "af_nova", "af_sarah", "am_adam", "am_michael",
                  "bf_emma", "bm_george"]
SAMPLE = """Welcome to TTS Studio.
Each line in this box is spoken as its own piece, with a pause after it.
Short lines, like headings, get a longer pause.
Try a quote: she said "we ship on Friday" and meant it.
Questions sound like questions, don't they?"""

PREVIEW = "--preview" in sys.argv
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 7870
RENDER_LOCK = threading.Lock()
RENDERS = {}  # render id -> (wav bytes, settings, engine, input text, spoken lines); last 20 kept for saving
DOWNLOAD = {"running": False, "done": 0, "total": 0, "error": ""}


# ── settings ──────────────────────────────────────────────────────────────────────────────────────────────
def config():
    try:
        return {**DEFAULT_CONFIG, **json.load(open(CONFIG, encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)


def save_config(**changes):
    c = {**config(), **changes}
    json.dump(c, open(CONFIG, "w", encoding="utf-8"), indent=2)
    return c


def presets():
    try:
        saved = json.load(open(PRESET, encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {e: {**d, **saved.get(e, {})} for e, d in DEFAULTS.items()}


# ── Kokoro (built in, runs in this process) ───────────────────────────────────────────────────────────────
class Kokoro:
    model = None

    @staticmethod
    def missing():
        d = config()["model_dir"]
        return [f for f in KOKORO_FILES if not os.path.exists(os.path.join(d, f))]

    @classmethod
    def load(cls):
        if cls.model is None:
            import onnxruntime as ort
            from kokoro_onnx import Kokoro as K
            d = config()["model_dir"]
            sess = ort.InferenceSession(os.path.join(d, KOKORO_FILES[0]), providers=["CPUExecutionProvider"])
            cls.model = K.from_session(sess, os.path.join(d, KOKORO_FILES[1]))
        return cls.model

    @classmethod
    def unload(cls):
        cls.model = None

    @classmethod
    def voices(cls):
        path = os.path.join(config()["model_dir"], KOKORO_FILES[1])
        if not PREVIEW and os.path.exists(path):
            try:
                import numpy as np
                return sorted(np.load(path).keys())  # cheap: reads the names, not the model
            except Exception:
                pass
        return PREVIEW_VOICES

    @classmethod
    def synth(cls, p, spoken, gaps):
        import numpy as np, soundfile as sf
        k = cls.load()
        parts = [(v, float(w)) for v, w in p["voices"] if v and float(w) > 0]
        if not parts:
            raise ValueError("Give at least one voice a share above zero.")
        weights = np.array([w for _, w in parts]); weights /= weights.sum()
        style = sum(k.get_voice_style(v) * w for (v, _), w in zip(parts, weights))
        out, sr, starts, pos = [], 24000, [], 0
        for said, gap in zip(spoken, gaps):
            a, sr = k.create(said, voice=style, speed=float(p["speed"]), lang="en-us")
            out += [a, np.zeros(int(sr * gap), dtype=a.dtype)]
            starts.append(pos); pos += len(a) + int(sr * gap)
        buf = io.BytesIO()
        sf.write(buf, np.concatenate(out), sr, format="WAV")
        return buf.getvalue(), [s / sr for s in starts]


def download_kokoro():
    """Fetch the two model files into model_dir, showing progress in /api/state. .part files until complete."""
    d = config()["model_dir"]
    os.makedirs(d, exist_ok=True)
    DOWNLOAD.update(running=True, done=0, total=0, error="")
    try:
        for f in Kokoro.missing():
            with urlopen(KOKORO_URL + f) as r, open(os.path.join(d, f + ".part"), "wb") as out:
                DOWNLOAD["total"] += int(r.headers.get("Content-Length", 0))
                while chunk := r.read(1 << 20):
                    out.write(chunk); DOWNLOAD["done"] += len(chunk)
            os.replace(os.path.join(d, f + ".part"), os.path.join(d, f))
    except Exception as e:
        DOWNLOAD["error"] = f"The download stopped: {e}. Check the internet connection and try again."
    DOWNLOAD["running"] = False


# ── Chatterbox (optional, the user's own install, in its own process) ────────────────────────────────────
CHECK_SCRIPT = r"""
import json, sys, importlib.metadata as md
out = {"python_version": sys.version.split()[0]}
try:
    out["chatterbox"] = md.version("chatterbox-tts")
except Exception:
    out["chatterbox"] = None
try:
    import torch
    out["torch"] = torch.__version__
    out["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else (
        "Apple GPU" if torch.backends.mps.is_available() else None)
except Exception as e:
    out["torch_error"] = str(e)
try:
    import perth
    out["watermark"] = perth.PerthImplicitWatermarker is not None
except Exception:
    out["watermark"] = False
print(json.dumps(out))
"""


def find_python(path):
    """Accept the python file itself, an environment folder, or a folder that contains one."""
    path = os.path.expanduser(path.strip().strip('"'))
    if os.path.isfile(path):
        return path
    for sub in ("", "venv", ".venv", "env"):
        for exe in (("Scripts", "python.exe"), ("bin", "python"), ("python.exe",), ("bin", "python3")):
            cand = os.path.join(path, sub, *exe)
            if os.path.isfile(cand):
                return cand
    return None


def check_chatterbox(path):
    py = find_python(path)
    if not py:
        return {"ok": False, "error": "There's no Python environment in that folder. Pick the folder you installed "
                                      "Chatterbox into (it contains a Scripts or bin folder)."}
    try:
        r = subprocess.run([py, "-c", CHECK_SCRIPT], capture_output=True, text=True, timeout=180)
        info = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "python": py, "error": "That Python didn't run the check. It may be broken or not a "
                                                    "Python install."}
    if not info.get("chatterbox"):
        return {"ok": False, "python": py, **info,
                "error": "Python works there, but Chatterbox isn't installed in it. Follow Chatterbox's install "
                         "steps inside this environment, then check again."}
    return {"ok": True, "python": py, **info}


class Chatterbox:
    proc, watermark, device, lock = None, None, None, threading.Lock()

    @classmethod
    def start(cls):
        c = config()
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "TTS_STUDIO_DEVICE": c["chatterbox_device"]}
        cls.proc = subprocess.Popen([c["chatterbox_python"], WORKER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=open(LOG, "ab"), text=True, encoding="utf-8", cwd=HERE, env=env)
        ready = cls._read()
        if not ready.get("ready"):
            cls.stop()
            raise RuntimeError(f"Chatterbox couldn't start: {ready.get('error', 'no reason given')}. "
                               f"Details are in chatterbox.log next to the studio.")
        cls.watermark, cls.device = ready["watermark"], ready["device"]

    @classmethod
    def _read(cls):
        line = cls.proc.stdout.readline()
        if not line:
            cls.stop()
            raise RuntimeError("Chatterbox stopped unexpectedly. Details are in chatterbox.log next to the studio.")
        return json.loads(line)

    @classmethod
    def stop(cls):
        if cls.proc:
            try:
                cls.proc.kill()
            except OSError:
                pass
        cls.proc = None

    @classmethod
    def synth(cls, p, spoken, gaps):
        with cls.lock:
            if cls.proc is None or cls.proc.poll() is not None:
                cls.start()
            ref = None if p["ref"] == BUILTIN else os.path.join(config()["voices_dir"], os.path.basename(p["ref"]))
            fd, out = tempfile.mkstemp(suffix=".wav"); os.close(fd)
            try:
                cls.proc.stdin.write(json.dumps({**{k: p[k] for k in ("exaggeration", "cfg_weight", "temperature",
                                                 "seed")}, "ref": ref, "lines": spoken, "gaps": gaps, "out": out}) + "\n")
                cls.proc.stdin.flush()
                r = cls._read()
                if not r.get("ok"):
                    raise RuntimeError(f"Chatterbox couldn't create that audio: {r.get('error')}")
                return open(out, "rb").read(), r.get("starts", [])
            finally:
                os.remove(out)


# ── rendering ─────────────────────────────────────────────────────────────────────────────────────────────
def is_head(ln):
    return len(ln) < 70 and ln.count(" ") < 9


def prepare(engine, p, text):
    """Returns (spoken pieces, pause after each, source lines of each, warning). Kokoro gets the punctuation rules;
    both get the word swaps. Source lines index text.split("\\n"), so the page can light the line being spoken."""
    items = [([i], l.strip()) for i, l in enumerate(text.split("\n")) if l.strip()]
    warning = ""
    if p["fixes"]:
        ov, warning = tr.load_overrides(config()["overrides"])
        items = [(src, out) for src, l in items for out in tr.apply_overrides([l], ov)]
        if engine == "kokoro":
            items = [(src, out) for src, l in tr.join_labels(items) for out in tr.split_long([l])]
    lines = [l for _, l in items]
    spoken = [tr.clean(l) for l in lines] if engine == "kokoro" and p["fixes"] else lines
    gaps = [float(p["gap_head"] if is_head(l) else p["gap_line"]) for l in lines]
    return spoken, gaps, [src for src, _ in items], warning


def tone_wav(spoken, gaps, sr=24000):
    """Preview stand-in for speech: one short beep per line, then that line's pause, so pacing still shows."""
    frames, starts = bytearray(), []
    for ln, gap in zip(spoken, gaps):
        starts.append(len(frames) / 2 / sr)
        n = int(sr * min(0.15 + 0.012 * len(ln), 1.2))
        for i in range(n):
            frames += struct.pack("<h", int(6000 * min(1.0, i / 400, (n - i) / 400) * math.sin(2 * math.pi * 440 * i / sr)))
        frames += b"\0\0" * int(sr * gap)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(bytes(frames))
    return buf.getvalue(), starts


def render(engine, p, text):
    """Returns (wav bytes, spoken pieces, [[start second, source lines], …] per piece, warning)."""
    spoken, gaps, srcs, warning = prepare(engine, p, text)
    if not spoken:
        raise ValueError("The script is empty. Type or paste what you want spoken.")
    if PREVIEW:
        wav, starts = tone_wav(spoken, gaps)
    else:
        with RENDER_LOCK:
            if engine == "kokoro":
                if Kokoro.missing():
                    raise ValueError("The Kokoro voice model isn't downloaded yet. Use the download button on the left.")
                Chatterbox.stop()  # one model in memory at a time
                wav, starts = Kokoro.synth(p, spoken, gaps)
            else:
                if not config()["chatterbox_python"]:
                    raise ValueError("Chatterbox isn't set up yet. Follow the set-up steps on the Chatterbox side first.")
                Kokoro.unload()
                wav, starts = Chatterbox.synth(p, spoken, gaps)
    return wav, spoken, [[round(s, 3), src] for s, src in zip(starts, srcs)], warning


# ── pronunciations (overrides.json): the page edits "words"; "lines" stays a file-only power feature ─────────
def pronunciations():
    ov, problem = tr.load_overrides(config()["overrides"])
    words = ov.get("words") if isinstance(ov.get("words"), dict) else {}
    return {"words": [[w, s] for w, s in words.items()], "lines": len(ov.get("lines") or {}), "problem": problem}


def save_pronunciations(rows):
    path = config()["overrides"]
    ov, problem = tr.load_overrides(path)
    if problem:  # never overwrite a file we couldn't read: it may hold line replacements the page can't see
        raise ValueError(problem + " Your changes weren't saved, so nothing in the file is lost.")
    words = {}
    for w, say in rows:
        w, say = str(w).strip(), str(say).strip()
        if w and say:
            words[w] = say
    ov["words"] = words
    ov.setdefault("_about", "Pronunciations for TTS Studio. 'words' = whole-word swaps (capitals matter), edited "
                            "in the studio. 'lines' = exact replacement for one whole line, edited here by hand.")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(ov, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)  # all or nothing: a crash mid-write can't leave a half-written file
    return pronunciations()


# ── folder browser for the Chatterbox picker (browsers can't hand a page a local path) ──────────────────────
def browse(path):
    if not path:
        path = os.path.expanduser("~")
    path = os.path.abspath(path)
    try:
        names = sorted(n for n in os.listdir(path) if not n.startswith(".") and os.path.isdir(os.path.join(path, n)))
    except OSError:
        return {"path": path, "error": "That folder can't be opened."}
    dirs = [{"name": n, "path": os.path.join(path, n), "env": bool(find_python(os.path.join(path, n)))}
            for n in names[:400]]
    parent = os.path.dirname(path)
    drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")] if os.name == "nt" else []
    return {"path": path, "parent": parent if parent != path else None, "dirs": dirs, "drives": drives,
            "here_env": bool(find_python(path))}


# ── web server ────────────────────────────────────────────────────────────────────────────────────────────
def state():
    c = config()
    return {"preview": PREVIEW,
            "kokoro": {"missing": Kokoro.missing(), "model_dir": c["model_dir"], "loaded": Kokoro.model is not None,
                       "download": DOWNLOAD},
            "chatterbox": {"python": c["chatterbox_python"], "check": c["chatterbox_check"],
                           "running": Chatterbox.proc is not None, "watermark": Chatterbox.watermark,
                           "device": Chatterbox.device, "voices_dir": c["voices_dir"]}}


def voice_samples():
    d = config()["voices_dir"]
    os.makedirs(d, exist_ok=True)
    return [BUILTIN] + sorted(f for f in os.listdir(d) if f.lower().endswith(".wav"))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode("utf-8"))

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj), "application/json")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path.startswith("/static/"):
            root = os.path.join(HERE, "static")
            f = os.path.normpath(os.path.join(root, u.path[len("/static/"):]))
            ctype = STATIC_TYPES.get(os.path.splitext(f)[1])
            if ctype and f.startswith(root + os.sep) and os.path.isfile(f):
                return self._send(200, open(f, "rb").read(), ctype)
            return self._send(404, "not found")
        if u.path == "/api/state":
            return self._json(state())
        if u.path == "/api/pronunciations":
            return self._json(pronunciations())
        if u.path == "/api/browse":
            return self._json(browse(parse_qs(u.query).get("path", [""])[0]))
        if u.path != "/":
            return self._send(404, "not found")
        page = open(os.path.join(HERE, "page.html"), encoding="utf-8").read()  # read per request: edits show on reload
        for k, v in {"__STATE__": state(), "__PRESETS__": presets(), "__VOICES__": Kokoro.voices(),
                     "__SAMPLES__": voice_samples(), "__TEXT__": SAMPLE}.items():
            page = page.replace(k, json.dumps(v))
        self._send(200, page, "text/html; charset=utf-8")

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        try:
            if self.path == "/api/render":
                engine = data["engine"]
                p = {**DEFAULTS[engine], **data["preset"]}
                wav, spoken, pieces, warning = render(engine, p, data["text"])
                rid = time.strftime("%Y%m%d_%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}"
                RENDERS[rid] = (wav, p, engine, data["text"], spoken)
                for old in sorted(RENDERS)[:-20]:
                    RENDERS.pop(old)
                extra = {"X-Spoken": quote("\n".join(spoken)), "X-Pieces": quote(json.dumps(pieces)), "X-Render-Id": rid}
                if warning:
                    extra["X-Warning"] = quote(warning)
                if engine == "chatterbox" and Chatterbox.watermark is not None:
                    extra["X-Watermark"] = "on" if Chatterbox.watermark else "off"
                return self._send(200, wav, "audio/wav", extra)
            if self.path == "/api/preset":
                saved = presets()
                saved[data["engine"]] = data["preset"]
                json.dump(saved, open(PRESET, "w", encoding="utf-8"), indent=2)
                return self._send(200, "Settings remembered. They'll be here next time you open the studio.")
            if self.path == "/api/save":
                rid = data.get("id", "")
                if rid not in RENDERS:
                    return self._send(400, "Create audio first, then save the take.")
                wav, p, engine, text, spoken = RENDERS[rid]
                name = re.sub(r"[^\w\-]+", "_", data.get("name", "").strip())[:60] or "untitled"
                d = os.path.join(config()["exports_dir"], f"{rid}_{name}")
                os.makedirs(d, exist_ok=True)
                open(os.path.join(d, "audio.wav"), "wb").write(wav)
                json.dump({**p, "engine": "preview tone" if PREVIEW else engine, "rendered": rid},
                          open(os.path.join(d, "settings.json"), "w", encoding="utf-8"), indent=2)
                open(os.path.join(d, "input.txt"), "w", encoding="utf-8", newline="\n").write(text)
                open(os.path.join(d, "spoken.txt"), "w", encoding="utf-8", newline="\n").write("\n".join(spoken) + "\n")
                return self._send(200, f"Saved. Your take is in {d}")
            if self.path == "/api/pronunciations":
                return self._json(save_pronunciations(data.get("words", [])))
            if self.path == "/api/kokoro/download":
                if not DOWNLOAD["running"]:
                    threading.Thread(target=download_kokoro, daemon=True).start()
                return self._json(state())
            if self.path == "/api/chatterbox/check":
                return self._json(check_chatterbox(data.get("path", "")))
            if self.path == "/api/chatterbox/use":
                r = check_chatterbox(data.get("path", ""))
                if not r["ok"]:
                    return self._json(r, 400)
                Chatterbox.stop()
                save_config(chatterbox_python=r["python"], chatterbox_check=r)
                return self._json(state())
            if self.path == "/api/chatterbox/forget":
                Chatterbox.stop()
                save_config(chatterbox_python="", chatterbox_check=None)
                return self._json(state())
        except Exception as e:  # show the reason in the page instead of a dead button
            return self._send(500, str(e) if isinstance(e, (ValueError, RuntimeError)) else f"{type(e).__name__}: {e}")
        self._send(404, "not found")


if __name__ == "__main__":
    mode = "preview mode, no voice model" if PREVIEW else "voice models load on first use"
    print(f"TTS Studio on http://127.0.0.1:{PORT}  ({mode}; Ctrl+C to stop)", flush=True)
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
    finally:
        Chatterbox.stop()
