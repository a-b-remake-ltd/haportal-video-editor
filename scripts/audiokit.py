#!/usr/bin/env python3
"""Shared audio plumbing for music.py, bed.py, sfx.py and finish.py.

WHY a module of its own: the four sound scripts all need the same few things (decode any
file to a numpy buffer, write a wav, measure RMS / LUFS, read the ElevenLabs key, check
credits). Keeping one copy means the measurement that calibrates the bed is the very
same measurement that checks it.

Design choices:
  * Decoding and encoding go through ffmpeg pipes (f32le), never soundfile/librosa:
    ffmpeg is already required, reads every container (mp4, mp3, m4a, wav) and keeps
    the dependency list at numpy alone.
  * numpy is imported lazily inside the functions that need it, so importing this module
    never fails on a machine without it (finish.py only needs the ffmpeg helpers).
  * The ElevenLabs key is read from the PROJECT's .env or the environment, never printed,
    never written anywhere. No key is never an error by itself: every caller has a
    no-key path and says what the user gets instead.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

SR = 48000
API = "https://api.elevenlabs.io"


# ------------------------------------------------------------------ decode / encode
def read_audio(path, sr=SR, mono=True, start=0.0, dur=None):
    """Decode `path` to float32 numpy: shape (n,) when mono, (n, 2) otherwise."""
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start:
        cmd += ["-ss", f"{start:.4f}"]
    cmd += ["-i", path]
    if dur is not None:
        cmd += ["-t", f"{dur:.4f}"]
    cmd += ["-vn", "-ac", "1" if mono else "2", "-ar", str(sr), "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True, env=hfcfg.ffmpeg_env())
    if r.returncode:
        sys.exit(f"could not decode {path}:\n{r.stderr.decode(errors='replace')[-600:]}")
    x = np.frombuffer(r.stdout, dtype=np.float32).copy()
    return x if mono else x.reshape(-1, 2)


def write_wav(path, x, sr=SR, codec="pcm_s16le"):
    """Write mono or stereo float audio to a wav (always stereo out, 48 kHz by default).
    16-bit by default: every browser decodes it, and the renderer is a browser."""
    import numpy as np
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        x = np.stack([x, x], 1)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", "-",
           "-c:a", codec, path]
    r = subprocess.run(cmd, input=np.ascontiguousarray(x).tobytes(), capture_output=True,
                       env=hfcfg.ffmpeg_env())
    if r.returncode:
        sys.exit(f"could not write {path}:\n{r.stderr.decode(errors='replace')[-600:]}")


def ff(*args):
    """Run ffmpeg quietly; exit with its stderr on failure."""
    r = hfcfg.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *map(str, args)])
    if r.returncode:
        sys.exit("ffmpeg failed:\n" + r.stderr[-1200:])
    return r


def duration(path):
    try:
        return float(hfcfg.probe(path, "format=duration") or 0)
    except ValueError:
        return 0.0


# ------------------------------------------------------------------ measurement
def db(a):
    """RMS level of a buffer in dBFS (a tiny floor keeps silence finite)."""
    import numpy as np
    a = np.asarray(a, dtype=np.float64)
    return float(20 * np.log10(np.sqrt(np.mean(a ** 2)) + 1e-9)) if a.size else -180.0


def rms_frames(x, sr, hop):
    """RMS in dB per non-overlapping frame of `hop` seconds."""
    import numpy as np
    n = max(1, int(round(hop * sr)))
    m = len(x) // n
    if m == 0:
        return np.array([db(x)])
    f = x[:m * n].reshape(m, n).astype(np.float64)
    return 20 * np.log10(np.sqrt((f ** 2).mean(1)) + 1e-9)


def ebur128(src, chain=""):
    """(integrated LUFS, true peak dBTP) of src's audio through an optional filter chain.
    Never pass -v error: it suppresses the ebur128 summary."""
    af = (chain + "," if chain else "") + "ebur128=peak=true"
    out = hfcfg.run(["ffmpeg", "-nostdin", "-i", src, "-vn", "-af", af, "-f", "null", "-"]).stderr
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", out)
    tp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", out)
    return (float(i[-1]) if i else None), (float(tp[-1]) if tp else None)


def mean_volume(src):
    """volumedetect mean (dB) — what the SFX scaling formula is tuned on (§7.3)."""
    out = hfcfg.run(["ffmpeg", "-nostdin", "-i", src, "-vn", "-af", "volumedetect",
                     "-f", "null", "-"]).stderr
    m = re.search(r"mean_volume: (-?[0-9.]+) dB", out)
    return float(m.group(1)) if m else None


def speech_mask(v, sr, hop=0.01, rel_db=30.0, floor_db=12.0):
    """Per-frame 'is someone speaking' from the voice's energy.

    WHY: Whisper's word ends touch the next word's start, so the word table has no gaps
    in connected speech even where the speaker breathes. The audio does. A frame is
    speech when it is above BOTH (p95 − rel_db) and (noise floor + floor_db); a 30 ms
    hangover bridges the dips inside a word so plosives don't read as pauses."""
    import numpy as np
    f = rms_frames(v, sr, hop)
    if f.size == 0:
        return f.astype(bool), hop
    p95 = np.percentile(f, 95)
    floor = np.percentile(f, 8)
    thr = max(p95 - rel_db, floor + floor_db)
    m = f > thr
    k = max(1, int(round(0.03 / hop)))
    out = m.copy()
    for i in range(1, k + 1):          # hangover: speech stays "on" 30 ms after it stops
        out[i:] |= m[:-i]
    return out, hop


def silent_runs(mask, hop, t0, t1):
    """[(start, end)] silent stretches of the mask inside [t0, t1] seconds."""
    i0, i1 = max(0, int(t0 / hop)), min(len(mask), int(t1 / hop) + 1)
    runs, s = [], None
    for i in range(i0, i1):
        if not mask[i] and s is None:
            s = i
        elif mask[i] and s is not None:
            runs.append((s * hop, i * hop))
            s = None
    if s is not None:
        runs.append((s * hop, i1 * hop))
    return runs


# ------------------------------------------------------------------ project context
def load_words(path="src/words.json"):
    """[[s, e, w]] (transcribe.py) or [{"w","s","e"}] → sorted [(s, e, w)]."""
    if not os.path.exists(path):
        return []
    raw = json.load(open(path, encoding="utf-8"))
    out = []
    for w in raw:
        if isinstance(w, dict):
            out.append((float(w["s"]), float(w["e"]), str(w.get("w", ""))))
        else:
            out.append((float(w[0]), float(w[1]), str(w[2])))
    return sorted(out)


def outro_info(path="build/outro.json"):
    """(outro_start, composition_end) from build_index.py, or (None, None)."""
    if os.path.exists(path):
        o = json.load(open(path, encoding="utf-8"))
        return float(o["start"]), float(o["end"])
    return None, None


def voice_path(cli=None):
    """The RAW voice the bed and the SFX are calibrated against: --voice, media.json's
    aroll, else assets/aroll.mp4."""
    if cli:
        return cli
    if os.path.exists("media.json"):
        try:
            a = json.load(open("media.json", encoding="utf-8")).get("aroll")
            if isinstance(a, str) and os.path.exists(a):
                return a
        except ValueError:
            pass
    return "assets/aroll.mp4"


# ------------------------------------------------------------------ ElevenLabs
def api_key():
    """ELEVENLABS_API_KEY from the environment or the project's .env (cwd, then parents
    up to the home folder). Returns None when there is none. NEVER print it."""
    k = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if k:
        return k
    d = os.getcwd()
    home = os.path.expanduser("~")
    while True:
        p = os.path.join(d, ".env")
        if os.path.isfile(p):
            for line in open(p, encoding="utf-8", errors="replace"):
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith("ELEVENLABS_API_KEY="):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if v:
                        return v
        if d in (home, os.path.dirname(d)):
            return None
        d = os.path.dirname(d)


def credits(key):
    """(remaining, limit, tier) from GET /v1/user/subscription, or (None, None, error).
    ElevenLabs bills music and sound generation in the same credits as speech."""
    req = urllib.request.Request(API + "/v1/user/subscription", headers={"xi-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        return None, None, f"HTTP {e.code}: {e.read()[:200].decode(errors='replace')}"
    except (urllib.error.URLError, OSError) as e:
        return None, None, str(e)
    lim, used = d.get("character_limit"), d.get("character_count")
    if lim is None or used is None:
        return None, None, "subscription response had no character counts"
    return int(lim) - int(used), int(lim), d.get("tier")


def require_credits(key, need, what):
    """Stop BEFORE spending if the account cannot cover `need` credits (the house rule:
    find out up front, not halfway through a batch). Returns the remaining balance."""
    left, lim, tier = credits(key)
    if left is None:
        sys.exit(f"could not read the ElevenLabs balance ({tier}) — check the key in .env\n"
                 f"(nothing was generated)")
    print(f"  ElevenLabs credits: {left:,} left of {lim:,} ({tier}); {what} needs ≈{need:,}")
    if left < need:
        sys.exit(f"\n  ✗ not enough ElevenLabs credits for {what} (≈{need:,} needed, {left:,} left).\n"
                 f"    Nothing was generated. Top up, or use the no-key path (see references/sound.md).")
    return left


def post(path, body, out, key, retries=3, timeout=900):
    """POST json, write the returned audio bytes to `out`. Retries 429/5xx with a pause
    (parallel music requests hit 429 — callers run them one after another anyway)."""
    data = json.dumps(body).encode()
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(API + path, data=data,
                                     headers={"xi-api-key": key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                blob = r.read()
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            with open(out, "wb") as f:
                f.write(blob)
            return out
        except urllib.error.HTTPError as e:
            msg = e.read()[:400].decode(errors="replace")
            last = f"HTTP {e.code}: {msg}"
            if e.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, OSError) as e:
            last = str(e)
        if attempt < retries:
            print(f"    retry in 20 s ({last[:120]})", flush=True)
            time.sleep(20)
    sys.exit(f"ElevenLabs request failed ({path}): {last}")
