#!/usr/bin/env python3
"""Sound effects: the named library, custom per-video cues, and PLACEMENT off the words.
references/sound.md §SFX.

    python3 scripts/sfx.py library                      # the named set in assets/sfx/; with a key
                                                        #   it also UPGRADES synthesised stand-ins
    python3 scripts/sfx.py library --keep-synth         # …only fill what is missing
    python3 scripts/sfx.py library --from ~/my-sounds   # import files you own (name.wav/mp3)
    python3 scripts/sfx.py library --synth              # no key / no credits: synthesise all
    python3 scripts/sfx.py status                       # where every cue came from
    python3 scripts/sfx.py gen "heavy steel bars slamming shut, metallic clang, short" --name bars
    python3 scripts/sfx.py place cues.json --apply      # scale + slide off words → media.json
    python3 scripts/sfx.py check build/master_words.json  # which cue sits on each misheard word
    python3 scripts/sfx.py selftest                     # negative tests (no credits, no files)

LIBRARY — every cue is a 48 kHz stereo wav, lead-in silence trimmed, peak −6 dBFS, ≤ 1.3 s
(logo_sting ≈ 3.5 s), so any volume number means the same thing for every cue. A cue comes
from, in order: --from DIR (files the user owns) → ElevenLabs sound-generation (when
ELEVENLABS_API_KEY is set; credits checked first) → a synthesised fallback built here from
sines and noise (free, owned by nobody, plainer than a generated cue).

PROVENANCE — assets/sfx/library.json records, per cue, its kind (synth / elevenlabs /
user), prompt and sha256. WHY: `doctor --install` synthesises the set at install time,
when there is no key yet, and a project copies those stand-ins in; `library` used to fill
only MISSING cues, so a user who added a key later kept the synthesised set forever.
Now, with a key, `library` UPGRADES every synth stand-in to a generated cue (credits
checked first; --keep-synth opts out). A copied file with no entry is recognised by its
hash against the skill's own set; a file nobody recorded is "unknown" and never touched.
After an upgrade, re-run the build: index.html clip durations were measured on the old
files (the script names the cues index.html uses).

PLACEMENT (§7.3) — cues are {"name", "t", "base_vol", "exempt"}:
* Scale to THIS voice: k = clamp(10^((voiceMean_dB + 16.1)/20), 0.03, 1), voiceMean from
  volumedetect on the RAW voice. Presets were tuned on a −16 dB-mean voice; a quiet AI
  avatar gets quiet effects and the master lifts everything together. vol = base × k.
* An effect must not sit on a word (it swallows short words: "סבתא" became "ספטו").
  For a cue inside a word: a measured pause ≥ 0.14 s before the word → start at
  speechOnset − 0.1; else a pause ≥ 0.14 s after it → start at speechEnd + 0.02; else
  halve its volume. ONE move, never a chain of slides (that is how a cue drifted onto a
  key word), and never more than 0.6 s.
* "Measured": Whisper's word ends touch the next word's start, so the word table shows
  no pauses in connected speech. The pause lengths and edges come from the voice's
  energy (audiokit.speech_mask), the word table only says WHICH word a cue hit.
* Exempt cues (bars, shatter, stamps, hook whooshes, outro) stay on their beat — keep
  them moderate (base ≤ 0.45).
"""
import datetime
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402
import audiokit as ak  # noqa: E402

SFX_DIR = "assets/sfx"
SR = ak.SR
GEN_URL = "/v1/sound-generation"
# The GATE estimates high (website rate) so a batch never starts that it cannot finish;
# the REPORT uses the measured API rate: 9 cues × 1.3 s cost 126 credits (≈ 11/s).
CREDITS_PER_SEC = 40
CREDITS_PER_SEC_EST = 12
MAX_LEN = 1.3
PEAK_DB = -6.0

# name: (prompt, seconds). Prompts are concrete and short ("short", "clean"): the model
# pads vague prompts with ambience that muddies a voice mix.
LIBRARY = {
    "whoosh_impact": ("cinematic fast whoosh into a deep sub bass impact hit, modern trailer, clean", 1.3),
    "soft_whoosh": ("soft airy whoosh transition, short, subtle", 1.3),
    "whoosh_low": ("low deep bass whoosh passing by, smooth, short, clean", 1.3),
    "swap_pop": ("playful cartoon swap pop with a tiny swoosh, short, comedic but premium", 1.3),
    "pop": ("single soft bubble pop, clean user interface sound, short", 1.3),
    "click": ("single crisp mouse click, clean, close mic, short", 1.3),
    "ding": ("clean modern UI notification ding, short, premium", 1.3),
    "glass_snap": ("short crisp snap of a plastic ring breaking, light comedic, clean", 1.3),
    "riser_short": ("short tension riser one second, airy, modern, ends abruptly", 1.3),
    "typing": ("soft fast keyboard typing on a laptop, short burst, clean, close mic", 1.3),
    "message": ("phone text message received sound, soft modern pop, short", 1.3),
    "comment_ping": ("soft social media comment notification ping, bright, short", 1.3),
    "page_flip": ("rapid calendar pages flipping fast, short, paper flutter", 1.3),
    "portal_suck": ("magical portal suction whoosh, energy pulled into a gate, short, clean", 1.3),
    "logo_sting": ("premium tech logo reveal sting, deep warm hit with a bright shimmering tail, "
                   "cinematic, clean", 3.5),
    "bars": ("heavy steel prison cell bars slamming shut, metallic clang, short", 1.3),
    "snap": ("thin strings snapping, quick twang, short", 1.3),
    "bricks": ("light building blocks stacking click, short, satisfying", 1.3),
    "shatter": ("metal bars breaking apart with a bright shimmer, short", 1.3),
    "clock": ("single soft clock tick tock, short", 1.3),
}


# ====================================================================== processing
# One-shot cues: the generator often returns a SERIES ("single mouse click" came back as
# eight clicks). Keep the first event only — a cue must have one attack to be placeable.
ONESHOT = {"click", "pop", "ding", "comment_ping", "glass_snap", "swap_pop", "message"}


def first_event(x):
    """Cut after the first event: at the first 40 ms stretch 30 dB under its peak."""
    import numpy as np
    m = np.abs(x).max(1) if x.ndim == 2 else np.abs(x)
    hop = int(0.01 * SR)
    e = ak.rms_frames(m, SR, 0.01)
    if not e.size:
        return x
    pk_i = int(np.argmax(e[:int(0.3 / 0.01)] if len(e) > 30 else e))
    floor = e[pk_i] - 30
    for i in range(pk_i + 1, len(e) - 4):
        if (e[i:i + 4] < floor).all():
            return x[:(i + 2) * hop]
    return x


def finish(x, max_len=MAX_LEN, oneshot=False):
    """Trim lead-in silence (first sample within 40 dB of the peak, 5 ms pre-roll), cap the
    length with a 60 ms fade, fade the head in over 3 ms, normalise to −6 dBFS peak."""
    import numpy as np
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        mono = np.abs(x).max(1)
    else:
        mono = np.abs(x)
        x = np.stack([x, x], 1)
    pk = mono.max() or 1.0
    on = np.nonzero(mono > pk * 10 ** (-40 / 20))[0]
    s = max(0, int(on[0]) - int(0.005 * SR)) if on.size else 0
    x = x[s:]
    if oneshot:
        x = first_event(x)
    n = int(max_len * SR)
    if len(x) > n:
        x = x[:n]
        f = int(0.06 * SR)
        x[-f:] *= np.linspace(1, 0, f)[:, None]
    f = int(0.003 * SR)
    x[:f] *= np.linspace(0, 1, f)[:, None]
    # drop a near-silent tail so the clip's duration is its audible length
    m = np.abs(x).max(1)
    alive = np.nonzero(m > (m.max() or 1) * 10 ** (-50 / 20))[0]
    if alive.size:
        x = x[:alive[-1] + int(0.02 * SR)]
    return x / (np.abs(x).max() or 1.0) * 10 ** (PEAK_DB / 20)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(sdir):
    p = os.path.join(sdir, "library.json")
    try:
        return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    except ValueError:
        return {}


def record(sdir, name, source, prompt=None, seconds=None, origin=None):
    """Write one cue's provenance (kind + hash) into <sdir>/library.json."""
    lib = load_manifest(sdir)
    path = os.path.join(sdir, f"{name}.wav")
    lib[name] = {"source": source, "prompt": prompt, "seconds": seconds,
                 "made": datetime.date.today().isoformat(),
                 "sha256": sha256(path) if os.path.exists(path) else None}
    if origin:
        lib[name]["from"] = origin
    json.dump(lib, open(os.path.join(sdir, "library.json"), "w", encoding="utf-8"),
              indent=1, ensure_ascii=False)


def kind_of(source):
    """synth / elevenlabs / user / unknown, from a manifest 'source' (old entries said
    'imported:<file>' for a user's file)."""
    s = (source or "").lower()
    if s.startswith("synth"):
        return "synth"
    if s.startswith("elevenlabs"):
        return "elevenlabs"
    if s.startswith(("imported", "user")):
        return "user"
    return "unknown"


def provenance(sdir, names=None, skill_sfx=None):
    """{name: kind} for every cue wav present in sdir (default: the named LIBRARY).

    No entry, or an entry whose hash no longer matches (someone replaced the file)?
    A byte-identical copy of the skill's own cue inherits ITS kind (setup_assets.py
    synthesises the skill's set; a project copies it in without the manifest).
    Anything else is 'unknown' — possibly the user's, so never overwritten."""
    skill_sfx = skill_sfx or os.path.join(hfcfg.SKILL_DIR, "assets", "sfx")
    lib = load_manifest(sdir)
    same = os.path.abspath(skill_sfx) == os.path.abspath(sdir)
    slib = {} if same else load_manifest(skill_sfx)
    out = {}
    for n in names or list(LIBRARY):
        p = os.path.join(sdir, f"{n}.wav")
        if not os.path.exists(p):
            continue
        e = lib.get(n)
        if e and (not e.get("sha256") or e["sha256"] == sha256(p)):
            out[n] = kind_of(e.get("source"))
            continue
        sp = os.path.join(skill_sfx, f"{n}.wav")
        if same and not e:
            out[n] = "synth"        # the skill's own folder: setup_assets.py made it (nothing ships)
        elif not same and os.path.exists(sp) and sha256(sp) == sha256(p):
            se = slib.get(n)
            # the skill's set is built by setup_assets.py: synthesised unless recorded
            out[n] = kind_of(se.get("source")) if se else "synth"
        else:
            out[n] = "unknown"
    return out


def store(name, x, sdir, source, prompt=None, max_len=MAX_LEN, origin=None):
    path = os.path.join(sdir, f"{name}.wav")
    y = finish(x, max_len, oneshot=name in ONESHOT)
    ak.write_wav(path, y)
    record(sdir, name, source, prompt, round(len(y) / SR, 3), origin)
    return path, len(y) / SR


# ====================================================================== synthesis
# The no-key fallbacks. Plain sines, noise and envelopes — deterministic (fixed seeds),
# royalty-free, and shaped like the named cue so the placement rules still make sense.
def _t(d):
    import numpy as np
    return np.arange(int(d * SR)) / SR


def _noise(d, seed):
    import numpy as np
    return np.random.default_rng(seed).uniform(-1, 1, int(d * SR))


def _band(x, lo, hi):
    """Brick-wall band-pass in the frequency domain (fine for one-shot cues)."""
    import numpy as np
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    X[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(X, len(x))


def _sweep_noise(d, f0, f1, seed, width=0.6):
    """Noise whose band centre glides f0 → f1: 24 short band-passed slices, crossfaded."""
    import numpy as np
    n = int(d * SR)
    out = np.zeros(n)
    k = 24
    seg = n // k
    win = np.hanning(seg * 2)
    src = _noise(d + 0.1, seed)
    for i in range(k):
        c = f0 * (f1 / f0) ** (i / (k - 1))
        a = max(0, i * seg - seg // 2)
        b = min(n, a + 2 * seg)
        out[a:b] += _band(src[a:b], c * (1 - width), c * (1 + width)) * win[:b - a]
    return out


def _tone(d, freqs, decay, gains=None, attack=0.002):
    import numpy as np
    t = _t(d)
    env = np.minimum(1, t / attack) * np.exp(-t / decay)
    y = np.zeros_like(t)
    for i, f in enumerate(freqs):
        y += np.sin(2 * np.pi * f * t) * (gains[i] if gains else 1.0)
    return y * env


def _boom(d=0.9, f0=70, f1=32, decay=0.25):
    import numpy as np
    t = _t(d)
    f = f1 + (f0 - f1) * np.exp(-t * 6)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / decay)


def _whoosh(d, f0, f1, seed=3):
    import numpy as np
    x = _sweep_noise(d, f0, f1, seed)
    t = np.linspace(0, 1, len(x))
    return x * np.sin(np.pi * np.minimum(1, t * 1.1)) ** 1.6


def _mix(*parts):
    """[(signal, start_seconds, gain)] → one buffer."""
    import numpy as np
    n = max(int(s * SR) + len(x) for x, s, _ in parts)
    y = np.zeros(n)
    for x, s, g in parts:
        a = int(s * SR)
        y[a:a + len(x)] += x * g
    return y


def _click(d=0.03, seed=33, lo=1500, hi=9000):
    import numpy as np
    t = _t(d)
    return _band(_noise(d, seed), lo, hi) * np.exp(-t / 0.004)


def synth(name):
    import numpy as np
    if name == "whoosh_impact":
        return _mix((_whoosh(0.55, 500, 2500, 3), 0, 0.8), (_boom(0.9), 0.42, 1.0))
    if name == "soft_whoosh":
        return _whoosh(0.6, 700, 3500, 4) * 0.7
    if name == "whoosh_low":
        return _mix((_whoosh(0.55, 900, 160, 5), 0, 1.0), (_boom(0.4, 70, 45, 0.15), 0.05, 0.4))
    if name == "swap_pop":
        return _mix((_whoosh(0.18, 1500, 4500, 6), 0, 0.5), (synth("pop"), 0.12, 1.0))
    if name == "pop":
        t = _t(0.18)
        f = 250 + 900 * (t / 0.18) ** 0.45
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.minimum(1, t / 0.003) * np.exp(-t / 0.035)
    if name == "click":
        return _click()
    if name == "ding":
        return _tone(1.1, [1568, 2352, 3136], 0.28, [1, 0.5, 0.25])
    if name == "glass_snap":
        return _mix((_click(0.04, 9, 2500, 12000), 0, 1.0),
                    (_tone(0.35, [2840, 4130, 5210], 0.06, [1, .7, .5]), 0.005, 0.5))
    if name == "riser_short":
        t = _t(0.95)
        u = t / t[-1]
        y = _sweep_noise(0.95, 300, 6000, 8) * 0.8 + np.sin(2 * np.pi * np.cumsum(120 * 6 ** u) / SR) * 0.4
        return y * u ** 1.7
    if name == "typing":
        rng = np.random.default_rng(12)
        parts, s = [], 0.0
        while s < 1.1:
            parts.append((_click(0.035, int(rng.integers(1e6)), 900, 6000), s, float(rng.uniform(.5, 1))))
            s += float(rng.uniform(0.06, 0.13))
        return _mix(*parts)
    if name == "message":
        return _mix((_tone(0.18, [880, 1760], 0.05, [1, .3]), 0, 1.0),
                    (_tone(0.3, [1320, 2640], 0.08, [1, .3]), 0.09, 1.0))
    if name == "comment_ping":
        return _tone(0.6, [2093, 4186], 0.12, [1, .35])
    if name == "page_flip":
        rng = np.random.default_rng(14)
        return _mix(*[(_whoosh(0.09, 1500, 5000, 20 + i) * np.hanning(int(0.09 * SR)), i * 0.11,
                       float(rng.uniform(.6, 1))) for i in range(8)])
    if name == "portal_suck":
        w = _whoosh(0.9, 4000, 300, 15)[::-1]
        t = _t(0.9)
        tone = np.sin(2 * np.pi * np.cumsum(200 + 600 * (t / t[-1]) ** 2) / SR) * (t / t[-1]) ** 2
        return w + tone * 0.3
    if name == "logo_sting":
        bell = _tone(3.5, [523.25, 659.25, 783.99, 987.77, 1174.66], 0.9, [1, .8, .7, .5, .35],
                     attack=0.01)
        shimmer = _sweep_noise(3.5, 6000, 9000, 16) * np.exp(-_t(3.5) / 0.7) * 0.15
        return _mix((_boom(1.4, 60, 30, 0.45), 0, 1.0), (bell, 0.02, 0.5), (shimmer, 0.02, 1.0))
    if name == "bars":
        clang = _tone(1.2, [220, 563, 921, 1340, 1877, 2611], 0.22, [1, .8, .7, .55, .4, .3])
        return _mix((_click(0.05, 2, 800, 8000), 0, 1.0), (clang, 0, 0.6), (_boom(0.6, 90, 40, 0.15), 0, 0.7))
    if name == "snap":
        t = _t(0.5)
        tw = np.sin(2 * np.pi * np.cumsum(330 * (1 + 0.03 * np.sin(2 * np.pi * 7 * t))) / SR) * np.exp(-t / 0.09)
        return _mix((_click(0.02, 5, 2000, 10000), 0, 1.0), (tw, 0.005, 0.6))
    if name == "bricks":
        knock = lambda f, s: _mix((_tone(0.12, [f, f * 1.5], 0.025), 0, 1), (_click(0.02, s, 600, 4000), 0, .5))
        return _mix((knock(620, 1), 0, 1.0), (knock(720, 2), 0.09, 0.9), (knock(860, 3), 0.18, 1.0))
    if name == "shatter":
        rng = np.random.default_rng(17)
        pings = [(_tone(0.5, [float(rng.uniform(2500, 7000))], 0.08), float(rng.uniform(0, 0.25)),
                  float(rng.uniform(.2, .5))) for _ in range(14)]
        burst = _band(_noise(0.4, 18), 2000, 12000) * np.exp(-_t(0.4) / 0.07)
        return _mix((burst, 0, 1.0), (_boom(0.5, 80, 40, 0.12), 0, 0.5), *pings)
    if name == "clock":
        return _mix((_click(0.03, 21, 1800, 5000), 0, 1.0), (_tone(0.08, [2000], 0.012), 0, 0.5),
                    (_click(0.03, 22, 900, 3000), 0.5, 0.8), (_tone(0.08, [1200], 0.015), 0.5, 0.5))
    # an unknown custom name without a key: a neutral soft pop, said out loud by the caller
    return synth("pop")


# ====================================================================== library
def _match(src_dir, name):
    """A file in src_dir named name.ext or name_<n>.ext (the generator's numbered takes)."""
    if not src_dir or not os.path.isdir(src_dir):
        return None
    pat = re.compile(rf"^{re.escape(name)}(_\d+)?\.(wav|mp3|m4a|aif|aiff|flac|ogg)$", re.I)
    hits = sorted(f for f in os.listdir(src_dir) if pat.match(f))
    return os.path.join(src_dir, hits[0]) if hits else None


def generate(key, name, prompt, seconds, influence, sdir):
    raw = os.path.join("build", "sfx_raw", f"{name}.mp3")
    ak.post(GEN_URL, {"text": prompt, "duration_seconds": seconds,
                      "prompt_influence": influence}, raw, key)
    return store(name, ak.read_audio(raw, mono=False), sdir, "elevenlabs", prompt,
                 max_len=max(MAX_LEN, seconds + 0.5) if seconds > MAX_LEN else MAX_LEN)


def index_uses(names, index="index.html"):
    """The cue names among `names` that index.html plays (its clip durations were
    measured on the files as they were at build time)."""
    if not os.path.exists(index):
        return []
    html = open(index, encoding="utf-8", errors="replace").read()
    return [n for n in names if re.search(rf"/{re.escape(n)}\.wav[\"']", html)]


def cmd_library(a):
    sdir = a.dir
    os.makedirs(sdir, exist_ok=True)
    names = a.names.split(",") if a.names else list(LIBRARY)
    prov = provenance(sdir, names)
    missing = [n for n in names if n not in prov]
    stand_ins = [n for n in names if prov.get(n) == "synth"]
    key = None if a.synth else ak.api_key()
    # what may be (re)made: everything with --force; else the missing cues, plus the
    # synthesised stand-ins when something better is available (a key, or user files)
    if a.force:
        todo = list(names)
    else:
        todo = missing + ([] if a.keep_synth else
                          [n for n in stand_ins if key or any(_match(d, n) for d in a.src)])
    if not todo:
        kinds = {k: sum(1 for v in prov.values() if v == k) for k in set(prov.values())}
        print(f"  library complete: {len(names)} cues in {sdir}/ ("
              + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())) + ")")
        if stand_ins and not key and not a.synth:
            print(f"  {len(stand_ins)} are synthesised stand-ins — with an ElevenLabs key, "
                  f"sfx.py library upgrades them (or --from DIR with files you own)")
        elif stand_ins and a.keep_synth:
            print(f"  {len(stand_ins)} synthesised stand-ins kept (--keep-synth)")
        return 0
    done, upgraded = {}, []
    for n in list(todo):                        # 1. files the user owns
        f = next((m for m in (_match(d, n) for d in a.src) if m), None)
        if f:
            p, d = store(n, ak.read_audio(f, mono=False), sdir, "user",
                         max_len=4.0 if n == "logo_sting" else MAX_LEN,
                         origin=os.path.basename(f))
            done[n] = f"imported {os.path.basename(f)} ({d:.2f}s)"
            upgraded += [n] if n in stand_ins else []
            todo.remove(n)
    if todo and key:                            # 2. ElevenLabs, credits first
        secs = sum(LIBRARY.get(n, ("", MAX_LEN))[1] for n in todo)
        need = int(secs * CREDITS_PER_SEC)
        what = (f"{len(todo)} sound effect(s)"
                + (f", {len([n for n in todo if n in stand_ins])} of them upgrading "
                   f"synthesised stand-ins" if any(n in stand_ins for n in todo) else ""))
        before = ak.require_credits(key, need, what)
        made = 0.0
        for n in list(todo):
            prompt, sec = LIBRARY.get(n, (n.replace("_", " ") + ", short, clean", MAX_LEN))
            print(f"  generating {n}…", flush=True)
            p, d = generate(key, n, prompt, sec, a.influence, sdir)
            made += sec
            done[n] = f"ElevenLabs ({d:.2f}s)" + ("  ← was a synth stand-in" if n in stand_ins else "")
            upgraded += [n] if n in stand_ins else []
            todo.remove(n)
        # never "credits used: 0": the balance lags (see audiokit.spend_text)
        ak.spend_report(key, before, int(round(made * CREDITS_PER_SEC_EST)),
                        f"sfx: {len(done)} cue(s), {made:.1f}s requested")
    todo = [n for n in todo if n not in prov or a.force]   # 3. synth: only what is MISSING
    if todo:
        if not key and not a.synth:
            print("  no ElevenLabs key — synthesising the missing cues (plainer than generated "
                  "ones; import your own with --from DIR any time)")
        for n in todo:
            p, d = store(n, synth(n), sdir, "synth", max_len=4.0 if n == "logo_sting" else MAX_LEN)
            done[n] = f"synthesised ({d:.2f}s)" + ("" if n in LIBRARY else "  ! unknown name → soft pop")
    for n, how in done.items():
        print(f"  {n:14s} {how}")
    after = provenance(sdir, names)
    kinds = {k: sum(1 for v in after.values() if v == k) for k in set(after.values())}
    print(f"  ✓ {len(names)} cues in {sdir}/ (48 kHz stereo, peak {PEAK_DB:.0f} dBFS): "
          + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
          + f" — provenance in {sdir}/library.json")
    if upgraded:
        print(f"  upgraded {len(upgraded)} synthesised stand-in(s): {', '.join(upgraded)}")
        used = index_uses(upgraded)
        if used:
            print(f"  ! index.html plays {', '.join(used)} with clip durations measured on the "
                  f"OLD files — re-run the build (scenes / build_index) before the next render")
    return 0


def cmd_gen(a):
    name = re.sub(r"[^a-z0-9_]+", "_", a.name.lower()).strip("_")
    out = os.path.join(a.dir, f"{name}.wav")
    if os.path.exists(out) and not a.force:
        sys.exit(f"{out} exists — --force to regenerate")
    key = ak.api_key()
    if not key:
        p, d = store(name, synth(name), a.dir, "synth")
        print(f"  no ElevenLabs key: '{name}' synthesised as a fallback ({d:.2f}s) → {p}\n"
              f"  (a library name gets its shaped synth; any other name a soft pop). For a real "
              f"custom cue, import a file you own: sfx.py library --from DIR --names {name}")
        return 0
    before = ak.require_credits(key, int(a.duration * CREDITS_PER_SEC), f"'{name}'")
    p, d = generate(key, name, a.prompt, a.duration, a.influence, a.dir)
    print(f"  ✓ {name} ({d:.2f}s) → {p}")
    ak.spend_report(key, before, int(round(a.duration * CREDITS_PER_SEC_EST)), f"sfx gen '{name}'")
    return 0


def cmd_status(a):
    """Where every cue in the folder came from — the same check doctor.py runs."""
    names = sorted(set(LIBRARY) | {os.path.splitext(f)[0] for f in os.listdir(a.dir)
                                   if f.endswith(".wav")}) if os.path.isdir(a.dir) else []
    prov = provenance(a.dir, names)
    if not prov:
        print(f"  no cues in {a.dir}/ — run: sfx.py library")
        return 1
    groups = [(k, [n for n in names if prov.get(n) == k and (k != "unknown" or n in LIBRARY)])
              for k in ("elevenlabs", "user", "synth", "unknown")]
    groups.append(("unrecorded", [n for n in names if prov.get(n) == "unknown" and n not in LIBRARY]))
    for k, cues in groups:          # unrecorded = made by another script (outro_*) or by hand
        if cues:
            print(f"  {k:10s} {len(cues):2d}  {', '.join(cues)}")
    miss = [n for n in LIBRARY if n not in prov]
    if miss:
        print(f"  missing    {len(miss):2d}  {', '.join(miss)}")
    stand = [n for n in LIBRARY if prov.get(n) == "synth"]
    if stand:
        print(f"  {len(stand)} named cue(s) are synthesised stand-ins — "
              + ("run: sfx.py library  (a key is set: it upgrades them)" if ak.api_key()
                 else "with an ElevenLabs key, sfx.py library upgrades them"))
    return 0


# ====================================================================== placement
def _speech(voice):
    v = ak.read_audio(voice)
    mask, hop = ak.speech_mask(v, SR)
    return v, mask, hop


def _on_speech(mask, hop, t, span=0.08):
    """Fraction of the cue's attack window [t, t+span] the voice is speaking in."""
    i0, i1 = int(t / hop), int((t + span) / hop) + 1
    seg = mask[max(0, i0):max(0, min(len(mask), i1))]
    return float(seg.mean()) if len(seg) else 0.0


def _longest(runs):
    return max(runs, key=lambda r: r[1] - r[0]) if runs else None


def place(cues, words, voice, end=None, sdir=SFX_DIR, voice_mean=None):
    """Scale cues to the voice and move them off words (§7.3).

    cues: [{"name", "t", "base_vol" (or "vol"), "exempt"}] or [t, "name[!]", base_vol]
          ("!" = exempt).
    Returns (entries for media.json audio.sfx, report dict)."""
    vm = voice_mean if voice_mean is not None else ak.mean_volume(voice)
    k = max(0.03, min(1.0, 10 ** (((vm if vm is not None else -16.1) + 16.1) / 20)))
    _, mask, hop = _speech(voice)
    end = end or ak.duration(voice)
    W = list(words)
    entries, moves, lengths = [], [], {}

    def inside(t):
        return next((i for i, w in enumerate(W) if w[0] - 0.04 <= t < w[1] - 0.02), None)

    for n, c in enumerate(cues):
        if isinstance(c, (list, tuple)):
            c = {"t": c[0], "name": str(c[1]).rstrip("!"), "base_vol": c[2],
                 "exempt": str(c[1]).endswith("!")}
        name, t0 = c["name"], float(c["t"])
        base = float(c.get("base_vol", c.get("vol", 0.2)))
        exempt = bool(c.get("exempt"))
        src = os.path.join(sdir, f"{name}.wav")
        if not os.path.exists(src):
            raise SystemExit(f"{src} missing — run: sfx.py library (or sfx.py gen ... --name {name})")
        if name not in lengths:
            lengths[name] = ak.duration(src)
        t, vol, what = t0, base * k, "clear"
        if exempt:
            what = "exempt (kept on the beat)"
            if base > 0.45:
                what += f"  ! base {base} > 0.45 — keep impacts moderate"
        else:
            i = inside(t0)
            if i is not None:
                w = W[i]
                prev_e = W[i - 1][1] if i else 0.0
                next_s = W[i + 1][0] if i + 1 < len(W) else end
                before = _longest(ak.silent_runs(mask, hop, max(0, prev_e - 0.15), w[0] + 0.12))
                after = _longest(ak.silent_runs(mask, hop, w[1] - 0.12, min(end, next_s + 0.15)))
                gb = before[1] - before[0] if before else 0.0
                ga = after[1] - after[0] if after else 0.0
                if gb >= 0.14 and abs(before[1] - 0.1 - t0) <= 0.6:
                    t, what = before[1] - 0.1, f"in '{w[2]}' → 0.1 s before it (pause {gb:.2f}s)"
                elif ga >= 0.14 and abs(after[0] + 0.02 - t0) <= 0.6:
                    t, what = after[0] + 0.02, f"in '{w[2]}' → after it (pause {ga:.2f}s)"
                else:
                    vol *= 0.5
                    what = (f"in '{w[2]}', no pause ≥ 0.14 s within reach "
                            f"(before {gb:.2f}, after {ga:.2f}) → volume halved")
        t = max(0.0, t)
        dur = round(min(lengths[name], end - t), 3)
        if dur <= 0.02:
            what += "  ! starts after the end — dropped"
            moves.append({"name": name, "t0": t0, "t": round(t, 3), "what": what})
            continue
        entries.append({"id": f"sfx{n:02d}_{name}", "src": src, "start": round(t, 3),
                        "duration": dur, "volume": round(vol, 4)})
        w_now = inside(t)
        moves.append({"name": name, "t0": round(t0, 3), "t": round(t, 3), "base": base,
                      "volume": round(vol, 4), "exempt": exempt, "what": what,
                      "word_at_onset": W[w_now][2] if w_now is not None else None,
                      "speech_at_onset": round(_on_speech(mask, hop, t), 2)})
    return entries, {"voice_mean_db": vm, "k": round(k, 4), "cues": moves}


def cmd_place(a):
    hfcfg.ensure_deps(["numpy"])
    cues = json.load(open(a.cues, encoding="utf-8"))
    if isinstance(cues, dict):
        cues = cues.get("cues") or cues.get("sfx") or []
    words = ak.load_words(a.words)
    if not words:
        print(f"  ! no words in {a.words} — cues cannot be checked against speech")
    voice = ak.voice_path(a.voice)
    _, end = ak.outro_info()
    if end is None and os.path.exists("build/bed_report.json"):     # the bed knows END too
        end = json.load(open("build/bed_report.json", encoding="utf-8")).get("end")
    end = a.end or end or ak.duration(voice)
    entries, rep = place(cues, words, voice, end, a.dir)
    print(f"  voice mean {rep['voice_mean_db']} dB → k = {rep['k']} (every base volume × k)")
    bad = 0
    for m in rep["cues"]:
        mv = "" if abs(m["t"] - m["t0"]) < 1e-3 else f" → {m['t']:6.2f}"
        on = m.get("speech_at_onset", 0)
        flag = ""
        if not m.get("exempt") and on > 0.5 and "halved" not in m["what"]:
            flag, bad = "   ✗ attack on speech", bad + 1
        print(f"  {m['name']:14s} {m['t0']:6.2f}{mv:9s} vol {m.get('volume', 0):<7} {m['what']}{flag}")
    os.makedirs("build", exist_ok=True)
    json.dump({"entries": entries, **rep}, open("build/sfx_placed.json", "w", encoding="utf-8"),
              indent=1, ensure_ascii=False)
    print(f"\n  {len(entries)} cues → build/sfx_placed.json"
          f"{'' if not bad else f'   ✗ {bad} non-exempt cue(s) still attack on speech'}")
    if a.apply:
        if not os.path.exists("media.json"):
            sys.exit("  media.json not found — paste build/sfx_placed.json 'entries' into audio.sfx")
        m = json.load(open("media.json", encoding="utf-8"))
        m.setdefault("audio", {})["sfx"] = entries
        json.dump(m, open("media.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        print("  ✓ media.json audio.sfx replaced")
    return 1 if bad else 0


def _attrs(tag):
    return dict(re.findall(r'([\w-]+)="([^"]*)"', tag))


def cues_from_index(index="index.html"):
    """SFX clips actually in the composition: every <audio> whose class or src says sfx.
    WHY: scene-path cues (scenes.py / moments.py / outro.py) never pass through
    `sfx.py place`, so build/sfx_placed.json may not exist — index.html is what was
    rendered."""
    if not os.path.exists(index):
        return []
    out = []
    for tag in re.findall(r"<audio\b[^>]*>", open(index, encoding="utf-8", errors="replace").read()):
        at = _attrs(tag)
        src, cls = at.get("src", ""), at.get("class", "")
        if "sfx" not in cls.split() and "/sfx/" not in src:
            continue
        try:
            st = float(at["data-start"])
        except (KeyError, ValueError):
            continue
        try:
            du = float(at.get("data-duration") or "nan")
        except ValueError:
            du = float("nan")
        if du != du:                                       # NaN: measure the file
            du = ak.duration(src) if os.path.exists(src) else 0.5
        out.append({"id": at.get("id") or os.path.basename(src), "src": src,
                    "start": st, "duration": du})
    return out


def cues_from_scenes(path="build/scenes.json", sdir=SFX_DIR):
    if not os.path.exists(path):
        return []
    try:
        sc = json.load(open(path, encoding="utf-8")).get("sfx", [])
    except (ValueError, AttributeError):
        return []
    out = []
    for c in sc:
        src = os.path.join(sdir, f"{c.get('name')}.wav")
        out.append({"id": c.get("id") or c.get("name"), "src": src, "start": float(c["start"]),
                    "duration": ak.duration(src) if os.path.exists(src) else 0.5})
    return out


def load_cues(placed=None, index="index.html", scenes="build/scenes.json", sdir=SFX_DIR):
    """(cues, where) — an explicit --placed file, else index.html (what was rendered),
    else build/sfx_placed.json, else build/scenes.json. Never raises: no list at all
    gives ([], reason) and the check runs on the words alone."""
    if placed:
        if not os.path.exists(placed):
            return [], f"{placed} not found"
        return json.load(open(placed, encoding="utf-8")).get("entries", []), placed
    c = cues_from_index(index)
    if c:
        return c, f"{index} audio clips"
    if os.path.exists("build/sfx_placed.json"):
        return (json.load(open("build/sfx_placed.json", encoding="utf-8")).get("entries", []),
                "build/sfx_placed.json")
    c = cues_from_scenes(scenes, sdir)
    if c:
        return c, scenes
    return [], f"no SFX list ({index}, build/sfx_placed.json, {scenes})"


def cmd_check(a):
    """Intelligibility (§10.6): diff the master's re-transcription against the source words
    and name the cue sitting on every changed word. Halving a cue that could not move is
    the rule, and sometimes it is not enough ("וגרוק" was heard as "ודרוק" under a
    halved pop) — this is where that shows up."""
    import difflib

    def n(w):
        return re.sub(r"[^\w]", "", w)
    for f in (a.heard, a.words):
        if not os.path.exists(f):
            print(f"  ✗ {f} not found — re-transcribe the master first: transcribe.py "
                  f"renders/final.mp4 --words {a.heard}")
            return 2
    src, heard = ak.load_words(a.words), ak.load_words(a.heard)
    placed, where = load_cues(a.placed, sdir=a.dir)
    print(f"  SFX cues: {len(placed)} from {where}")
    A, B = [n(w[2]) for w in src], [n(w[2]) for w in heard]
    sm = difflib.SequenceMatcher(None, A, B, autojunk=False)
    match = sum(b.size for b in sm.get_matching_blocks()) / max(1, len(A))
    print(f"  word match {100 * match:.1f}% (target ≥ 97%)")
    bad = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal" or i1 == i2:
            continue
        t0, t1 = src[i1][0], src[i2 - 1][1]
        hits = [e for e in placed if e["start"] < t1 + 0.05 and e["start"] + e["duration"] > t0 - 0.1]
        cue = ", ".join(f"{e['id']}@{e['start']:.2f}" for e in hits) or "no cue — check the bed level there"
        print(f"  {t0:6.2f}s  {' '.join(w[2] for w in src[i1:i2])}  →  "
              f"{' '.join(w[2] for w in heard[j1:j2]) or '(missing)'}   [{cue}]")
        bad += bool(hits)
    if bad:
        print("  move those cues to the nearest phrase boundary (or drop them) and re-render; "
              "a transcriber's spelling variant with no cue on it is not an SFX problem")
    return 0 if match >= 0.97 else 1


def selftest():
    """Negative tests — `sfx.py selftest`. Temp folders, no credits. Exit 1 on failure."""
    import shutil
    import tempfile
    fails = []
    tmp = tempfile.mkdtemp(prefix="sfx_selftest_")
    cwd = os.getcwd()
    try:
        skill, proj = os.path.join(tmp, "skill"), os.path.join(tmp, "proj", "assets", "sfx")
        os.makedirs(skill)
        os.makedirs(proj)
        for nm in ("pop", "click", "ding"):
            store(nm, synth(nm), skill, "synth")
        store("bars", synth("bars"), skill, "elevenlabs", "bars prompt")
        # a project copies the skill's files WITHOUT the manifest (the real failure)
        for nm in ("pop", "click", "bars"):
            shutil.copy2(os.path.join(skill, f"{nm}.wav"), proj)
        ak.write_wav(os.path.join(proj, "ding.wav"), synth("click"))   # someone's own file
        pv = provenance(proj, ["pop", "click", "bars", "ding", "snap"], skill_sfx=skill)
        want = {"pop": "synth", "click": "synth", "bars": "elevenlabs", "ding": "unknown"}
        if pv != want:
            fails.append(f"provenance of copied cues: {pv} != {want}")
        # a recorded cue whose file was later replaced by hand is no longer 'synth'
        store("snap", synth("snap"), proj, "synth")
        ak.write_wav(os.path.join(proj, "snap.wav"), synth("pop"))
        if provenance(proj, ["snap"], skill_sfx=skill).get("snap") != "unknown":
            fails.append("a hand-replaced file must read 'unknown' (never overwritten)")
        if kind_of("imported:bars_2.wav") != "user" or kind_of("synth") != "synth":
            fails.append("kind_of: old 'imported:' entries must read as the user's")
        # check: cues come from index.html when build/sfx_placed.json is absent
        os.chdir(os.path.join(tmp, "proj"))
        open("index.html", "w").write(
            '<audio id="a1" class="clip sfx" data-start="1.5" data-duration="0.3" '
            'src="assets/sfx/pop.wav" data-volume="0.1">'
            '<audio id="bgm" class="clip music" data-start="0" data-duration="9" '
            'src="assets/bgm/bed.wav">'
            '<audio data-start="4.0" src="assets/sfx/click.wav" class="clip">')
        cues, where = load_cues()
        if [c["id"] for c in cues] != ["a1", "click.wav"] or "index.html" not in where:
            fails.append(f"index.html cues: {cues} from {where}")
        os.remove("index.html")
        cues, where = load_cues()
        if cues or "no SFX list" not in where:
            fails.append(f"no cue source must give ([], reason), got {cues}, {where}")
        if index_uses(["pop"], "nope.html") != []:
            fails.append("index_uses on a missing index.html must be []")
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)
    for f in fails:
        print("  ✗ " + f)
    print(f"  sfx selftest: {'all passed' if not fails else f'{len(fails)} failed'}")
    return 1 if fails else 0


def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("--dir", default=SFX_DIR)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lib = sub.add_parser("library", help="ensure the named set exists")
    lib.add_argument("--from", dest="src", action="append", default=[],
                     help="import matching files you own from this folder (repeatable)")
    lib.add_argument("--synth", action="store_true", help="never call ElevenLabs; synthesise")
    lib.add_argument("--keep-synth", action="store_true",
                     help="with a key: only fill MISSING cues, keep synthesised stand-ins")
    lib.add_argument("--names", help="comma list (default: the whole set)")
    lib.add_argument("--force", action="store_true")
    lib.add_argument("--influence", type=float, default=0.6)
    g = sub.add_parser("gen", help="one custom cue from a prompt")
    g.add_argument("prompt")
    g.add_argument("--name", required=True)
    g.add_argument("--duration", type=float, default=1.3)
    g.add_argument("--influence", type=float, default=0.6)
    g.add_argument("--force", action="store_true")
    p = sub.add_parser("place", help="scale cues to the voice and move them off words")
    p.add_argument("cues", help="json list of {name, t, base_vol, exempt}")
    p.add_argument("--words", default="src/words.json")
    p.add_argument("--voice")
    p.add_argument("--end", type=float)
    p.add_argument("--apply", action="store_true", help="write media.json audio.sfx")
    c = sub.add_parser("check", help="re-transcription diff → the cue on each changed word")
    c.add_argument("heard", help="words json of the re-transcribed master (transcribe.py --words)")
    c.add_argument("--words", default="src/words.json")
    c.add_argument("--placed", help="a cue list (sfx_placed.json format); default: "
                   "index.html's sfx clips → build/sfx_placed.json → build/scenes.json")
    sub.add_parser("status", help="where every cue came from (synth / elevenlabs / user)")
    if sys.argv[1:2] == ["selftest"]:
        hfcfg.ensure_deps(["numpy"])
        return selftest()
    a = ap.parse_args()
    hfcfg.load(a.config)
    hfcfg.require("ffmpeg", "ffprobe")
    hfcfg.ensure_deps(["numpy"])
    return {"library": cmd_library, "gen": cmd_gen, "place": cmd_place,
            "check": cmd_check, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
