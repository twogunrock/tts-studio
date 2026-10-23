"""Chatterbox worker for TTS Studio.

Runs inside the user's own Chatterbox Python environment; app.py starts it on first use and stops it when
Kokoro is used, so only one model sits in memory. Protocol: the worker prints one JSON "ready" line after the
model loads, then answers one JSON request per stdin line with one JSON reply line. Everything the libraries
print goes to stderr (app.py writes it to chatterbox.log), so stdout carries only the protocol.

Kept to the standard library plus what Chatterbox itself installs (torch, numpy, perth).
"""
import json, os, sys, wave

proto = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")  # private copy of stdout for replies
os.dup2(2, 1)                                                    # stray prints, even from C code, go to stderr
sys.stdout = sys.stderr


def reply(**kw):
    proto.write(json.dumps(kw) + "\n")
    proto.flush()


try:
    import numpy as np, torch, perth
    WATERMARK = perth.PerthImplicitWatermarker is not None
    if not WATERMARK:  # needs pkg_resources (setuptools < 81); without it Chatterbox can't start at all
        perth.PerthImplicitWatermarker = perth.DummyWatermarker
    from chatterbox.tts import ChatterboxTTS
    dev = os.environ.get("TTS_STUDIO_DEVICE", "auto")
    if dev == "auto":
        dev = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    model = ChatterboxTTS.from_pretrained(device=dev)
except Exception as e:
    reply(ready=False, error=f"{type(e).__name__}: {e}")
    sys.exit(1)

reply(ready=True, watermark=WATERMARK, device=dev)

for line in sys.stdin:
    try:
        req = json.loads(line)
        parts, starts, pos = [], [], 0
        for i, (said, gap) in enumerate(zip(req["lines"], req["gaps"])):
            torch.manual_seed(int(req["seed"]) + i)  # per-line seed: editing one line leaves the others identical
            wav = model.generate(said, audio_prompt_path=req.get("ref") or None,
                                 exaggeration=float(req["exaggeration"]), cfg_weight=float(req["cfg_weight"]),
                                 temperature=float(req["temperature"]))
            w = wav.squeeze(0).cpu().numpy()
            parts += [w, np.zeros(int(model.sr * float(gap)), dtype=w.dtype)]
            starts.append(pos / model.sr); pos += len(w) + int(model.sr * float(gap))
        pcm = (np.clip(np.concatenate(parts), -1, 1) * 32767).astype("<i2")
        with wave.open(req["out"], "wb") as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(model.sr); f.writeframes(pcm.tobytes())
        reply(ok=True, starts=starts)  # when each line starts, so the page can light it while playing
    except Exception as e:
        reply(ok=False, error=f"{type(e).__name__}: {e}")
