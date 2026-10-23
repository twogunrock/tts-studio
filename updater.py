"""One-click updates from GitHub releases (ADR-009). Standard library only.

The program files are exactly what a release ZIP contains. Everything the user makes or downloads lives in files the
repo never ships (settings, pronunciations, takes, the voice model, tools/, .venv/), so copying a release over the
folder can't touch them; PROTECTED is a second guard in case a future ZIP ever did contain one.
"""
import io, json, os, re, shutil, zipfile
from urllib.request import Request, urlopen

REPO = "twogunrock/tts-studio"
API = os.environ.get("TTS_STUDIO_UPDATE_API", "https://api.github.com")  # tests point this at a local mock
INSTALLED = ".program-files"  # what the last update installed, so the next one can remove files a release dropped
PROTECTED = ("config.json", "preset.json", "overrides.json", "studio.log", "chatterbox.log", INSTALLED,
             "exports/", "models/", "voices/", "tools/", ".venv/")


def parse(v):
    return tuple(int(n) for n in re.findall(r"\d+", v)[:3])


def protected(rel):
    return any(rel == p or (p.endswith("/") and rel.startswith(p)) for p in PROTECTED)


def latest(current):
    """The newest release: {"version", "notes_url", "zip_url"}. Sends nothing but the request itself."""
    req = Request(f"{API}/repos/{REPO}/releases/latest",
                  headers={"Accept": "application/vnd.github+json", "User-Agent": f"TTS-Studio/{current}"})
    r = json.load(urlopen(req, timeout=10))
    return {"version": r["tag_name"].lstrip("vV"), "notes_url": r["html_url"], "zip_url": r["zipball_url"]}


def apply(here, info, current):
    """Install the release in info over the program files in here. On any failure, puts every file back."""
    work = os.path.join(here, "tools", "update")
    os.makedirs(work, exist_ok=True)
    data = urlopen(Request(info["zip_url"], headers={"User-Agent": f"TTS-Studio/{current}"}), timeout=180).read()

    # Check the download before anything is replaced.
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        broken = z.testzip()
    except zipfile.BadZipFile:
        broken = "the whole file"
    if broken:
        raise ValueError(f"The update download is damaged ({broken}). Nothing was changed; try again later.")
    entries = [i for i in z.infolist() if not i.is_dir()]
    prefix = entries[0].filename.split("/")[0] + "/" if entries else ""
    files = {}
    for i in entries:
        rel = i.filename[len(prefix):] if i.filename.startswith(prefix) else None
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            raise ValueError("The update download isn't laid out like TTS Studio. Nothing was changed.")
        files[rel] = i
    if "app.py" not in files or "VERSION" not in files:
        raise ValueError("The update download isn't TTS Studio. Nothing was changed.")
    if z.read(files["VERSION"]).decode("utf-8").strip() != info["version"]:
        raise ValueError("The update download doesn't match the version it claims to be. Nothing was changed.")
    files = {rel: i for rel, i in files.items() if not protected(rel)}

    # Back up whatever gets replaced or removed, then copy the new files in. Any failure restores the backup.
    backup = os.path.join(work, f"backup-{current}")
    shutil.rmtree(backup, ignore_errors=True)
    listed = os.path.join(here, INSTALLED)
    old = open(listed, encoding="utf-8").read().split("\n") if os.path.exists(listed) else []
    stale = [rel for rel in old if rel and rel not in files and not protected(rel)]
    done = []  # (rel, had an old copy)

    def keep(rel):
        src = os.path.join(here, rel)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(os.path.join(backup, rel)), exist_ok=True)
            shutil.copy2(src, os.path.join(backup, rel))
            return True
        return False

    try:
        for rel, i in files.items():
            dst = os.path.join(here, rel)
            done.append((rel, keep(rel)))
            os.makedirs(os.path.dirname(dst) or here, exist_ok=True)
            with open(dst + ".new", "wb") as out:
                out.write(z.read(i))
            mode = (i.external_attr >> 16) & 0o777
            if mode and os.name != "nt":
                os.chmod(dst + ".new", mode)  # keeps the Mac/Linux start scripts runnable
            os.replace(dst + ".new", dst)
        for rel in stale:
            if keep(rel):
                os.remove(os.path.join(here, rel))
                done.append((rel, True))
    except Exception:
        for rel, had in reversed(done):
            dst = os.path.join(here, rel)
            if had:
                shutil.copy2(os.path.join(backup, rel), dst)
            elif os.path.exists(dst):
                os.remove(dst)
        raise
    with open(listed, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(sorted(files)) + "\n")
    return info["version"]
