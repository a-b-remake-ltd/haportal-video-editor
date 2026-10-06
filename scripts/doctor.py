#!/usr/bin/env python3
"""One-command setup and health check for the skill.

    python3 scripts/doctor.py                    # check only — changes nothing
    python3 scripts/doctor.py --install          # fix what can be fixed safely (free)
    python3 scripts/doctor.py --install --test-transcribe   # …and prove Hebrew transcription works
    python3 scripts/doctor.py --install --with-roto         # also torch + torchvision (rotoscoping)

WHY it exists: the people using this skill follow a video-editing tutorial; many have
never opened a terminal. Setup has to be one command, free by default, and every miss
has to come with the exact command that fixes it.

What --install does (each step only when it is missing):
  1. builds ONE python venv for the skill's heavier deps at
       ~/.haportal-video-editor/venv     ($HAPORTAL_VIDEO_EDITOR_HOME overrides)
     outside the skill folder, because a skill installed as a plugin may be read-only.
     Scripts that need it re-run themselves under it (hfcfg.ensure_deps), so nobody ever
     activates a venv by hand. Uses `uv` when present (fast; it can fetch a modern python),
     otherwise the system python's own venv + pip.
  2. pip-installs numpy, pillow, python-bidi and faster-whisper (the free Hebrew
     transcriber). --with-mlx adds mlx-whisper (Apple-GPU Whisper for NON-Hebrew speech;
     it pulls torch, so it is opt-in). --with-roto adds torch + torchvision for matte.py —
     heavy, off by default.
  3. downloads the Hebrew model ivrit-ai/whisper-large-v3-turbo-ct2 (~1.6 GB, once, into
     ~/.cache/huggingface).
  4. runs scripts/setup_assets.py (fonts, synthesised SFX, flares, gsap).
  5. caches the HyperFrames CLI (`npx --yes hyperframes --version`).
It never installs system software (ffmpeg, Node, Chrome) — it prints the exact command.
"""
from __future__ import annotations

import argparse
import glob
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

HEBREW_MODEL = "ivrit-ai/whisper-large-v3-turbo-ct2"
BASE_PKGS = ["numpy", "pillow", "python-bidi", "faster-whisper"]
MLX_PKGS = ["mlx-whisper"]
ROTO_PKGS = ["torch", "torchvision"]
MIN_NODE = 22
MIN_FREE_GB = 8

MAC = sys.platform == "darwin"
ARM_MAC = MAC and platform.machine() == "arm64"

OK, BAD, WARN, INFO = "  ✓", "  ✗", "  !", "  ·"
problems, warnings = [], []


def line(mark, what, detail="", fix=""):
    print(f"{mark} {what}" + (f" — {detail}" if detail else ""), flush=True)
    for i, f in enumerate(fix.splitlines() if fix else []):
        print(("        fix: " if i == 0 else "             ") + f)
    if mark == BAD:
        problems.append(what)
    elif mark == WARN:
        warnings.append(what)


def sh(cmd, timeout=120, **kw):
    try:
        return hfcfg.run(cmd, timeout=timeout, **kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


# ===================================================================== checks

def check_python():
    v = sys.version_info
    if v < (3, 9):
        line(BAD, f"python {v.major}.{v.minor}", "3.9 or newer is needed",
             "brew install python   (or https://www.python.org/downloads/)")
    else:
        line(OK, f"python {v.major}.{v.minor}.{v.micro}", sys.executable)


def check_ffmpeg():
    for tool in ("ffmpeg", "ffprobe"):
        p = hfcfg.which(tool)
        if not p:
            line(BAD, tool, "not found",
                 "brew install ffmpeg" if MAC else "sudo apt install ffmpeg   (or your OS's package)")
            continue
        out = sh([tool, "-version"]).stdout.splitlines()
        ver = out[0].split(" version ")[1].split()[0] if out and " version " in out[0] else "?"
        line(OK, tool, f"{ver}  ({p})")


def check_node():
    node = shutil.which("node")
    if not node:
        line(BAD, "Node.js", "not found (HyperFrames needs Node 22+)",
             "brew install node" if MAC else "https://nodejs.org (LTS 22 or newer)")
        return False
    v = sh(["node", "--version"]).stdout.strip()
    m = re.match(r"v(\d+)", v)
    if not m or int(m.group(1)) < MIN_NODE:
        line(BAD, f"Node.js {v}", f"HyperFrames needs {MIN_NODE}+",
             "brew upgrade node" if MAC else "install Node 22+ from https://nodejs.org")
        return False
    line(OK, f"Node.js {v}")
    return True


def check_hyperframes(install):
    if not shutil.which("npx"):
        line(BAD, "HyperFrames", "npx not found (comes with Node.js)")
        return
    cmd = ["npx", "--yes" if install else "--no-install", "hyperframes", "--version"]
    r = sh(cmd, timeout=300 if install else 60)
    out = (r.stdout + r.stderr).strip().splitlines()
    ver = next((x for x in out if re.search(r"\d+\.\d+", x)), "")
    if r.returncode == 0 and ver:
        line(OK, "HyperFrames", ver.strip())
    elif install:
        line(BAD, "HyperFrames", "npx could not run it", "npx --yes hyperframes --version")
    else:
        line(WARN, "HyperFrames", "not cached yet — downloads on first use",
             "python3 scripts/doctor.py --install   (or: npx --yes hyperframes --version)")


def check_chrome():
    try:
        p = hfcfg.chrome_path()
    except SystemExit:
        p = None
    if p:
        line(OK, "Chrome", p)
    else:
        line(BAD, "Chrome / Chromium", "not found (captions and screenshots render with it)",
             "install Google Chrome from https://www.google.com/chrome/\n"
             "or set CHROME_PATH=/path/to/chrome")


def check_disk():
    free = shutil.disk_usage(os.path.expanduser("~")).free / 1e9
    if free < MIN_FREE_GB:
        line(WARN, "disk space", f"{free:.0f} GB free — the Hebrew model is 1.6 GB and a "
             f"4K render can take several GB", "free some space before a long session")
    else:
        line(OK, "disk space", f"{free:.0f} GB free")


def py_status(mod):
    """'here' (this interpreter), 'venv', or None."""
    if hfcfg.has_module(mod):
        return "here"
    vp = hfcfg.venv_python()
    if vp and hfcfg.has_module(mod, vp):
        return "venv"
    return None


def check_python_deps(with_roto, with_mlx):
    vp = hfcfg.venv_python()
    line(OK if vp else INFO, "skill venv",
         vp if vp else f"not built yet ({hfcfg.venv_dir()})")
    wanted = [("numpy", "numpy", True), ("PIL", "pillow", True),
              ("faster_whisper", "faster-whisper", True), ("bidi", "python-bidi", False)]
    if with_mlx or ARM_MAC:
        wanted.append(("mlx_whisper", "mlx-whisper", with_mlx))
    wanted += [("torch", "torch", with_roto), ("torchvision", "torchvision", with_roto)]
    for mod, pkg, required in wanted:
        where = py_status(mod)
        if where:
            line(OK, pkg, "system python" if where == "here" else "skill venv")
        elif required:
            line(BAD, pkg, "missing", "python3 scripts/doctor.py --install"
                 + (" --with-roto" if pkg in ROTO_PKGS else "")
                 + (" --with-mlx" if pkg in MLX_PKGS else ""))
        else:
            note = {"python-bidi": "optional — RTL labels already work without it",
                    "mlx-whisper": "optional — Apple-GPU Whisper for non-Hebrew (--with-mlx)",
                    "torch": "optional — only for rotoscoping (--with-roto)",
                    "torchvision": "optional — only for rotoscoping (--with-roto)"}[pkg]
            line(INFO, pkg, note)


def hf_cached(repo):
    """Is a complete snapshot of `repo` in the Hugging Face cache?"""
    base = os.environ.get("HF_HUB_CACHE") or os.path.join(
        os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface"), "hub")
    snap = glob.glob(os.path.join(base, "models--" + repo.replace("/", "--"),
                                  "snapshots", "*", "model.bin"))
    return any(os.path.getsize(p) > 500e6 for p in snap if os.path.exists(p))


def check_model():
    if hf_cached(HEBREW_MODEL):
        line(OK, "Hebrew model", f"{HEBREW_MODEL} (cached)")
    else:
        line(WARN, "Hebrew model", f"{HEBREW_MODEL} not downloaded yet (1.6 GB, once)",
             "python3 scripts/doctor.py --install")


def check_assets():
    a = os.path.join(hfcfg.SKILL_DIR, "assets")
    fonts = glob.glob(os.path.join(a, "fonts", "*.ttf")) + glob.glob(os.path.join(a, "fonts", "*.otf"))
    gsap = os.path.exists(os.path.join(a, "vendor", "gsap.min.js"))
    sfx = len(glob.glob(os.path.join(a, "sfx", "*.wav")))
    flares = os.path.exists(os.path.join(a, "flares", "flare2_v.mp4"))
    missing = [n for n, ok in (("fonts", fonts), ("gsap", gsap), ("sfx", sfx), ("flares", flares))
               if not ok]
    if missing:
        line(BAD, "skill assets", "missing: " + ", ".join(missing),
             "python3 scripts/doctor.py --install   (or: python3 scripts/setup_assets.py)")
    else:
        names = ", ".join(sorted({os.path.splitext(os.path.basename(f))[0] for f in fonts}))
        line(OK, "skill assets", f"fonts ({names}), {sfx} sfx, flares, gsap")


def check_scribe():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import transcribe
    if transcribe.env_key("ELEVENLABS_API_KEY"):
        line(INFO, "ElevenLabs Scribe", "key found — paid upgrade available (--engine scribe)")
    else:
        line(INFO, "ElevenLabs Scribe", "no key — not needed; the free Hebrew model is the default")


# ==================================================================== install

def pick_python_for_venv():
    """A modern interpreter when one is around (newer wheels, faster numpy), else this one.
    The scripts themselves stay 3.9-compatible either way."""
    for name in ("python3.12", "python3.11", "python3.13", "python3.10"):
        p = shutil.which(name)
        if p:
            return p
    return sys.executable


def build_venv():
    vdir = hfcfg.venv_dir()
    if hfcfg.venv_python():
        return hfcfg.venv_python()
    os.makedirs(os.path.dirname(vdir), exist_ok=True)
    uv = shutil.which("uv")
    print(f"    creating the skill venv at {vdir} …", flush=True)
    if uv:
        r = sh([uv, "venv", "--python", "3.12", "--seed", vdir], timeout=600)
        if r.returncode:
            r = sh([uv, "venv", "--python", sys.executable, "--seed", vdir], timeout=600)
    else:
        r = sh([pick_python_for_venv(), "-m", "venv", vdir], timeout=600)
    if r.returncode or not hfcfg.venv_python():
        print((r.stderr or r.stdout)[-800:])
        return None
    return hfcfg.venv_python()


def pip_install(vp, pkgs):
    uv = shutil.which("uv")
    print(f"    installing {' '.join(pkgs)} …", flush=True)
    if uv:
        cmd = [uv, "pip", "install", "--python", vp] + pkgs
    else:
        sh([vp, "-m", "pip", "install", "-q", "--upgrade", "pip"], timeout=600)
        cmd = [vp, "-m", "pip", "install", "-q"] + pkgs
    r = sh(cmd, timeout=3600)
    if r.returncode:
        print((r.stderr or r.stdout)[-1500:])
    return r.returncode == 0


def download_model(vp):
    print(f"    downloading {HEBREW_MODEL} (1.6 GB, once) …", flush=True)
    code = ("from faster_whisper import WhisperModel;"
            f"WhisperModel({HEBREW_MODEL!r}, device='cpu', compute_type='int8');print('ok')")
    r = sh([vp, "-c", code], timeout=7200)
    return r.returncode == 0


def install(with_roto, with_mlx):
    print("\ninstalling (free, local) …", flush=True)
    vp = build_venv()
    if not vp:
        line(BAD, "skill venv", "could not be created",
             f"python3 -m venv {hfcfg.venv_dir()}   then re-run this command")
        return
    pkgs = [p for p in BASE_PKGS]
    if with_mlx or (with_roto and ARM_MAC):
        pkgs += MLX_PKGS
    if with_roto:
        pkgs += ROTO_PKGS
    mods = {"numpy": "numpy", "pillow": "PIL", "python-bidi": "bidi",
            "faster-whisper": "faster_whisper", "mlx-whisper": "mlx_whisper",
            "torch": "torch", "torchvision": "torchvision"}
    todo = [p for p in pkgs if not hfcfg.has_module(mods[p], vp)]
    if todo and not pip_install(vp, todo):
        line(BAD, "python packages", "pip failed (see above)",
             f"{vp} -m pip install {' '.join(todo)}")
    if not hf_cached(HEBREW_MODEL) and hfcfg.has_module("faster_whisper", vp):
        if not download_model(vp):
            line(WARN, "Hebrew model", "download failed — it will retry on first transcription")
    r = subprocess.run([sys.executable, os.path.join(hfcfg.SKILL_DIR, "scripts", "setup_assets.py")],
                       env=hfcfg.ffmpeg_env())
    if r.returncode:
        line(WARN, "setup_assets.py", "finished with problems (see its output above)")
    print("", flush=True)


# ============================================================ test transcribe

def test_transcribe():
    """Prove the whole path: audio → Hebrew model → words. Uses macOS's Hebrew voice
    (Carmit) when present so there is real speech to recognise; otherwise a 2 s tone,
    which only proves the model loads and runs."""
    print("\ntest transcription …", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "probe.wav")
        phrase = "שלום, זאת בדיקה של התמלול"
        spoken = False
        if MAC and shutil.which("say"):
            voices = sh(["say", "-v", "?"]).stdout
            if re.search(r"^Carmit\s", voices, re.M):
                aiff = os.path.join(tmp, "probe.aiff")
                if sh(["say", "-v", "Carmit", "-o", aiff, phrase]).returncode == 0:
                    spoken = sh(["ffmpeg", "-v", "error", "-y", "-i", aiff, "-ar", "16000",
                                 "-ac", "1", clip]).returncode == 0
        if not spoken:
            sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=2", "-ar", "16000", "-ac", "1", clip])
        vp = hfcfg.venv_python() if not hfcfg.has_module("faster_whisper") else sys.executable
        if not vp or not hfcfg.has_module("faster_whisper", vp):
            line(BAD, "test transcription", "faster-whisper is not installed",
                 "python3 scripts/doctor.py --install")
            return
        script = os.path.join(hfcfg.SKILL_DIR, "scripts", "transcribe.py")
        out = os.path.join(tmp, "t.json")
        t0 = time.time()
        r = sh([vp, script, clip, "--lang", "he", "--engine", "auto", "--out", out,
                "--words", os.path.join(tmp, "w.json"), "--flags", "", "--force"],
               timeout=3600)
        dt = time.time() - t0
        if r.returncode:
            line(BAD, "test transcription", "failed", (r.stderr or r.stdout).strip()[-600:])
            return
        import json
        text = json.load(open(out, encoding="utf-8")).get("text", "").strip()
        if spoken:
            ok = "בדיקה" in text or "שלום" in text
            line(OK if ok else WARN, "test transcription",
                 f"heard \"{text}\" in {dt:.0f}s (incl. model load)")
        else:
            line(OK, "test transcription", f"model loaded and ran in {dt:.0f}s "
                 f"(tone only — no Hebrew voice to speak a test phrase)")


# ======================================================================= main

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true", help="fix what can be fixed safely")
    ap.add_argument("--with-roto", action="store_true",
                    help="also torch + torchvision for rotoscoping (heavy)")
    ap.add_argument("--with-mlx", action="store_true",
                    help="also mlx-whisper (Apple-GPU Whisper for non-Hebrew; pulls torch)")
    ap.add_argument("--test-transcribe", action="store_true",
                    help="run a short Hebrew test transcription end to end")
    a = ap.parse_args()

    print(f"HAPORTAL - VIDEO EDITOR — doctor   ({hfcfg.SKILL_DIR})\n", flush=True)
    if a.install:
        install(a.with_roto, a.with_mlx)
        print("checking …\n", flush=True)

    check_python()
    check_ffmpeg()
    if check_node():
        check_hyperframes(a.install)
    check_chrome()
    check_disk()
    check_python_deps(a.with_roto, a.with_mlx)
    check_model()
    check_assets()
    check_scribe()
    if a.test_transcribe:
        test_transcribe()

    print()
    if problems:
        print(f"{len(problems)} thing(s) to fix: {', '.join(problems)}.")
        if not a.install:
            print("most of them: python3 scripts/doctor.py --install")
        return 1
    print("ready." + (f"  ({len(warnings)} note(s) above)" if warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
