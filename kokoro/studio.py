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


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kokoro Studio</title>
<style>
:root{--bg:#f6f7f9;--fg:#1d2330;--card:#fff;--line:#d8dde5;--acc:#2f6fdf;--mut:#667085;--warn-bg:#fff4e0;--warn:#8a5300}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--fg:#e6e9ef;--card:#1d2128;--line:#343a45;--acc:#6ea0ff;--mut:#98a2b3;--warn-bg:#3a2c12;--warn:#f0c070}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,Segoe UI,sans-serif}
main{max-width:980px;margin:0 auto;padding:20px 16px}
h1{font-size:20px;margin:0 0 14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:14px}
.banner{background:var(--warn-bg);color:var(--warn);border-radius:8px;padding:10px 14px;margin-bottom:14px}
.row{display:flex;gap:10px;align-items:center;margin:6px 0;flex-wrap:wrap}
label{color:var(--mut);min-width:110px}
select,input[type=number],input[type=text]{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:5px;padding:4px 6px}
input[type=range]{flex:1;min-width:160px}
textarea{width:100%;box-sizing:border-box;min-height:220px;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:8px;font:14px/1.45 ui-monospace,Consolas,monospace}
button{background:var(--acc);color:#fff;border:0;border-radius:6px;padding:8px 14px;font-weight:600;cursor:pointer}
button.alt{background:transparent;color:var(--acc);border:1px solid var(--acc)} button:disabled{opacity:.4;cursor:default}
.val{min-width:44px;text-align:right;font-variant-numeric:tabular-nums}
.hint{color:var(--mut);font-size:13px}
#status{color:var(--mut)} audio{width:100%;margin-top:8px}
#spoken{white-space:pre-wrap;font:13px/1.5 ui-monospace,Consolas,monospace;color:var(--mut);max-height:260px;overflow:auto}
code{background:var(--bg);border:1px solid var(--line);border-radius:4px;padding:0 4px}
</style></head><body><main>
<h1>Kokoro Studio</h1>
<div id="banner"></div>
<div class="card"><b>Voice blend</b> <span id="wsum" class="hint"></span>
<div id="voices"></div>
<div class="row"><button class="alt" onclick="addVoice()">+ voice</button></div></div>
<div class="card"><b>Delivery</b>
<div class="row"><label>Speed</label><input type="range" id="speed" min="0.7" max="1.3" step="0.01"><span class="val" id="speed_v"></span></div>
<div class="row"><label>Pause after line</label><input type="range" id="gap_line" min="0" max="1.5" step="0.05"><span class="val" id="gap_line_v"></span></div>
<div class="row"><label>Pause after heading</label><input type="range" id="gap_head" min="0" max="2" step="0.05"><span class="val" id="gap_head_v"></span></div>
<div class="row"><label>Punctuation fixes</label><input type="checkbox" id="clean"><span class="hint">commas around quotes, dashes to pauses, list items to colons, plus your word swaps in overrides.json</span></div></div>
<div class="card"><b>Text</b> <span class="hint">one line = one spoken piece; edit freely</span>
<textarea id="text"></textarea>
<div class="row"><button onclick="go(false)">Render all</button><button class="alt" onclick="go(true)">Render selection</button>
<button class="alt" onclick="save()">Save preset</button><span id="status"></span></div>
<div class="row"><input type="text" id="xname" placeholder="name for this export" style="flex:1;min-width:200px">
<button id="xbtn" onclick="exp()" disabled>Export last render</button></div>
<audio id="audio" controls></audio></div>
<div class="card"><b>What Kokoro actually read</b><div id="spoken"></div></div>
</main><script>
const VOICES=__VOICES__, S=__STATUS__; let P=__PRESET__, LAST=null;
const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
if(S.preview) $('banner').innerHTML='<div class="banner"><b>Preview mode.</b> No model is loaded: Render plays a test tone per line so you can check pacing, and the panel at the bottom shows the real cleaned-up text.</div>';
else if(S.missing.length) $('banner').innerHTML='<div class="banner"><b>Model files not found</b> in <code>'+esc(S.model_dir)+'</code>: '+S.missing.map(esc).join(', ')+'. Download them into that folder, or change <code>model_dir</code> in config.json.</div>';
function voiceRow(v,w){const d=document.createElement('div');d.className='row';
 d.innerHTML='<select>'+VOICES.map(x=>`<option${x==v?' selected':''}>${x}</option>`).join('')+'</select>'+
 `<input type="range" min="0" max="1" step="0.05" value="${w}"><span class="val">${w}</span><button class="alt" title="remove">×</button>`;
 const r=d.querySelector('input[type=range]'),s=d.querySelector('.val');
 r.oninput=()=>{s.textContent=r.value;sum()}; d.querySelector('button').onclick=()=>{d.remove();sum()};
 $('voices').appendChild(d)}
function addVoice(){voiceRow(VOICES[0],0.25);sum()}
function sum(){const t=[...$('voices').querySelectorAll('input[type=range]')].reduce((a,r)=>a+ +r.value,0);
 $('wsum').textContent='(weights total '+t.toFixed(2)+', normalised when rendering)'}
function bind(id){const r=$(id),s=$(id+'_v');r.value=P[id];s.textContent=P[id];r.oninput=()=>s.textContent=r.value}
function read(){return{voices:[...$('voices').children].map(d=>[d.querySelector('select').value,+d.querySelector('input').value]),
 speed:+$('speed').value,gap_line:+$('gap_line').value,gap_head:+$('gap_head').value,clean:$('clean').checked}}
async function go(sel){const ta=$('text');let t=ta.value;if(sel){t=ta.value.substring(ta.selectionStart,ta.selectionEnd);if(!t.trim()){$('status').textContent='select some text first';return}}
 $('status').textContent=S.loaded||S.preview?'rendering…':'loading the model, then rendering…';const t0=Date.now();
 const r=await fetch('/render',{method:'POST',body:JSON.stringify({preset:read(),text:t})});
 if(!r.ok){$('status').textContent='error: '+await r.text();return}
 S.loaded=true;$('spoken').textContent=decodeURIComponent(r.headers.get('X-Spoken')||'');
 LAST=r.headers.get('X-Render-Id');$('xbtn').disabled=false;
 const b=await r.blob();$('audio').src=URL.createObjectURL(b);$('audio').play().catch(()=>{});
 $('status').textContent='done in '+((Date.now()-t0)/1000).toFixed(1)+'s'}
async function exp(){if(!LAST)return;const r=await fetch('/export',{method:'POST',body:JSON.stringify({id:LAST,name:$('xname').value})});
 $('status').textContent=await r.text()}
async function save(){const r=await fetch('/preset',{method:'POST',body:JSON.stringify(read())});$('status').textContent=await r.text()}
P.voices.forEach(([v,w])=>voiceRow(v,w));sum();['speed','gap_line','gap_head'].forEach(bind);$('clean').checked=P.clean;
$('text').value=__TEXT__;
</script></body></html>"""


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
        if self.path != "/":
            return self._send(404, "not found")
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
                    return self._send(400, "nothing to export: render something first")
                wav, p, text, spoken = RENDERS[rid]
                name = re.sub(r"[^\w\-]+", "_", data.get("name", "").strip())[:60] or "untitled"
                d = os.path.join(config()["exports_dir"], f"{rid}_{name}")
                os.makedirs(d, exist_ok=True)
                open(os.path.join(d, "audio.wav"), "wb").write(wav)
                json.dump({**p, "rendered": rid, "model": "preview tone" if PREVIEW else MODEL_FILES[0]},
                          open(os.path.join(d, "settings.json"), "w", encoding="utf-8"), indent=2)
                open(os.path.join(d, "input.txt"), "w", encoding="utf-8", newline="\n").write(text)
                open(os.path.join(d, "spoken.txt"), "w", encoding="utf-8", newline="\n").write("\n".join(spoken) + "\n")
                return self._send(200, f"exported -> {d}")
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
