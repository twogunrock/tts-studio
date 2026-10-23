"""Chatterbox Studio: tune a Chatterbox narration voice by ear, in your browser.

    python studio.py              ->  http://127.0.0.1:7861
    python studio.py --preview    ->  the page with no model loaded (Render plays a test tone)
    python studio.py --port 7871

Stdlib web server. The model loads on the first Render, not at startup, and holds ~4 GB of GPU memory until
you stop the studio. config.json (next to this file, created on first run) says where the model download is
cached (hf_home), which device to use, and where reference clips live. "Save preset" writes preset.json;
"Export last render" writes exports/<time>_<name>/ = audio.wav + settings.json + input.txt + spoken.txt.
"""
import importlib.util, io, json, math, os, re, struct, sys, threading, time, wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
PRESET = os.path.join(HERE, "preset.json")
DEFAULT_CONFIG = {"hf_home": os.path.join(HERE, "hf"), "device": "auto", "refs_dir": os.path.join(HERE, "refs"),
                  "exports_dir": os.path.join(HERE, "exports"), "overrides": os.path.join(HERE, "overrides.json")}
BUILTIN = "(built-in voice)"
DEFAULT = {"ref": BUILTIN, "exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8,
           "seed": 42, "gap_line": 0.5, "gap_head": 1.0, "fixes": True}
SAMPLE = """Welcome to Chatterbox Studio.
Each line in this box is spoken as its own piece, with a pause after it.
Short lines, like headings, get a longer pause.
Punctuation steers the delivery! Does a question sound like one?
Pick a reference clip above and its delivery carries over."""

PREVIEW = "--preview" in sys.argv
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 7861
M, WATERMARK, LOCK = None, None, threading.Lock()
RENDERS = {}  # render id -> (wav bytes, preset, input text, spoken lines); last 20 kept for Export


def config():
    try:
        return {**DEFAULT_CONFIG, **json.load(open(CONFIG, encoding="utf-8"))}
    except (OSError, ValueError):
        json.dump(DEFAULT_CONFIG, open(CONFIG, "w", encoding="utf-8"), indent=2)
        return dict(DEFAULT_CONFIG)


def model_status():
    installed = importlib.util.find_spec("chatterbox") is not None  # checks the install without importing torch
    return {"preview": PREVIEW, "installed": installed, "hf_home": config()["hf_home"], "loaded": M is not None,
            "watermark": WATERMARK}


def preset():
    try:
        return {**DEFAULT, **json.load(open(PRESET, encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULT)


def refs():
    d = config()["refs_dir"]
    os.makedirs(d, exist_ok=True)
    return [BUILTIN] + sorted(f for f in os.listdir(d) if f.lower().endswith(".wav"))


def load():
    """First Render loads the model. Imports live here so preview mode needs nothing but Python."""
    global M, WATERMARK
    with LOCK:
        if M is None:
            c = config()
            os.environ["HF_HOME"] = c["hf_home"]  # where the ~3 GB model download is cached
            import torch, perth
            WATERMARK = perth.PerthImplicitWatermarker is not None
            if not WATERMARK:  # its backend doesn't load on every Python version; output is then unwatermarked
                perth.PerthImplicitWatermarker = perth.DummyWatermarker
            from chatterbox.tts import ChatterboxTTS
            dev = c["device"]
            if dev == "auto":
                dev = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
            M = ChatterboxTTS.from_pretrained(device=dev)
    return M


def fixes(ln):
    """Pronunciation swaps only. Chatterbox reads punctuation naturally, so no pause rewrites."""
    try:
        words = json.load(open(config()["overrides"], encoding="utf-8")).get("words", {})
    except (OSError, ValueError):
        return ln
    for w, say in words.items():
        ln = re.sub(rf"\b{re.escape(w)}\b", say, ln)
    return ln


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
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    spoken = [fixes(ln) if p["fixes"] else ln for ln in lines]
    if PREVIEW:
        return tone_wav(lines, p), spoken
    import numpy as np, soundfile as sf, torch
    m = load()
    ref = None if p["ref"] == BUILTIN else os.path.join(config()["refs_dir"], os.path.basename(p["ref"]))
    out, sr = [], m.sr
    for i, (ln, said) in enumerate(zip(lines, spoken)):
        torch.manual_seed(int(p["seed"]) + i)  # per-line seed: editing one line leaves the others identical
        wav = m.generate(said, audio_prompt_path=ref, exaggeration=float(p["exaggeration"]),
                         cfg_weight=float(p["cfg_weight"]), temperature=float(p["temperature"]))
        wav = wav.squeeze(0).cpu().numpy()
        out += [wav, np.zeros(int(sr * float(p["gap_head"] if is_head(ln) else p["gap_line"])), dtype=wav.dtype)]
    buf = io.BytesIO()
    sf.write(buf, np.concatenate(out), sr, format="WAV")
    return buf.getvalue(), spoken


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Chatterbox Studio</title>
<style>
:root{--bg:#f6f7f9;--fg:#1d2330;--card:#fff;--line:#d8dde5;--acc:#2f6fdf;--mut:#667085;--warn-bg:#fff4e0;--warn:#8a5300}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--fg:#e6e9ef;--card:#1d2128;--line:#343a45;--acc:#6ea0ff;--mut:#98a2b3;--warn-bg:#3a2c12;--warn:#f0c070}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,Segoe UI,sans-serif}
main{max-width:980px;margin:0 auto;padding:20px 16px}
h1{font-size:20px;margin:0 0 14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:14px}
.banner{background:var(--warn-bg);color:var(--warn);border-radius:8px;padding:10px 14px;margin-bottom:14px}
.row{display:flex;gap:10px;align-items:center;margin:6px 0;flex-wrap:wrap}
label{color:var(--mut);min-width:120px}
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
<h1>Chatterbox Studio</h1>
<div id="banner"></div>
<div class="card"><b>Voice</b>
<div class="row"><label>Reference clip</label><select id="ref"></select><span class="hint">or any .wav in <code>__REFS_DIR__</code> (about 10 s); its delivery carries over</span></div>
<div class="row"><label>Exaggeration</label><input type="range" id="exaggeration" min="0" max="1.5" step="0.05"><span class="val" id="exaggeration_v"></span><span class="hint">emotion strength</span></div>
<div class="row"><label>CFG weight</label><input type="range" id="cfg_weight" min="0" max="1" step="0.05"><span class="val" id="cfg_weight_v"></span><span class="hint">lower = slower, more deliberate</span></div>
<div class="row"><label>Temperature</label><input type="range" id="temperature" min="0.3" max="1.2" step="0.05"><span class="val" id="temperature_v"></span><span class="hint">lower = steadier, higher = livelier</span></div>
<div class="row"><label>Seed</label><input type="number" id="seed" style="width:90px"><button class="alt" onclick="$('seed').value=Math.floor(Math.random()*99999)">new take</button><span class="hint">same seed + same line = same audio</span></div></div>
<div class="card"><b>Pacing and text</b>
<div class="row"><label>Pause after line</label><input type="range" id="gap_line" min="0" max="1.5" step="0.05"><span class="val" id="gap_line_v"></span></div>
<div class="row"><label>Pause after heading</label><input type="range" id="gap_head" min="0" max="2" step="0.05"><span class="val" id="gap_head_v"></span></div>
<div class="row"><label>Pronunciation fixes</label><input type="checkbox" id="fixes"><span class="hint">word swaps from overrides.json</span></div></div>
<div class="card"><b>Text</b> <span class="hint">one line = one generated piece. Punctuation (! ? … ,) steers delivery.</span>
<textarea id="text"></textarea>
<div class="row"><button onclick="go(false)">Render all</button><button class="alt" onclick="go(true)">Render selection</button>
<button class="alt" onclick="save()">Save preset</button><span id="status"></span></div>
<div class="row"><input type="text" id="xname" placeholder="name for this export" style="flex:1;min-width:200px">
<button id="xbtn" onclick="exp()" disabled>Export last render</button></div>
<audio id="audio" controls></audio></div>
<div class="card"><b>What Chatterbox read</b><div id="spoken"></div></div>
</main><script>
const REFS=__REFS__, S=__STATUS__; let P=__PRESET__, LAST=null;
const $=id=>document.getElementById(id);
function banner(){let h='';
 if(S.preview) h='<b>Preview mode.</b> No model is loaded: Render plays a test tone per line so you can check pacing, and the panel at the bottom shows the text after word swaps.';
 else if(!S.installed) h='<b>Chatterbox isn\\'t installed</b> in the Python running this studio. Run <code>pip install chatterbox-tts</code>, then restart.';
 else if(S.watermark===false) h='<b>Watermark off.</b> The Perth watermarker didn\\'t load on this Python version, so renders are not watermarked.';
 $('banner').innerHTML=h?'<div class="banner">'+h+'</div>':''}
banner();
$('ref').innerHTML=REFS.map(r=>`<option${r==P.ref?' selected':''}>${r}</option>`).join('');
['exaggeration','cfg_weight','temperature','gap_line','gap_head'].forEach(id=>{const r=$(id),s=$(id+'_v');r.value=P[id];s.textContent=P[id];r.oninput=()=>s.textContent=r.value});
$('seed').value=P.seed;$('fixes').checked=P.fixes;$('text').value=__TEXT__;
function read(){return{ref:$('ref').value,exaggeration:+$('exaggeration').value,cfg_weight:+$('cfg_weight').value,
 temperature:+$('temperature').value,seed:+$('seed').value,gap_line:+$('gap_line').value,gap_head:+$('gap_head').value,fixes:$('fixes').checked}}
async function go(sel){const ta=$('text');let t=ta.value;if(sel){t=ta.value.substring(ta.selectionStart,ta.selectionEnd);if(!t.trim()){$('status').textContent='select some text first';return}}
 $('status').textContent=S.loaded||S.preview?'rendering… (about real time)':'loading the model (first time downloads ~3 GB), then rendering…';const t0=Date.now();
 const r=await fetch('/render',{method:'POST',body:JSON.stringify({preset:read(),text:t})});
 if(!r.ok){$('status').textContent='error: '+await r.text();return}
 S.loaded=true;const wm=r.headers.get('X-Watermark');if(wm)S.watermark=wm==='on';banner();
 $('spoken').textContent=decodeURIComponent(r.headers.get('X-Spoken')||'');LAST=r.headers.get('X-Render-Id');$('xbtn').disabled=false;
 const b=await r.blob();$('audio').src=URL.createObjectURL(b);$('audio').play().catch(()=>{});
 $('status').textContent='done in '+((Date.now()-t0)/1000).toFixed(1)+'s'}
async function save(){const r=await fetch('/preset',{method:'POST',body:JSON.stringify(read())});$('status').textContent=await r.text()}
async function exp(){if(!LAST)return;const r=await fetch('/export',{method:'POST',body:JSON.stringify({id:LAST,name:$('xname').value})});$('status').textContent=await r.text()}
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
        page = (PAGE.replace("__REFS_DIR__", config()["refs_dir"].replace("&", "&amp;").replace("<", "&lt;"))
                .replace("__REFS__", json.dumps(refs())).replace("__STATUS__", json.dumps(model_status()))
                .replace("__PRESET__", json.dumps(preset())).replace("__TEXT__", json.dumps(SAMPLE)))
        self._send(200, page, "text/html; charset=utf-8")

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        try:
            if self.path == "/render":
                p = {**DEFAULT, **data["preset"]}
                if not PREVIEW and not model_status()["installed"]:
                    return self._send(400, "Chatterbox isn't installed: see the note at the top of the page")
                wav, spoken = synth(p, data["text"])
                rid = time.strftime("%Y%m%d_%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}"
                RENDERS[rid] = (wav, p, data["text"], spoken)
                for old in sorted(RENDERS)[:-20]:
                    RENDERS.pop(old)
                extra = {"X-Spoken": quote("\n".join(spoken)), "X-Render-Id": rid}
                if WATERMARK is not None:
                    extra["X-Watermark"] = "on" if WATERMARK else "off"
                return self._send(200, wav, "audio/wav", extra)
            if self.path == "/preset":
                json.dump(data, open(PRESET, "w", encoding="utf-8"), indent=2)
                return self._send(200, f"saved {time.strftime('%H:%M:%S')} -> preset.json")
            if self.path == "/export":
                rid = data.get("id", "")
                if rid not in RENDERS:
                    return self._send(400, "nothing to export: render something first")
                wav, p, text, spoken = RENDERS[rid]
                name = re.sub(r"[^\w\-]+", "_", data.get("name", "").strip())[:60] or "untitled"
                d = os.path.join(config()["exports_dir"], f"{rid}_{name}")
                os.makedirs(d, exist_ok=True)
                open(os.path.join(d, "audio.wav"), "wb").write(wav)
                json.dump({**p, "rendered": rid, "model": "preview tone" if PREVIEW else "chatterbox (standard)"},
                          open(os.path.join(d, "settings.json"), "w", encoding="utf-8"), indent=2)
                open(os.path.join(d, "input.txt"), "w", encoding="utf-8", newline="\n").write(text)
                open(os.path.join(d, "spoken.txt"), "w", encoding="utf-8", newline="\n").write("\n".join(spoken) + "\n")
                return self._send(200, f"exported -> {d}")
        except Exception as e:  # show the reason in the page instead of a dead button
            return self._send(500, f"{type(e).__name__}: {e}")
        self._send(404, "not found")


if __name__ == "__main__":
    config()
    mode = "preview mode, no model" if PREVIEW else "model loads on first Render"
    print(f"Chatterbox Studio on http://127.0.0.1:{PORT}  ({mode}; Ctrl+C to stop)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
