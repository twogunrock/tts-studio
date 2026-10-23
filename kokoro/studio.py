"""Kokoro Studio: tune a Kokoro-82M narration voice by ear, in your browser.

    python studio.py              ->  http://127.0.0.1:7860
    python studio.py --preview    ->  the page with no model loaded (Render plays a test tone)
    python studio.py --port 7870

Stdlib web server. The model loads on the first Render, not at startup. config.json (next to this file,
created on first run) says where the model files live. "Save preset" writes preset.json; "Export last render"
writes exports/<time>_<name>/ = audio.wav + settings.json + input.txt + spoken.txt.
"""
import io, json, math, os, re, struct, sys, threading, time, wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

import text_rules as tr

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
PRESET = os.path.join(HERE, "preset.json")
DEFAULT_CONFIG = {"model_dir": os.path.join(HERE, "models"), "exports_dir": os.path.join(HERE, "exports"),
                  "overrides": os.path.join(HERE, "overrides.json")}
MODEL_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
DEFAULT = {"voices": [["af_heart", 0.5], ["af_nova", 0.25], ["af_sarah", 0.25]], "speed": 1.0,
           "gap_line": 0.25, "gap_head": 0.6, "clean": True}
# Shown in preview mode, when the voices file isn't loaded. The real list comes from voices-v1.0.bin.
PREVIEW_VOICES = ["af_bella", "af_heart", "af_kore", "af_nicole", "af_nova", "af_sarah", "am_adam", "am_michael",
                  "bf_emma", "bm_george"]
SAMPLE = """Welcome to Kokoro Studio.
Each line in this box is spoken as its own piece, with a pause after it.
Short lines, like headings, get a longer pause.
Try a quote: she said "we ship on Friday" and meant it.
Dashes – like this one – become short pauses."""

PREVIEW = "--preview" in sys.argv
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 7860
K, VOICES, LOCK = None, None, threading.Lock()
RENDERS = {}  # render id -> (wav bytes, preset, input text, spoken lines); last 20 kept for Export


def config():
    try:
        return {**DEFAULT_CONFIG, **json.load(open(CONFIG, encoding="utf-8"))}
    except (OSError, ValueError):
        json.dump(DEFAULT_CONFIG, open(CONFIG, "w", encoding="utf-8"), indent=2)
        return dict(DEFAULT_CONFIG)


def model_status():
    d = config()["model_dir"]
    missing = [f for f in MODEL_FILES if not os.path.exists(os.path.join(d, f))]
    return {"preview": PREVIEW, "model_dir": d, "missing": missing, "loaded": K is not None}


def preset():
    try:
        return {**DEFAULT, **json.load(open(PRESET, encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULT)


def load():
    """First Render loads the model. Imports live here so preview mode needs nothing but Python."""
    global K, VOICES
    with LOCK:
        if K is None:
            import onnxruntime as ort
            from kokoro_onnx import Kokoro
            d = config()["model_dir"]
            sess = ort.InferenceSession(os.path.join(d, MODEL_FILES[0]), providers=["CPUExecutionProvider"])
            K = Kokoro.from_session(sess, os.path.join(d, MODEL_FILES[1]))
            VOICES = sorted(K.get_voices())
    return K


def voice_names():
    """Real names from the voices file when it's there (cheap, no model load); the preview list otherwise."""
    if VOICES:
        return VOICES
    path = os.path.join(config()["model_dir"], MODEL_FILES[1])
    if not PREVIEW and os.path.exists(path):
        try:
            import numpy as np
            return sorted(np.load(path).keys())
        except Exception:
            pass
    return PREVIEW_VOICES


def spoken_lines(p, text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not p["clean"]:  # fixes off = read exactly what's in the box
        return lines, lines
    lines = tr.split_long(tr.join_labels(tr.apply_overrides(lines, config()["overrides"])))
    return lines, [tr.clean(ln) for ln in lines]


def is_head(ln):
    return len(ln) < 70 and ln.count(" ") < 9


def tone_wav(lines, p, sr=24000):
    """Preview stand-in for speech: one short beep per line, then that line's pause, so pacing still shows."""
    frames = bytearray()
    for ln in lines:
        n = int(sr * min(0.15 + 0.012 * len(ln), 1.2))
        for i in range(n):
            fade = min(1.0, i / 400, (n - i) / 400)
            frames += struct.pack("<h", int(6000 * fade * math.sin(2 * math.pi * 440 * i / sr)))
        frames += b"\0\0" * int(sr * float(p["gap_head"] if is_head(ln) else p["gap_line"]))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(bytes(frames))
    return buf.getvalue()


def synth(p, text):
    lines, spoken = spoken_lines(p, text)
    if PREVIEW:
        return tone_wav(lines, p), spoken
    import numpy as np, soundfile as sf
    k = load()
    parts = [(v, float(w)) for v, w in p["voices"] if v and float(w) > 0]
    weights = np.array([w for _, w in parts]); weights /= weights.sum()
    st = sum(k.get_voice_style(v) * w for (v, _), w in zip(parts, weights))
    out, sr = [], 24000
    for ln, said in zip(lines, spoken):
        a, sr = k.create(said, voice=st, speed=float(p["speed"]), lang="en-us")
        out += [a, np.zeros(int(sr * float(p["gap_head"] if is_head(ln) else p["gap_line"])), dtype=a.dtype)]
    buf = io.BytesIO()
    sf.write(buf, np.concatenate(out), sr, format="WAV")
    return buf.getvalue(), spoken


PAGE_FILE = os.path.join(HERE, "page.html")
SHARED = os.path.join(os.path.dirname(HERE), "shared")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode("utf-8"))

    def do_GET(self):
        if self.path in ("/shared/studio.css", "/shared/studio.js"):
            ctype = "text/css" if self.path.endswith(".css") else "text/javascript"
            return self._send(200, open(os.path.join(SHARED, self.path.rsplit("/", 1)[1]), "rb").read(), ctype + "; charset=utf-8")
        if self.path != "/":
            return self._send(404, "not found")
        PAGE = open(PAGE_FILE, encoding="utf-8").read()  # read per request, so page edits show on reload
        page = (PAGE.replace("__VOICES__", json.dumps(voice_names())).replace("__STATUS__", json.dumps(model_status()))
                .replace("__PRESET__", json.dumps(preset())).replace("__TEXT__", json.dumps(SAMPLE)))
        self._send(200, page, "text/html; charset=utf-8")

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        try:
            if self.path == "/render":
                p = {**DEFAULT, **data["preset"]}
                if not PREVIEW and model_status()["missing"]:
                    return self._send(400, "model files not found: see the note at the top of the page")
                wav, spoken = synth(p, data["text"])
                rid = time.strftime("%Y%m%d_%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}"
                RENDERS[rid] = (wav, p, data["text"], spoken)
                for old in sorted(RENDERS)[:-20]:
                    RENDERS.pop(old)
                return self._send(200, wav, "audio/wav", {"X-Spoken": quote("\n".join(spoken)), "X-Render-Id": rid})
            if self.path == "/export":
                rid = data.get("id", "")
                if rid not in RENDERS:
                    return self._send(400, "Create audio first, then save the take.")
                wav, p, text, spoken = RENDERS[rid]
                name = re.sub(r"[^\w\-]+", "_", data.get("name", "").strip())[:60] or "untitled"
                d = os.path.join(config()["exports_dir"], f"{rid}_{name}")
                os.makedirs(d, exist_ok=True)
                open(os.path.join(d, "audio.wav"), "wb").write(wav)
                json.dump({**p, "rendered": rid, "model": "preview tone" if PREVIEW else MODEL_FILES[0]},
                          open(os.path.join(d, "settings.json"), "w", encoding="utf-8"), indent=2)
                open(os.path.join(d, "input.txt"), "w", encoding="utf-8", newline="\n").write(text)
                open(os.path.join(d, "spoken.txt"), "w", encoding="utf-8", newline="\n").write("\n".join(spoken) + "\n")
                return self._send(200, f"Saved. Your take is in {d}")
            if self.path == "/preset":
                json.dump(data, open(PRESET, "w", encoding="utf-8"), indent=2)
                return self._send(200, f"saved {time.strftime('%H:%M:%S')} -> preset.json")
        except Exception as e:  # show the reason in the page instead of a dead button
            return self._send(500, f"{type(e).__name__}: {e}")
        self._send(404, "not found")


if __name__ == "__main__":
    config()
    mode = "preview mode, no model" if PREVIEW else "model loads on first Render"
    print(f"Kokoro Studio on http://127.0.0.1:{PORT}  ({mode}; Ctrl+C to stop)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
