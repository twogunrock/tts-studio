"""Text cleanup applied before Kokoro speaks a line. Pure stdlib, so preview mode can show it without the model."""
import json, re


def clean(ln):
    """Kokoro pauses on commas, not on quotes or dashes; without this, quoted phrases run together."""
    ln = ln.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    # Pause rules from https://deapi.ai/blog/kokoro-tts-guide-how-to-control-41-voices-with-nothing-but-punctuation
    # ("," short breath, ":" anticipation, " - " lighter than a comma, ALL CAPS / SSML do nothing).
    ln = re.sub(r'(?:…|\.\.\.)"', '"', ln)                 # "I intend to…" -> "I intend to"
    ln = re.sub(r"^([\w/&' ]{1,30}?)\s*[—–-]\s+", r"\1: ", ln)  # "Initiative – You're…" list item -> colon
    ln = re.sub(r'"\s*[—–-]\s+', '". ', ln)                  # title: "I Intend To" – Clarity… (a comma here was ignored)
    ln = re.sub(r"\s*(—|–| - )\s*", ", ", ln)                # other dashes -> comma pause
    ln = ln.replace("…", ",").replace("...", ",")
    ln = ln.replace(" & ", " and ").replace(" = ", " equals ").replace(" + ", " plus ")  # symbols get spoken oddly
    ln = re.sub(r'([.!?]["\'])\.$', r"\1", ln)               # 'ID 8193."' + added '.' -> one full stop
    # Comma goes outside the closing quote, so the quoted phrase gets a pause on both sides.
    ln = re.sub(r'(\w) "([^"]+?)[,.]?" (?=[a-z])', r'\1, "\2", ', ln)  # mid-sentence quote gets commas
    ln = re.sub(r'"([^"]+?)[,.]?" (?=[a-z])', r'"\1", ', ln)           # quote followed by its verb
    return re.sub(r",\s*,", ",", ln)


def apply_overrides(lines, path):
    """overrides.json: exact line replacements first, then whole-word swaps. Returns a new list of lines."""
    try:
        ov = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return lines
    out = []
    for ln in lines:
        ln = ov.get("lines", {}).get(ln, ln)
        for w, say in ov.get("words", {}).items():
            ln = re.sub(rf"\b{re.escape(w)}\b", say, ln)
        out += ln.split("\n")
    return out


def join_labels(lines, max_len=40):
    """A short label ending in ':' ("Tech Support:", "with:") is read with the line after it, not as its own
    piece. Longer lead-ins ("…helps create a culture of:") stay separate."""
    out, carry = [], ""
    for ln in lines:
        if carry:
            ln, carry = f"{carry} {ln}", ""
        if ln.endswith(":") and len(ln) <= max_len:
            carry = ln
            continue
        out.append(ln)
    if carry:
        out.append(carry)
    return out


def split_long(lines, limit=450):
    """Same guide: prosody degrades past ~500 chars, so break long lines at sentence ends."""
    out = []
    for ln in lines:
        cur = ""
        for s in re.split(r"(?<=[.!?])\s+", ln):
            if cur and len(cur) + len(s) > limit:
                out.append(cur.strip()); cur = ""
            cur += s + " "
        if cur.strip():
            out.append(cur.strip())
    return out
