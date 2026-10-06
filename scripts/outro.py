#!/usr/bin/env python3
"""The animated logo outro ("סגיר") — opt-in, brand-coloured, inside the Reels grid.

WHY THIS IS A MODULE AND NOT A TEMPLATE. The signature closing
was hand-built around one logo cut into K / O / wordmark parts. Logos differ, so this file
rebuilds the SAME choreography from what scripts/brand_from_logo.py measures on any logo:
its size, its colours and — the key to the signature — its enclosed transparent counters
("holes"). The speaker shrinks into a circle around the face and flies into the logo; when
the logo has a round hole big enough, the circle lands EXACTLY in it, which is the shot.

It is opt-in on purpose: it runs only when a logo exists AND the user said yes (a 16:9
project deliberately had no outro). Ask first — references/outro.md has the wording.

Three styles, all ≈3.4-4.4 s after speech, all brand-coloured:
  portal  (default, the signature)  speaker → circle around the face → flies into the logo's
          hole (or onto a brand disc that opens into the logo), hairline, tagline, handle,
          slow push, fade.
  line    a brand hairline turns the page in reading direction (right→left for Hebrew),
          the logo wipes in, the tagline types itself.
  impact  hard cut to brand primary, logo slams in with a short overshoot, small shake,
          flash, handle pops. For energetic reels.

What this module returns is FRAGMENTS — element specs, CSS, timeline lines and SFX — that
build_index.py emits through its own clip() so every timed value still lands in
build/expected.json and the END guard. It never writes index.html itself (except the
standalone `preview` composition, which is a test bench, not a deliverable).

The hand-off from live video to the freeze frame is the one place a jump can hide:
  * the last frame is extracted at FULL resolution with an explicit bt709 matrix (the
    renderer decodes video as bt709; ffmpeg's png path would otherwise guess bt601 and the
    colours shift on the cut),
  * the freeze gets the A-roll's box, object-fit and transform-origin, and its first
    transform is the beat map's final state (scale 1.02, y 0 for full-screen),
  * every outro tween drives "#aroll, #ofreeze" TOGETHER, so whichever one is on screen
    is in the same place — the freeze can take over on any frame without a seam,
  * the freeze appears one frame BEFORE the video ends (videos are inclusive of their end
    frame, divs/images exclusive) — both show the same picture on that frame.
Never wrap the <video> in a timed element (it freezes); the video is animated itself.

Usage
  python3 scripts/outro.py plan                       # what the build would emit (json)
  python3 scripts/outro.py face  --aroll assets/aroll.mp4
  python3 scripts/outro.py sfx                        # synthesise the outro cues
  python3 scripts/outro.py preview --style portal [--brand brand/brand.json]
          [--aroll assets/aroll.mp4] [--out build/outro_preview] [--render]
"""
from __future__ import annotations

import json
import math
import os
import shutil
import string
import sys
import unicodedata
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402
import grid  # noqa: E402

TEMPLATE_DIR = os.path.join(hfcfg.SKILL_DIR, "templates", "outro")

# Tail after the outro start, per style. The portal runs 4.4 s.
DURATION = {"portal": 4.4, "line": 3.8, "impact": 3.4}
STYLES = tuple(DURATION)

# How far under the SPEECH level each outro cue lands (peak momentary loudness vs the
# voice reference). Under speech the house rule is ~7 dB (references/sound.md); in the
# outro there is no voice to protect, the cues ARE the foreground, so they sit closer —
# and always above the outro bed (OUTRO_BED_BELOW_DB). The vault thunk is the landing and
# sits closest; the shimmer is air, not a hit.
CUE_BELOW_VOICE_DB = {"page": 5.0, "vault": 3.0, "shimmer": 6.0, "slam": 2.0,
                      "rush": 6.0, "pop": 6.0}
OUTRO_BED_BELOW_DB = 9.0

# Logo sizing: equal AREA, not equal width, so a square mark and a wide wordmark carry the
# same visual weight. Clamped to the safe zone with ~60 px air on each side.
LOGO_AREA = 220000
LOGO_MAX_W = 760
LOGO_MAX_H = 440
# A hole is usable for the landing when it is round enough and the face inside it is still
# a face on a phone (a 60 px circle at 1080 wide reads; a 30 px one is a dot).
HOLE_MIN_ROUNDNESS = 0.78
HOLE_MIN_SCREEN_R = 40
# The face circle is drawn this much LARGER than the hole: the logo sits above it, so the
# overlap disappears under the ink, and an anti-aliased hole edge never shows background.
HOLE_OVERSCAN = 1.05


# ------------------------------------------------------------------- colour
def _rgb(h):
    s = str(h).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def _lum(c):
    def lin(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (_rgb(c) if isinstance(c, str) else c)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _mix(a, b, t):
    a, b = _rgb(a), _rgb(b)
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ------------------------------------------------------------------ settings
def settings(cfg, media=None):
    """Merged outro settings, or None when the outro is off.

    media.json "outro" wins over config "outro". `true` means "on, config defaults";
    `false` or {"enabled": false} switches it off even if the config says on.
    """
    base = dict(cfg.get("outro", {}) or {})
    m = (media or {}).get("outro")
    if m is False:
        return None
    if m is True:
        base["enabled"] = True
    elif isinstance(m, dict):
        base.update({k: v for k, v in m.items() if not str(k).startswith("_")})
        base.setdefault("enabled", True)
        if "enabled" not in m:
            base["enabled"] = True
    if not base.get("enabled"):
        return None
    style = base.get("style") or "portal"
    if style not in STYLES:
        sys.exit(f"outro.style {style!r} — choose one of {', '.join(STYLES)}")
    base["style"] = style
    return base


def brand_json_path(cfg):
    b = cfg.get("brand", {})
    if b.get("json"):
        return b["json"]
    return os.path.join(os.path.dirname(b.get("css") or "brand/brand.css") or "brand",
                        "brand.json")


def load_brand(cfg, path=None):
    """brand.json (from brand_from_logo.py). The outro needs a logo — no logo, no outro."""
    p = path or brand_json_path(cfg)
    if not os.path.exists(p):
        sys.exit(f"outro is on but {p} does not exist.\n"
                 f"  The outro needs the client's logo: run scripts/brand_from_logo.py "
                 f"<logo.png> first,\n  or switch the outro off (media.json \"outro\": false).")
    d = json.load(open(p, encoding="utf-8"))
    lg = d.get("logo") or {}
    # prefer the version with white-filled counters knocked out (brand_from_logo.py)
    src = (lg.get("knocked") if lg.get("knocked") and os.path.exists(lg["knocked"]) else None) \
        or lg.get("trimmed") or lg.get("src")
    if not src or not os.path.exists(src):
        sys.exit(f"{p}: logo file {src!r} not found (paths are relative to the project)")
    d["_path"] = p
    d["_logo"] = src
    return d


def aroll_state(beatmap):
    """The A-roll's transform at the end of the reel — MUST mirror build_index.py's rule
    (`panel` → scale 1.02, y 770; any other non-hook beat → scale 1.02, y 0; no beat → 1, 0).
    The freeze frame starts from exactly this state, or the hand-off jumps."""
    state = (1.0, 0.0)
    if beatmap and getattr(beatmap, "BEATS", None):
        for _, kind, _ in beatmap.BEATS:
            if kind == "hook":
                continue
            state = (1.02, 770.0) if kind == "panel" else (1.02, 0.0)
    return state


# ---------------------------------------------------------------- media io
def _raw_rgb(path, w, h, at=None, pix="rgb24", extra_vf=""):
    """One frame as raw bytes, scaled to w x h. ffmpeg only — no numpy needed."""
    cmd = ["ffmpeg", "-v", "error"]
    if at is not None:
        cmd += ["-ss", f"{max(0.0, at):.3f}"]
    cmd += ["-i", path, "-frames:v", "1",
            "-vf", f"{extra_vf}scale={w}:{h}:flags=area,format={pix}",
            "-f", "rawvideo", "-"]
    r = hfcfg.run(cmd, text=False)
    return r.stdout


def probe_size(path):
    s = hfcfg.probe(path, "stream=width,height", "v:0")
    try:
        w, h = [int(x) for x in s.split(",")[:2]]
        return w, h
    except ValueError:
        return None


def media_duration(path):
    try:
        return float(hfcfg.probe(path, "format=duration") or 0)
    except ValueError:
        return 0.0


def measure_face(aroll, end, W=1080, H=1920):
    """Face centre and width in COMPOSITION px, from a skin mask over the last ~1 s.

    The mask is the one references/layout.md uses for recentring
    (r>95 & r>g+16 & g>b+6 & 40<r-b<130): the largest column run of skin is the face, its
    top is the hairline-ish edge, and the face centre sits ~0.62 face-widths below that.
    Hands and phones fool a single frame, so take the median of five and reject runs that
    are implausibly narrow or wide. Returns (x, y, width, how)."""
    size = probe_size(aroll)
    if not size:
        return W / 2, H * 0.28, 0, "fallback (could not read the A-roll)"
    sw, sh = size
    sx, sy = 270, max(2, round(270 * sh / sw / 2) * 2)
    k = sw / sx                     # source px per sample px
    xs, ys, ws = [], [], []
    for i in range(5):
        t = end - 0.95 + i * 0.2
        buf = _raw_rgb(aroll, sx, sy, at=t)
        if len(buf) < sx * sy * 3:
            continue
        r0, r1 = int(sy * 150 / 1920), int(sy * 1000 / 1920)
        cols = [0] * sx
        mask = bytearray(sx * sy)
        for y in range(r0, r1):
            row = y * sx * 3
            for x in range(sx):
                p = row + x * 3
                r, g, b = buf[p], buf[p + 1], buf[p + 2]
                if r > 95 and r > g + 16 and g > b + 6 and 40 < r - b < 130:
                    mask[y * sx + x] = 1
                    cols[x] += 1
        peak = max(cols) or 0
        if peak < 4:
            continue
        thr = max(3, 0.25 * peak)
        best, cur = (0, 0), None
        for x in range(sx + 1):
            on = x < sx and cols[x] >= thr
            if on and cur is None:
                cur = x
            elif not on and cur is not None:
                if x - cur > best[1] - best[0]:
                    best = (cur, x)
                cur = None
        a, b_ = best
        width = (b_ - a) * k
        if not (30 <= width <= 0.62 * sw):
            continue
        cx = sum((x + 0.5) * cols[x] for x in range(a, b_)) / max(1, sum(cols[a:b_])) * k
        need = 0.3 * (b_ - a)
        top = None
        for y in range(r0, r1):
            n = sum(mask[y * sx + x] for x in range(a, b_))
            if n >= need:
                top = y
                break
        if top is None:
            continue
        cy = (top + 0.62 * (b_ - a)) * k
        xs.append(cx)
        ys.append(cy)
        ws.append(width)
    if len(xs) < 2:
        return W / 2, H * 0.28, 0, "fallback (no stable skin region — upper-third centre)"
    med = lambda v: sorted(v)[len(v) // 2]
    fx, fy, fw = med(xs), med(ys), med(ws)
    # source px → element px (object-fit: cover)
    c = max(W / sw, H / sh)
    ox, oy = (sw * c - W) / 2, (sh * c - H) / 2
    return fx * c - ox, fy * c - oy, fw * c, f"skin mask, {len(xs)}/5 frames"


def extract_last_frame(aroll, out, W, H):
    """The A-roll's LAST frame at full resolution, colour-matched to how the renderer shows
    the video. -sseof seeks near the end and -update keeps overwriting, so the file holds
    the final decoded frame whatever the timestamps say."""
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    tags = hfcfg.probe(aroll, "stream=color_space,color_range", "v:0")
    matrix = "bt709"
    if "bt601" in tags or "smpte170m" in tags or "bt470bg" in tags:
        matrix = "bt601"
    rng = "pc" if ",pc" in tags else "tv"
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos:"
          f"in_color_matrix={matrix}:in_range={rng}:out_range=pc,crop={W}:{H},format=rgb24")
    r = hfcfg.run(["ffmpeg", "-v", "error", "-y", "-sseof", "-0.6", "-i", aroll,
                   "-vf", vf, "-update", "1", "-compression_level", "3", out])
    if r.returncode or not os.path.exists(out):
        sys.exit(f"could not extract the last frame of {aroll}\n{r.stderr[-400:]}")
    return out


def logo_coverage(path, against):
    """Fraction of the logo's opaque pixels that stand off each background colour.

    A logo is a mark, not body text, so the bar is lower than WCAG text contrast: 1.45:1
    (gold on bone, a classic pairing, measures ~1.8). Returns {bg_hex: fraction}."""
    size = probe_size(path) or (300, 100)
    w = min(320, size[0])
    h = max(2, round(w * size[1] / size[0]))
    buf = _raw_rgb(path, w, h, pix="rgba")
    out = {}
    tot = 0.0
    px = []
    for i in range(0, len(buf) - 3, 4):
        a = buf[i + 3]
        if a > 60:
            px.append(((buf[i], buf[i + 1], buf[i + 2]), a / 255.0))
            tot += a / 255.0
    for bg in against:
        ok = sum(a for c, a in px if _contrast(c, bg) >= 1.45)
        out[bg] = ok / tot if tot else 0.0
    return out


def silhouette(src, colour, out):
    """A one-colour copy of the logo that keeps its alpha — an <img>, so grid.py can still
    measure it (a CSS mask on a div has no text and would escape the grid gate)."""
    r, g, b = _rgb(colour)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    res = hfcfg.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vf",
                     f"format=rgba,geq=r={r}:g={g}:b={b}:a='alpha(X,Y)'", out])
    if res.returncode or not os.path.exists(out):
        sys.exit(f"could not make a silhouette of {src}\n{res.stderr[-300:]}")
    return out


def wav_duration(path):
    try:
        with wave.open(path) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return media_duration(path)


# ---------------------------------------------------------------- sfx synth
def synth_sfx(sfx_dir, force=False):
    """The outro's own cues, synthesised (no licence, no attribution), -6 dBFS peak:

      outro_page.wav     page-turn swish — the start of portal / the line's wipe
      outro_vault.wav    heavy vault thunk + latch — the circle landing in the hole
      outro_shimmer.wav  soft logo shimmer — the logo resolving
      outro_rush.wav     short rising air — into the impact slam
      outro_slam.wav     punchy low hit + flash crack — the impact landing
      outro_pop.wav      small soft pop — the handle in impact
    """
    import math as m
    import random
    import setup_assets as sa
    SR = sa.SR
    os.makedirs(sfx_dir, exist_ok=True)

    def page(dur=0.62):
        n = int(dur * SR)
        body = sa._highpass(sa._lowpass_sweep(sa._noise(n, 41), 7000.0, 1600.0), 650.0)
        rng = random.Random(9)
        out, crinkle = [], 1.0
        for i, v in enumerate(body):
            t = i / n
            if i % 180 == 0:
                crinkle = 0.65 + 0.35 * rng.random()
            amp = (m.sin(m.pi * min(1.0, t * 1.25)) ** 1.6) * crinkle
            out.append(v * amp)
        return out

    def vault(dur=1.15):
        n = int(dur * SR)
        out = [0.0] * n
        env = sa._env(n, 0.002, 0.30, 3.0)
        p = 0.0
        for i in range(n):
            f = 46.0 + 30.0 * m.exp(-9.0 * i / n)
            p += 2 * m.pi * f / SR
            out[i] += m.sin(p) * env[i] * 1.0
        for f, g, d in ((382.0, 0.30, 0.22), (611.0, 0.22, 0.18), (1127.0, 0.12, 0.12),
                        (1873.0, 0.06, 0.08)):
            e = sa._env(n, 0.001, d, 2.6)
            q = 0.0
            for i in range(n):
                q += 2 * m.pi * f / SR
                out[i] += m.sin(q) * e[i] * g
        hit = sa._lowpass_sweep(sa._noise(int(0.05 * SR), 77), 5000.0, 600.0)
        for i, v in enumerate(hit):
            out[i] += v * 0.5 * (1 - i / len(hit))
        s = int(0.075 * SR)                       # the latch, a beat after the door
        latch = sa._highpass(sa._noise(int(0.03 * SR), 78), 1800.0)
        le = sa._env(len(latch), 0.0005, 0.008, 5.0)
        for i, v in enumerate(latch):
            if s + i < n:
                out[s + i] += v * le[i] * 0.35
        return out

    def shimmer(dur=1.5):
        n = int(dur * SR)
        out = [0.0] * n
        for k, (f, g) in enumerate(((2093.0, 0.5), (2637.0, 0.42), (3136.0, 0.34),
                                    (4186.0, 0.24), (5274.0, 0.16))):
            s = int(k * 0.045 * SR)
            e = sa._env(n - s, 0.012, 0.55, 2.4)
            q = 0.0
            for i in range(n - s):
                q += 2 * m.pi * f * (1 + 0.002 * m.sin(2 * m.pi * 5.5 * i / SR)) / SR
                out[s + i] += m.sin(q) * e[i] * g
        air = sa._highpass(sa._noise(n, 51), 6000.0)
        for i, v in enumerate(air):
            t = i / n
            out[i] += v * 0.10 * m.sin(m.pi * t) ** 2
        return out

    def rush(dur=0.32):
        n = int(dur * SR)
        body = sa._lowpass_sweep(sa._noise(n, 61), 500.0, 9000.0)
        return [v * (i / n) ** 2.2 for i, v in enumerate(body)]

    def slam(dur=0.9):
        n = int(dur * SR)
        out = [0.0] * n
        env = sa._env(n, 0.001, 0.22, 3.4)
        p = 0.0
        for i in range(n):
            f = 58.0 + 70.0 * m.exp(-14.0 * i / n)
            p += 2 * m.pi * f / SR
            out[i] = m.sin(p) * env[i]
        crack = sa._highpass(sa._noise(int(0.08 * SR), 66), 2200.0)
        ce = sa._env(len(crack), 0.0005, 0.03, 4.0)
        for i, v in enumerate(crack):
            out[i] += v * ce[i] * 0.55
        return out

    def pop(dur=0.2):
        n = int(dur * SR)
        env = sa._env(n, 0.003, 0.05, 4.0)
        out, p = [0.0] * n, 0.0
        for i in range(n):
            f = 380.0 + 700.0 * (i / n) ** 0.5
            p += 2 * m.pi * f / SR
            out[i] = m.sin(p) * env[i]
        return out

    made = []
    for name, fn in (("page", page), ("vault", vault), ("shimmer", shimmer),
                     ("rush", rush), ("slam", slam), ("pop", pop)):
        path = os.path.join(sfx_dir, f"outro_{name}.wav")
        if force or not os.path.exists(path):
            sa._write_wav(path, fn(), peak_db=-6.0)
            made.append(path)
    return made


def momentary(path, af=""):
    """EBU R128 momentary loudness (400 ms windows, LUFS) of a file, one value per 100 ms.
    Loudness, not volumedetect's mean: a cue that decays for a second has a mean far below
    how loud it LANDS, and a mean-based level puts it ~10 dB too quiet."""
    r = hfcfg.run(["ffmpeg", "-nostats", "-nostdin", "-i", path, "-vn", "-af",
                   f"{af}apad=pad_dur=0.4,ebur128", "-f", "null", "-"])
    out = []
    for ln in r.stderr.splitlines():
        if " t:" in ln and " M:" in ln:
            try:
                out.append(float(ln.split(" M:")[1].split()[0]))
            except (IndexError, ValueError):
                pass
    return [v for v in out if v > -70]


def voice_reference(aroll):
    """The speech level a cue is measured against: the 90th percentile of the A-roll's
    momentary loudness — the level of spoken words, ignoring pauses and the odd shout."""
    v = sorted(momentary(aroll))
    return v[int(0.9 * (len(v) - 1))] if v else None


_PEAK_CACHE = {}


def cue_volume(cfg, voice_ref, cue, path):
    """Level a cue by where it LANDS against the voice, not by copying a number:
    vol = 10^((voice_ref − below − cue_peak)/20), cue_peak = the cue's loudest momentary
    loudness at unity, below = CUE_BELOW_VOICE_DB[cue] (+ audio.outro_sfx_trim_db)."""
    if voice_ref is None:
        return 0.35
    if path not in _PEAK_CACHE:
        m = momentary(path)
        _PEAK_CACHE[path] = max(m) if m else None
    cp = _PEAK_CACHE[path]
    if cp is None:
        return 0.35
    below = CUE_BELOW_VOICE_DB.get(cue, 6.0) + \
        float(cfg.get("audio", {}).get("outro_sfx_trim_db", 0.0))
    v = 10 ** ((voice_ref - below - cp) / 20.0)
    return round(max(0.02, min(1.0, v)), 3)


# --------------------------------------------------------------------- plan
def plan(cfg, media, aroll_end, beatmap=None, root=".", brand_path=None, quiet=False,
         state=None):
    """Everything the builder needs, or None when the outro is off.

    Keys: style, start, end, aroll_end, elements [{tag,id,cls,start,dur,extra,inner}],
          css, js [lines], sfx [{id,src,start,duration,volume}], bed {...}, info {...}
    """
    st = settings(cfg, media)
    if not st:
        return None
    say = (lambda *a: None) if quiet else (lambda *a: print("  outro:", *a))
    W, H = cfg["project"]["width"], cfg["project"]["height"]
    fps = float(cfg["project"].get("fps", 25))
    F = round(1.0 / fps, 4)
    g = grid.from_config(cfg)
    gx0, gy0, gx1, gy1 = g["safe"]
    cxs = g["center_x"]
    lang_dir = cfg.get("language", {}).get("direction", "rtl")
    style = st["style"]

    cwd = os.getcwd()
    os.chdir(root)
    try:
        brand = load_brand(cfg, brand_path)
        col = dict(brand.get("colors") or {})
        for k, v in (("primary", cfg["brand"].get("accent", "#4fe6d8")), ("ink", "#0a0a0a"),
                     ("paper", "#f7f5f0"), ("on_primary", "#0a0a0a"),
                     ("hl_on_dark", cfg["brand"].get("accent", "#4fe6d8")),
                     ("hl_on_light", "#0b6b66"), ("grad_a", "#0e384e"), ("grad_b", "#03141e")):
            col.setdefault(k, v)
        lg = brand["logo"]
        logo_src = brand["_logo"]
        lw0 = float(lg.get("w") or (probe_size(logo_src) or (600, 200))[0])
        lh0 = float(lg.get("h") or (probe_size(logo_src) or (600, 200))[1])
        asp = lw0 / lh0

        aroll = media.get("aroll", "assets/aroll.mp4")
        if not os.path.exists(aroll):
            sys.exit(f"outro: A-roll {aroll} not found")

        # ---------------------------------------------------------- timing
        start = st.get("start")
        start = float(start) if start is not None else aroll_end - 0.2
        if start < aroll_end - 1.5 or start > aroll_end + 1.0:
            sys.exit(f"outro.start {start:.2f}s is not near the A-roll end {aroll_end:.2f}s "
                     f"(allowed {aroll_end - 1.5:.2f}-{aroll_end + 1.0:.2f})")
        O = round(round(start / F) * F, 3)
        E = round(round((O + DURATION[style]) / F) * F, 3)
        fz0 = round(aroll_end - F, 3)          # the freeze shows the video's last frame
        s0, y0 = state or aroll_state(beatmap)

        # --------------------------------------------------- background
        paper, ink = col["paper"], col["ink"]
        dark_mid = _mix(col["grad_a"], col["grad_b"], 0.5)
        if style == "impact":
            bg_kind, bg_hex = "primary", col["primary"]
        else:
            want = st.get("background")
            cov = logo_coverage(logo_src, [paper, dark_mid])
            if want in ("paper", "dark"):
                bg_kind = want
            elif cov[paper] >= 0.85 or cov[paper] >= cov[dark_mid]:
                bg_kind = "paper"          # the signature look: bone/paper, unless it fails
            else:
                bg_kind = "dark"
            bg_hex = paper if bg_kind == "paper" else dark_mid
        cov_bg = logo_coverage(logo_src, [bg_hex])[bg_hex]
        logo_file = logo_src
        variant = "full colour"
        if cov_bg < 0.85:
            light_bg = _lum(bg_hex) > 0.30
            pref = lg.get("on_light") if light_bg else lg.get("on_dark")
            if pref and os.path.exists(pref):
                logo_file, variant = pref, ("on_light" if light_bg else "on_dark")
            else:
                colour = col["on_primary"] if bg_kind == "primary" else (
                    ink if light_bg else "#ffffff")
                logo_file = silhouette(logo_src, colour, "assets/outro/logo_sil.png")
                variant = f"silhouette {colour}"
        say(f"{style}  start {O:.2f}s → end {E:.2f}s  background {bg_kind}  "
            f"logo {variant} (coverage {cov_bg:.0%})")

        if bg_kind == "paper":
            bg_css, fade = "var(--brand-paper)", "var(--brand-paper)"
            tag_color, handle_color = "var(--brand-ink)", "var(--hl-on-light)"
            rule = "var(--brand-primary)" if _contrast(col["primary"], paper) >= 1.3 \
                else "var(--brand-ink)"
        elif bg_kind == "dark":
            # a plain linear gradient: a soft radial glow bands visibly in an 8-bit encode
            bg_css = "linear-gradient(170deg, var(--brand-grad-a), var(--brand-grad-b))"
            fade = "var(--brand-grad-b)"
            tag_color, handle_color = "rgba(255,255,255,.90)", "var(--hl-on-dark)"
            rule = "var(--brand-primary)" if _contrast(col["primary"], dark_mid) >= 1.3 \
                else "var(--hl-on-dark)"
        else:
            bg_css = fade = "var(--brand-primary)"
            tag_color = handle_color = rule = "var(--brand-on-primary)"

        # ------------------------------------------------------- geometry
        lw = math.sqrt(LOGO_AREA * asp)
        lh = lw / asp
        k = min(1.0, LOGO_MAX_W / lw, LOGO_MAX_H / lh)
        lw, lh = lw * k, lh * k
        sc = lw / lw0                                   # logo px → screen px

        hole = None
        hs = lg.get("holes") or []
        if style == "portal" and hs and not st.get("no_hole"):
            h0 = hs[0]
            ok_round = float(h0.get("roundness", 0)) >= HOLE_MIN_ROUNDNESS
            if ok_round and float(h0["r"]) * sc < HOLE_MIN_SCREEN_R:
                # grow the logo (within the safe zone) until the hole can hold a face
                grow = min(HOLE_MIN_SCREEN_R / (float(h0["r"]) * sc),
                           LOGO_MAX_W / lw, (LOGO_MAX_H + 80) / lh)
                lw, lh = lw * grow, lh * grow
                sc = lw / lw0
            if not ok_round:
                say(f"hole roundness {h0.get('roundness')} < {HOLE_MIN_ROUNDNESS} — disc landing")
            elif float(h0["r"]) * sc < HOLE_MIN_SCREEN_R:
                say(f"hole r {float(h0['r']) * sc:.0f}px on screen < {HOLE_MIN_SCREEN_R} — "
                    f"disc landing")
            elif not _hole_is_clear(logo_src, h0):
                say("the hole is FILLED in the logo file (opaque pixels at its centre) — the "
                    "face would vanish under it; disc landing. Knock the counter out of the "
                    "logo to get the hole landing.")
            else:
                hole = h0

        # vertical stack: logo, rule, tagline, handle — centred on y ~860, x 500
        tagline = (st.get("tagline") or "").strip()
        handle = (st.get("handle") or "").strip()
        tfs = 0
        if tagline:
            n = max(1, len(tagline))
            tfs = int(max(30, min(50, (gx1 - gx0 - 140) / (0.56 * n))))
            if 0.56 * n * 30 > gx1 - gx0 - 40:
                say(f"tagline is {n} chars — too long for one line inside the safe zone; "
                    f"shorten it (≤ ~40 chars)")
        hfs = 36 if handle else 0
        gap_rule, gap_tag, gap_handle = 44, 30, 18
        th = round(tfs * 1.25) if tagline else 0
        hh = round(hfs * 1.25) if handle else 0
        block = lh + gap_rule + 2 + (gap_tag + th if tagline else 0) + \
            (gap_handle + hh if handle else 0)
        cy_block = float(st.get("center_y", 860))
        ly = cy_block - block / 2
        ly = max(gy0 + 60, min(ly, gy1 - 120 - block))
        lx = cxs - lw / 2
        ry = ly + lh + gap_rule
        ty = ry + 2 + gap_tag
        hy = (ty + th + gap_handle) if tagline else (ry + 2 + gap_tag)
        rw = min(lw, 560.0)
        rx = cxs - rw / 2

        # where the speaker circle lands (screen) and how big it is there
        if hole:
            lox, loy = float(hole["cx"]) * sc, float(hole["cy"]) * sc
            land_x, land_y = lx + lox, ly + loy
            land_r = float(hole["r"]) * sc * HOLE_OVERSCAN
        else:
            lox, loy = lw / 2, lh / 2
            land_x, land_y = lx + lox, ly + loy
            land_r = max(46.0, min(150.0, min(lw, lh) * 0.36))
        # radius that uncovers the whole logo box from the pivot
        cover_r = max(math.hypot(px - lox, py - loy) for px in (0, lw) for py in (0, lh)) + 4
        iris_end = cover_r + 26

        # -------------------------------------------------------- the face
        face = st.get("face")
        if face:
            fx, fy, fw, how = float(face[0]), float(face[1]), 0.0, "config"
        elif style != "portal":           # only the portal flies the face anywhere
            fx, fy, fw, how = W / 2, H * 0.28, 0.0, "not needed for this style"
        else:
            fx, fy, fw, how = measure_face(aroll, aroll_end, W, H)
        R0 = max(240.0, min(340.0, fw * 1.1)) if fw else 330.0
        fcx = min(max(fx, R0 + 12), W - R0 - 12)
        fcy = min(max(fy, R0 + 12), H - R0 - 12)
        if style == "portal":
            say(f"face {fx:.0f},{fy:.0f} ({how}); circle r {R0:.0f} at {fcx:.0f},{fcy:.0f}; "
                f"lands at {land_x:.0f},{land_y:.0f} r {land_r:.1f}" +
                (" in the hole" if hole else " on a brand disc"))
        Rbig = math.ceil(max(math.hypot(px - fcx, py - fcy) for px in (0, W) for py in (0, H))) + 6
        # transform that maps the local circle (fcx,fcy,R0) onto (land_x,land_y,land_r) with
        # the element's transform-origin (50% 30%) kept — changing the origin mid-scale jumps
        oxo, oyo = W * 0.5, H * 0.30
        s1 = land_r / R0
        tx = land_x - oxo - s1 * (fcx - oxo)
        ty_ = land_y - oyo - s1 * (fcy - oyo)

        # --------------------------------------------------------- files
        freeze = extract_last_frame(aroll, "assets/outro_last.png", W, H)
        sfx_dir = cfg.get("audio", {}).get("sfx_dir", "assets/sfx")
        synth_sfx(sfx_dir)
        voice_ref = voice_reference(aroll)
    finally:
        os.chdir(cwd)

    r = lambda v: round(float(v), 3)
    FOOT = '"#aroll, #ofreeze"'

    # ------------------------------------------------------------- elements
    els = []
    span = r(E - O)
    els.append(dict(tag="div", id="obg", cls="obg", start=O, dur=span,
                    extra=' data-grid="bleed" data-layout-ignore'))
    els.append(dict(tag="img", id="ofreeze", cls="ofreeze", start=fz0, dur=r(E - fz0),
                    extra=f' src="{freeze}" alt="" data-grid="bleed" data-layout-ignore'))
    if style == "line":
        els.append(dict(tag="div", id="owipe", cls="owipe", start=O, dur=0.6,
                        extra=' data-grid="bleed" data-layout-ignore'))
    inner = ['<div id="olockin">']
    if not hole and style == "portal":
        # the disc is a circle whose STROKE is the fill: r = R/2, stroke-width = R draws a
        # solid disc; growing r while thinning the stroke opens it like an iris, and the
        # stroke's inner edge (r - w/2) is exactly where the logo's reveal mask is.
        S = 2 * math.ceil(iris_end + 8)
        inner.append(f'<svg id="odisc" data-grid="bleed" width="{S}" height="{S}" '
                     f'viewBox="0 0 {S} {S}"><circle id="odiscc" cx="{S // 2}" cy="{S // 2}" '
                     f'r="{r(land_r / 2)}" fill="none" stroke-width="{r(land_r)}" /></svg>')
    inner.append(f'<img id="ologo" src="{logo_file}" alt="" />')
    inner.append('<div id="orule" data-grid="bleed"></div>')
    if tagline:
        if style == "line":
            inner.append(f'<div id="otag">{_char_spans(tagline)}</div>')
        else:
            inner.append(f'<div id="otag">{_esc(tagline)}</div>')
    if handle:
        inner.append(f'<div id="ohandle"><span>{_esc(handle)}</span></div>')
    if style == "impact":
        inner.append('<div id="oflash" data-grid="bleed"></div>')
    inner.append('</div>')
    els.append(dict(tag="div", id="olock", cls="olock", start=O, dur=span, inner="".join(inner),
                    extra=' data-layout-allow-overflow'))
    els.append(dict(tag="div", id="ofade", cls="ofade", start=O, dur=span,
                    extra=' data-grid="bleed" data-layout-ignore'))

    # ------------------------------------------------------------------ css
    rtl = lang_dir == "rtl"
    css = string.Template(open(os.path.join(TEMPLATE_DIR, "outro.css"),
                               encoding="utf-8").read()).substitute(
        style=style, W=W, H=H, bg=bg_css, fade=fade, rule=rule, wipe_w=5,
        push_ox=r(land_x if style == "portal" else cxs),
        push_oy=r(land_y if style == "portal" else ly + lh / 2),
        lx=r(lx), ly=r(ly), lw=r(lw), lh=r(lh), lox=r(lox), loy=r(loy),
        dx=r(land_x - math.ceil(iris_end + 8)), dy=r(land_y - math.ceil(iris_end + 8)),
        dd=2 * math.ceil(iris_end + 8),
        disc="var(--brand-primary)",
        rx=r(rx), ry=r(ry), rw=r(rw),
        rule_origin=("100% 50%" if rtl else "0% 50%") if style == "line" else "50% 50%",
        gx0=gx0 + 50, gw=gx1 - gx0 - 100, ty=r(ty), th=th, tfs=tfs, dir=lang_dir, tag_color=tag_color,
        hy=r(hy), hh=hh, hfs=hfs, handle_color=handle_color,
        flash_w=W + 160, flash_h=H + 160)

    # ------------------------------------------------------------------- js
    js = [f"      // ---- OUTRO ({style}) — scripts/outro.py. Starts {O}s, ends {E}s.",
          f"      tl.set(\"#ofreeze\", {{ x: 0, y: {r(y0)}, scale: {s0} }}, 0);",
          "      tl.set(\"#orule\", { scaleX: 0 }, 0);"]
    sfx = []

    def cue(name, t, cid):
        path = os.path.join(sfx_dir, f"outro_{name}.wav")
        full = path if os.path.isabs(path) else os.path.join(root, path)
        dur = min(wav_duration(full), E - t)
        if dur <= 0.05:
            return
        sfx.append({"id": cid, "src": path, "start": r(t), "duration": r(dur),
                    "volume": cue_volume(cfg, voice_ref, name, full)})

    def push(t0, to=1.035):
        js.append(f"      tl.fromTo(\"#olockin\", {{ scale: 1 }}, {{ scale: {to}, "
                  f"duration: {r(E - t0)}, ease: \"none\", immediateRender: false }}, {r(t0)});")

    def text_in(t_tag, t_handle):
        if tagline and style != "line":
            js.append(f"      tl.fromTo(\"#otag\", {{ opacity: 0, y: 18 }}, {{ opacity: 1, y: 0, "
                      f"duration: 0.6, ease: \"power2.out\", immediateRender: false }}, {r(t_tag)});")
        if handle:
            if style == "impact":
                js.append(f"      tl.fromTo(\"#ohandle\", {{ opacity: 0, scale: 0.6 }}, "
                          f"{{ opacity: 1, scale: 1, duration: 0.34, ease: \"back.out(2.2)\", "
                          f"immediateRender: false }}, {r(t_handle)});")
            else:
                js.append(f"      tl.fromTo(\"#ohandle\", {{ opacity: 0, y: 12 }}, {{ opacity: 1, "
                          f"y: 0, duration: 0.5, ease: \"power2.out\", immediateRender: false }}, "
                          f"{r(t_handle)});")

    def fade_out(dur=0.4):
        js.append(f"      tl.fromTo(\"#ofade\", {{ opacity: 0 }}, {{ opacity: 1, duration: {dur}, "
                  f"ease: \"power1.in\", immediateRender: false }}, {r(E - dur - F)});")

    if style == "portal":
        t_land = O + 1.24
        js += [
            # the speaker frame closes into a circle around the face …
            f"      tl.fromTo({FOOT}, {{ clipPath: \"circle({Rbig}px at {r(fcx)}px {r(fcy)}px)\" }}, "
            f"{{ clipPath: \"circle({r(R0)}px at {r(fcx)}px {r(fcy)}px)\", duration: 0.6, "
            f"ease: \"power3.inOut\", immediateRender: false }}, {r(O + 0.02)});",
            # … then flies and shrinks into the landing circle
            f"      tl.fromTo({FOOT}, {{ x: 0, y: {r(y0)}, scale: {s0} }}, {{ x: {r(tx)}, "
            f"y: {r(ty_)}, scale: {round(s1, 5)}, duration: 0.62, ease: \"power3.inOut\", "
            f"immediateRender: false }}, {r(O + 0.62)});",
        ]
        if hole:
            hr = float(hole["r"]) * sc
            ring = min(cover_r, hr * 2.25)
            js += [
                # the vault, in two beats like the original (O first, then K + wordmark):
                # 1) the ring around the hole rotates in on the hole centre as the face lands,
                # 2) a radial mask opens from that ring to the whole mark.
                f"      tl.fromTo(\"#ologo\", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.18, "
                f"ease: \"none\", immediateRender: false }}, {r(O + 0.86)});",
                f"      tl.fromTo(\"#ologo\", {{ rotation: -120, scale: 1.3, "
                f"clipPath: \"circle({r(hr * 0.98)}px at {r(lox)}px {r(loy)}px)\" }}, "
                f"{{ rotation: 0, scale: 1, clipPath: \"circle({r(ring)}px at {r(lox)}px {r(loy)}px)\", "
                f"duration: 0.62, ease: \"power3.out\", immediateRender: false }}, {r(O + 0.86)});",
                f"      tl.fromTo(\"#ologo\", {{ clipPath: \"circle({r(ring)}px at {r(lox)}px {r(loy)}px)\" }}, "
                f"{{ clipPath: \"circle({r(cover_r)}px at {r(lox)}px {r(loy)}px)\", duration: 0.75, "
                f"ease: \"power2.inOut\", immediateRender: false }}, {r(O + 1.48)});",
                f"      tl.to(\"#ofreeze\", {{ opacity: 0, duration: 0.4, ease: \"power1.in\" }}, "
                f"{r(O + 1.75)});",
            ]
        else:
            # no usable hole: the face becomes a brand disc, and the disc opens like an iris
            # into the logo — the ring's inner edge and the logo's mask move together
            d_open, t_open = 0.8, t_land + 0.16
            js += [
                f"      tl.fromTo(\"#odisc\", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.2, "
                f"ease: \"power1.out\", immediateRender: false }}, {r(t_land - 0.04)});",
                f"      tl.set(\"#ofreeze\", {{ opacity: 0 }}, {r(t_land + 0.2)});",
                f"      tl.fromTo(\"#odiscc\", {{ attr: {{ r: {r(land_r / 2)}, \"stroke-width\": {r(land_r)} }} }}, "
                f"{{ attr: {{ r: {r(iris_end)}, \"stroke-width\": 3 }}, duration: {d_open}, "
                f"ease: \"expo.inOut\", immediateRender: false }}, {r(t_open)});",
                f"      tl.set(\"#ologo\", {{ opacity: 1 }}, {r(t_open)});",
                f"      tl.fromTo(\"#ologo\", {{ scale: 1.06, clipPath: \"circle(0px at {r(lox)}px {r(loy)}px)\" }}, "
                f"{{ scale: 1, clipPath: \"circle({r(iris_end - 1.5)}px at {r(lox)}px {r(loy)}px)\", "
                f"duration: {d_open}, ease: \"expo.inOut\", immediateRender: false }}, {r(t_open)});",
                f"      tl.to(\"#odisc\", {{ opacity: 0, duration: 0.24, ease: \"power1.in\" }}, "
                f"{r(t_open + d_open * 0.55)});",
            ]
        js.append(f"      tl.fromTo(\"#orule\", {{ scaleX: 0 }}, {{ scaleX: 1, duration: 0.7, "
                  f"ease: \"power3.out\", immediateRender: false }}, {r(O + 1.95)});")
        text_in(O + 2.05, O + 2.4)
        push(t_land)
        fade_out()
        cue("page", O + 0.03, "osfx_page")
        cue("vault", t_land - 0.02, "osfx_vault")
        cue("shimmer", O + 1.48 if hole else t_land + 0.24, "osfx_shimmer")

    elif style == "line":
        # the page turns in READING direction: right→left for Hebrew
        x_from, x_to = (W, -6) if rtl else (-6, W)
        inset_to = f"inset(0px {W}px 0px 0px)" if rtl else f"inset(0px 0px 0px {W}px)"
        drift = -60 if rtl else 60
        js += [
            f"      tl.fromTo(\"#owipe\", {{ x: {x_from} }}, {{ x: {x_to}, duration: 0.55, "
            f"ease: \"power2.inOut\", immediateRender: false }}, {r(O)});",
            f"      tl.fromTo({FOOT}, {{ clipPath: \"inset(0px 0px 0px 0px)\" }}, "
            f"{{ clipPath: \"{inset_to}\", duration: 0.55, ease: \"power2.inOut\", "
            f"immediateRender: false }}, {r(O)});",
            f"      tl.fromTo({FOOT}, {{ x: 0, y: {r(y0)}, scale: {s0} }}, {{ x: {drift}, y: {r(y0)}, "
            f"scale: {s0}, duration: 0.55, ease: \"power2.in\", immediateRender: false }}, {r(O)});",
            f"      tl.set(\"#ofreeze\", {{ opacity: 0 }}, {r(O + 0.6)});",
            f"      tl.set(\"#ologo\", {{ opacity: 1 }}, {r(O + 0.4)});",
            f"      tl.fromTo(\"#ologo\", {{ x: {-drift * 0.6}, "
            f"clipPath: \"{'inset(0% 0% 0% 100%)' if rtl else 'inset(0% 100% 0% 0%)'}\" }}, "
            f"{{ x: 0, clipPath: \"inset(0% 0% 0% 0%)\", duration: 0.8, ease: \"power3.out\", "
            f"immediateRender: false }}, {r(O + 0.4)});",
            f"      tl.fromTo(\"#orule\", {{ scaleX: 0 }}, {{ scaleX: 1, duration: 0.6, "
            f"ease: \"power3.out\", immediateRender: false }}, {r(O + 0.85)});",
        ]
        t_type = O + 1.05
        if tagline:
            chars = _graphemes(tagline)
            step = min(0.055, 1.1 / max(1, len(chars)))
            js.append(f"      tl.set(\"#otag\", {{ opacity: 1 }}, {r(t_type)});")
            js.append(f"      document.querySelectorAll(\"#otag .ch\").forEach((el, i) => "
                      f"tl.set(el, {{ opacity: 1 }}, {r(t_type)} + i * {step}));")
            t_type += step * len(chars)
        text_in(0, t_type + 0.15)
        push(O + 0.4, 1.03)
        fade_out()
        cue("page", O, "osfx_page")
        cue("shimmer", O + 0.45, "osfx_shimmer")

    else:  # impact
        t_hit = O + 0.28
        slam_from = round(max(1.0, min(1.6, (gx1 - gx0 - 8) / lw,
                                       2 * min(ly + lh / 2 - gy0, gy1 - ly - lh / 2) / lh)), 3)
        js += [
            f"      tl.set({FOOT}, {{ opacity: 0 }}, {r(O)});",   # the hard cut
            # slam from as big as the SAFE ZONE allows (≤1.6×) — the grid rule holds even
            # for three frames; a motion blur sells the speed the extra size would have
            f"      tl.fromTo(\"#ologo\", {{ opacity: 0, scale: {slam_from}, filter: \"blur(14px)\" }}, "
            f"{{ opacity: 1, scale: 0.97, filter: \"blur(0px)\", "
            f"duration: 0.26, ease: \"power4.in\", immediateRender: false }}, {r(O + 0.02)});",
            f"      tl.to(\"#ologo\", {{ scale: 1, duration: 0.22, ease: \"back.out(3)\" }}, {r(t_hit)});",
            f"      tl.fromTo(\"#oflash\", {{ opacity: 0.85 }}, {{ opacity: 0, duration: 0.32, "
            f"ease: \"power2.out\", immediateRender: false }}, {r(t_hit)});",
        ]
        # a small decaying shake on the lockup — seek-safe explicit steps, no random
        shake = [(9, -6), (-7, 5), (5, -3), (-3, 2), (0, 0)]
        js.append(f"      tl.set(\"#olockin\", {{ x: 0, y: 0 }}, {r(t_hit)});")
        for i, (sx, sy) in enumerate(shake):
            js.append(f"      tl.to(\"#olockin\", {{ x: {sx}, y: {sy}, duration: 0.05, ease: \"sine.inOut\" }}, "
                      f"{r(t_hit + 0.01 + i * 0.05)});")
        js.append(f"      tl.fromTo(\"#orule\", {{ scaleX: 0 }}, {{ scaleX: 1, duration: 0.45, "
                  f"ease: \"power3.out\", immediateRender: false }}, {r(O + 0.55)});")
        text_in(O + 0.62, O + 0.9)
        push(O + 0.6, 1.04)
        fade_out(0.35)
        cue("rush", O, "osfx_rush")
        cue("slam", t_hit - 0.01, "osfx_slam")
        cue("pop", O + 0.9, "osfx_pop")

    # end-state hard kills (lint: gsap_exit_missing_hard_kill) for the elements whose own
    # window ends before the composition does
    if style == "line":
        js.append(f"      tl.set(\"#owipe\", {{ opacity: 0 }}, {r(O + 0.6)});")

    info = {"style": style, "background": bg_kind, "logo": logo_file, "variant": variant,
            "hole": bool(hole), "face": [round(fx), round(fy)], "face_how": how,
            "circle": {"x": round(fcx), "y": round(fcy), "r": round(R0)},
            "land": {"x": round(land_x, 1), "y": round(land_y, 1), "r": round(land_r, 1)},
            "logo_box": [round(lx), round(ly), round(lx + lw), round(ly + lh)],
            "voice_ref_lufs": voice_ref}
    return {"style": style, "start": O, "end": E, "aroll_end": round(aroll_end, 3),
            "freeze_start": fz0, "elements": els, "css": css, "js": js, "sfx": sfx,
            "bed": {"swell_from": r(O + 0.1), "fade_from": r(E - 1.0), "fade_to": r(E - F),
                    "voice_ref": voice_ref},
            "info": info}


def _hole_is_clear(src, h):
    """Is the logo actually transparent at the hole's centre? Logo files often ship with
    the counter filled white, which brand_from_logo.py cannot call a hole — but a hand-
    edited brand.json can. Landing a face under opaque ink hides it completely."""
    size = probe_size(src)
    if not size:
        return True
    w, hh = size
    x = max(0, min(w - 1, int(round(float(h["cx"])))))
    y = max(0, min(hh - 1, int(round(float(h["cy"])))))
    r = hfcfg.run(["ffmpeg", "-v", "error", "-i", src, "-vf", f"crop=1:1:{x}:{y},format=rgba",
                   "-f", "rawvideo", "-"], text=False)
    buf = r.stdout
    return not (len(buf) >= 4 and buf[3] > 40)


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _graphemes(s):
    """Base characters with their combining marks (niqqud, accents) kept together, so the
    typewriter never shows a mark without its letter."""
    out = []
    for ch in s:
        if out and unicodedata.combining(ch):
            out[-1] += ch
        else:
            out.append(ch)
    return out


def _char_spans(s):
    # Hebrew letters do not join, so per-character spans keep shaping intact. (Arabic does
    # join — for Arabic, type by word instead; the line style warns about it.)
    return "".join(f'<span class="ch">{"&nbsp;" if c == " " else _esc(c)}</span>'
                   for c in _graphemes(s))


# --------------------------------------------------------------- the bed
def bed_automation(clip_start, base_vol, plan_, swell_to=None):
    """The volume lane that lets the music bed reach the end and RESOLVE.

    The voice is gone in the outro, so the bed may rise a little (to the post-flare level at
    most) and then fade to silence exactly on the last frame — a bed that is still at full
    level on the final frame reads as "the export got cut". Lane times are clip-local.
    Values are absolute gains (HyperFrames: a volume lane REPLACES data-volume)."""
    v = float(base_vol)
    up = float(swell_to) if swell_to else v
    a = max(0.0, plan_["bed"]["swell_from"] - clip_start)
    f0 = max(a + 0.4, plan_["bed"]["fade_from"] - clip_start)
    f1 = max(f0 + 0.2, plan_["bed"]["fade_to"] - clip_start)
    pts = [{"t": 0, "v": round(v, 4)}, {"t": round(a, 3), "v": round(v, 4)},
           {"t": round(a + 0.6, 3), "v": round(up, 4)}, {"t": round(f0, 3), "v": round(up, 4)},
           {"t": round(f1, 3), "v": 0}]
    return json.dumps({"version": 1, "lanes": [{"target": "volume", "points": pts}]},
                      separators=(",", ":"))


def bed_to_extend(music, aroll_end):
    """The id of the music clip that should carry the reel into the outro: the one that
    ends LAST, provided it plays to the A-roll's end (±0.15 s). A bed that stops earlier
    was cut out on purpose (references/sound.md) — leave it alone."""
    best = None
    for s in music or []:
        e = float(s["start"]) + float(s["duration"])
        if e >= aroll_end - 0.15 and (best is None or e > best[1]):
            best = (s.get("id"), e)
    return best[0] if best else None


def extend_bed(s, plan_, cfg):
    """(duration, extra attributes) for the bed clip so it reaches the outro's last frame
    and RESOLVES on it.

    The level in the outro is MEASURED, never a ratio: a track's own loudness moves (the
    test bed rose 8 dB between its intro and its chorus), so "+5 dB" can land the outro
    louder than the speech. The bed's momentary loudness over the exact source window the
    outro will play is measured at unity, and its gain is set so it sits
    audio.outro_bed_below_voice (9 dB) under the voice reference — under the cues — present, never louder
    than the reel's own speech — clamped to [half the spoken-part gain, max(gain,
    bgm_after_flare)]. Then it fades to silence ending on the last frame.
    Verified: a HyperFrames volume lane REPLACES data-volume (lane 0.5 on data-volume 0.5
    measures −6 dB, not −12), so the lane carries absolute gains."""
    start = float(s["start"])
    want = round(plan_["end"] - start, 3)
    ms = float(s.get("media_start") or 0)
    src = s["src"]
    avail = media_duration(src) - ms if os.path.exists(src) else want
    dur = want
    if avail and avail + 1e-3 < want:
        dur = round(avail, 3)
        print(f"  outro: ! bed {s.get('id')} runs out at {start + avail:.2f}s, "
              f"{want - avail:.2f}s before the outro ends — pick a later media_start or a "
              f"longer track")
    au = cfg.get("audio", {})
    vol = float(s.get("volume") if s.get("volume") is not None
                else au.get("bgm_under_voice", 0.056))
    out_vol = vol
    vref = plan_["bed"].get("voice_ref")
    a = ms + max(0.0, plan_["start"] - start)
    b = ms + min(dur, plan_["bed"]["fade_from"] - start)      # the part before the fade
    if vref is not None and os.path.exists(src) and b - a > 0.5:
        m = sorted(momentary(src, f"atrim={a:.3f}:{b:.3f},"))
        if m:
            # the LOUD end of the window (90th percentile), not its median: a bed that
            # crescendos inside the outro measured +6.5 dB over target with the median
            med = m[int(0.9 * (len(m) - 1))]
            target = vref - float(au.get("outro_bed_below_voice", OUTRO_BED_BELOW_DB))
            want_vol = 10 ** ((target - med) / 20.0)
            out_vol = max(vol * 0.5, min(want_vol, max(vol, float(au.get("bgm_after_flare", 0.135)))))
            if want_vol < vol * 0.5 - 1e-4:
                print(f"  outro: ! the bed gets {20 * math.log10(vol * 0.5 / want_vol):.1f} dB too "
                      f"loud in the outro even at half its gain (the track builds there) — "
                      f"lower its volume, or pick a media_start whose outro window is calmer")
            print(f"  outro: bed {s.get('id')} {vol:g} under the voice → {out_vol:.3f} in the "
                  f"outro (window {med:.1f} LUFS at unity, voice {vref:.1f}, target {target:.1f})")
    bed = dict(plan_["bed"], fade_to=min(plan_["bed"]["fade_to"], start + dur))
    lane = bed_automation(start, vol, dict(plan_, bed=bed), out_vol)
    return dur, f" data-automation='{lane}'"


# ---------------------------------------------------------------- preview
PREVIEW_HTML = """<!doctype html>
<!-- GENERATED by scripts/outro.py preview — a test bench for the outro, not a deliverable. -->
<html lang="{lang}">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={W}, height={H}" />
    <title>outro preview</title>
    <script src="assets/vendor/gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      :root {{ --brand-font: "{family}"; }}
{brand_css}
      @font-face {{ font-family: "{family}"; src: url("{font}") format("truetype");
                   font-weight: 100 1000; }}
      html, body {{ margin: 0; width: {W}px; height: {H}px; overflow: hidden; background: #000; }}
      #root {{ position: relative; width: {W}px; height: {H}px; overflow: hidden; background: #000; }}
      video {{ position: absolute; inset: 0; width: {W}px; height: {H}px; object-fit: cover; }}
      #aroll {{ z-index: 20; transform-origin: 50% 30%; }}
{css}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-width="{W}" data-height="{H}" data-duration="{end}">
{body}
    </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
      tl.set("#aroll", {{ scale: 1.02, y: 0 }}, 0);
{js}
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""


def brand_vars_css(colors):
    """:root brand tokens from brand.json colours, for a page without brand.css."""
    c = {k: v for k, v in (colors or {}).items() if isinstance(v, str) and v.startswith("#")}
    rgb = lambda h: ", ".join(str(x) for x in _rgb(h))
    out = ["      :root {"]
    for k, var in (("primary", "--brand-primary"), ("secondary", "--brand-secondary"),
                   ("accent", "--brand-accent")):
        if k in c:
            out.append(f"        {var}: {c[k]}; {var}-rgb: {rgb(c[k])};")
    for k, var in (("ink", "--brand-ink"), ("paper", "--brand-paper"),
                   ("on_primary", "--brand-on-primary"), ("hl_on_dark", "--hl-on-dark"),
                   ("hl_on_light", "--hl-on-light"), ("grad_a", "--brand-grad-a"),
                   ("grad_b", "--brand-grad-b")):
        if k in c:
            out.append(f"        {var}: {c[k]};")
    out.append("      }")
    return "\n".join(out)


def emit(el, idx):
    """One element spec → html (the preview's stand-in for build_index.clip())."""
    a = (f'<{el["tag"]} id="{el["id"]}" class="clip {el.get("cls", "")}" '
         f'data-start="{el["start"]}" data-duration="{el["dur"]}" data-track-index="{idx}"'
         f'{el.get("extra", "")}')
    return f'      {a}>{el.get("inner", "")}</{el["tag"]}>'


def preview(a):
    cfg = hfcfg.load(a.config)
    W, H = cfg["project"]["width"], cfg["project"]["height"]
    fps = cfg["project"].get("fps", 25)
    out = os.path.abspath(a.out)
    os.makedirs(os.path.join(out, "assets", "vendor"), exist_ok=True)
    os.makedirs(os.path.join(out, "assets", "fonts"), exist_ok=True)
    os.makedirs(os.path.join(out, "brand"), exist_ok=True)

    # brand: copy brand.json + its logo files into the bench, same relative paths
    bpath = a.brand or brand_json_path(cfg)
    if not os.path.exists(bpath):
        sys.exit(f"{bpath} not found — pass --brand path/to/brand.json")
    bj = json.load(open(bpath, encoding="utf-8"))
    bdir = os.path.dirname(os.path.abspath(bpath))
    proj = os.getcwd()
    for key in ("src", "trimmed", "on_dark", "on_light"):
        p = (bj.get("logo") or {}).get(key)
        if not p:
            continue
        # the files next to THIS brand.json win — a project brand/ folder may hold another
        # client's logo under the same relative path
        cand = [os.path.join(bdir, os.path.basename(p)), os.path.join(proj, p)]
        srcf = next((c for c in cand if os.path.exists(c)), None)
        if srcf:
            dst = os.path.join(out, p)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(srcf, dst)
    shutil.copy2(bpath, os.path.join(out, "brand", "brand.json"))
    css_src = os.path.join(bdir, "brand.css")
    brand_css = open(css_src, encoding="utf-8").read() if os.path.exists(css_src) \
        else brand_vars_css(bj.get("colors"))

    # gsap + font
    for rel in ("assets/vendor/gsap.min.js",):
        for base in (proj, hfcfg.SKILL_DIR):
            if os.path.exists(os.path.join(base, rel)):
                shutil.copy2(os.path.join(base, rel), os.path.join(out, rel))
                break
        else:
            sys.exit(f"{rel} missing — run scripts/setup_assets.py")
    family = cfg["brand"]["font_family"]
    font = None
    for fd in (cfg["brand"].get("font_dir", "assets/fonts"),
               os.path.join(hfcfg.SKILL_DIR, "assets", "fonts")):
        if os.path.isdir(fd):
            fs = sorted(f for f in os.listdir(fd) if f.lower().endswith((".ttf", ".otf")))
            pick = next((f for f in fs if os.path.splitext(f)[0].lower() == family.lower()),
                        fs[0] if fs else None)
            if pick:
                shutil.copy2(os.path.join(fd, pick), os.path.join(out, "assets", "fonts", pick))
                font = f"assets/fonts/{pick}"
                if os.path.splitext(pick)[0].lower() != family.lower():
                    family = os.path.splitext(pick)[0].split("[")[0].split("-")[0]
                break
    if not font:
        sys.exit("no font found — run scripts/setup_assets.py")

    # A-roll lead-in: the last 1.6 s of the given A-roll, or a synthetic stand-in
    lead = 1.6
    dst = os.path.join(out, "assets", "aroll.mp4")
    if a.aroll and os.path.exists(a.aroll):
        d = media_duration(a.aroll)
        n = int(round(lead * fps))
        r = hfcfg.run(["ffmpeg", "-v", "error", "-y", "-sseof", f"-{lead + 0.5:.2f}", "-i", a.aroll,
                       "-vf", f"fps={fps},scale={W}:{H}:force_original_aspect_ratio=increase,"
                              f"crop={W}:{H},format=yuv420p",
                       "-frames:v", str(n), "-af", f"atrim=end={lead}", "-c:v", "libx264",
                       "-crf", "14", "-preset", "fast", "-colorspace", "bt709",
                       "-color_primaries", "bt709", "-color_trc", "bt709",
                       "-c:a", "aac", "-t", f"{lead}", dst])
        if r.returncode:
            sys.exit(f"could not cut the A-roll lead-in\n{r.stderr[-400:]}")
        _ = d
    else:
        geq = ("r='if(lt(pow((X-540)/190,2)+pow((Y-560)/240,2),1),226,40+Y/30)':"
               "g='if(lt(pow((X-540)/190,2)+pow((Y-560)/240,2),1),170,52+Y/40)':"
               "b='if(lt(pow((X-540)/190,2)+pow((Y-560)/240,2),1),138,66+Y/24)'")
        r = hfcfg.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                       f"color=c=black:s={W}x{H}:d={lead}:r={fps}", "-f", "lavfi", "-i",
                       f"anullsrc=r=48000:cl=stereo", "-t", f"{lead}",
                       "-vf", f"geq={geq},format=yuv420p", "-c:v", "libx264", "-crf", "18",
                       "-c:a", "aac", "-shortest", dst])
        if r.returncode:
            sys.exit(f"could not make a synthetic A-roll\n{r.stderr[-400:]}")
    aroll_end = round(hfcfg.frames(dst) / float(fps), 3)

    ocfg = dict(cfg)
    ocfg["outro"] = dict(cfg.get("outro", {}))
    ocfg["outro"].update({"enabled": True, "style": a.style})
    for k in ("tagline", "handle", "background"):
        v = getattr(a, k, None)
        if v:
            ocfg["outro"][k] = v
    ocfg["audio"] = dict(cfg.get("audio", {}))
    ocfg["audio"]["sfx_dir"] = "assets/sfx"
    ocfg["brand"] = dict(cfg["brand"])
    ocfg["brand"]["json"] = "brand/brand.json"
    media = {"aroll": "assets/aroll.mp4"}
    # the bench's A-roll sits in the beat map's full-screen state, like a real reel's end
    p = plan(ocfg, media, aroll_end, None, root=out, state=(1.02, 0.0))
    body = [f'      <video id="aroll" class="clip" src="assets/aroll.mp4" data-start="0" '
            f'data-duration="{aroll_end}" data-track-index="1" playsinline data-has-audio="true"></video>']
    for i, el in enumerate(p["elements"]):
        body.append(emit(el, 10 + i))
    for i, s in enumerate(p["sfx"]):
        body.append(f'      <audio id="{s["id"]}" src="{s["src"]}" data-start="{s["start"]}" '
                    f'data-duration="{s["duration"]}" data-track-index="{30 + i}" '
                    f'data-volume="{s["volume"]}"></audio>')
    html = PREVIEW_HTML.format(lang=cfg["language"].get("code", "he"), W=W, H=H, family=family,
                               font=font, brand_css=brand_css, css=p["css"],
                               end=p["end"], body="\n".join(body), js="\n".join(p["js"]))
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(html)
    json.dump(p["info"], open(os.path.join(out, "outro_info.json"), "w"), indent=1)
    print(f"  preview → {out}/index.html  ({p['style']}, {p['end']:.2f}s)")
    if a.render:
        mp4 = os.path.join(out, "renders", f"outro_{p['style']}.mp4")
        r = hfcfg.run(["npx", "hyperframes", "render", "--quality", "draft", "--fps",
                       str(int(fps)) if int(fps) in (24, 30, 60) else "30", "-o", mp4],
                      cwd=out, capture_output=True)
        if r.returncode:
            sys.exit(f"render failed:\n{(r.stdout or '')[-1500:]}\n{(r.stderr or '')[-1500:]}")
        print(f"  rendered → {mp4}")
    return 0


# -------------------------------------------------------------------- cli
def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("cmd", choices=["plan", "face", "sfx", "preview"])
    ap.add_argument("--style", choices=STYLES, default=None)
    ap.add_argument("--brand", help="brand.json (default: next to brand.css)")
    ap.add_argument("--aroll", default=None)
    ap.add_argument("--media", default="media.json")
    ap.add_argument("--bounds", default="src/bounds.json")
    ap.add_argument("--tagline")
    ap.add_argument("--handle")
    ap.add_argument("--background", choices=["paper", "dark"])
    ap.add_argument("--out", default="build/outro_preview")
    ap.add_argument("--render", action="store_true", help="preview: also render a draft mp4")
    ap.add_argument("--force", action="store_true", help="sfx: rebuild the cues")
    a = ap.parse_args()
    hfcfg.require("ffmpeg", "ffprobe")
    cfg = hfcfg.load(a.config)

    if a.cmd == "sfx":
        d = cfg.get("audio", {}).get("sfx_dir", "assets/sfx")
        made = synth_sfx(d, force=a.force)
        print(f"  {len(made)} outro cue(s) written to {d}")
        return 0
    if a.cmd == "face":
        aroll = a.aroll or "assets/aroll.mp4"
        end = media_duration(aroll)
        x, y, w, how = measure_face(aroll, end, cfg["project"]["width"], cfg["project"]["height"])
        print(json.dumps({"face": [round(x), round(y)], "width": round(w), "how": how}))
        return 0
    if a.cmd == "preview":
        if not a.style:
            a.style = (cfg.get("outro") or {}).get("style") or "portal"
        if not a.aroll and os.path.exists("assets/aroll.mp4"):
            a.aroll = "assets/aroll.mp4"
        return preview(a)
    # plan
    media = json.load(open(a.media, encoding="utf-8")) if os.path.exists(a.media) else {}
    if a.style:
        media["outro"] = dict(media.get("outro") or {}, style=a.style)
    bounds = json.load(open(a.bounds, encoding="utf-8")) if os.path.exists(a.bounds) else {}
    aroll_end = bounds.get("total") or media_duration(media.get("aroll", "assets/aroll.mp4"))
    beatmap, _ = hfcfg.load_beats()
    p = plan(cfg, media, float(aroll_end), beatmap, brand_path=a.brand)
    if not p:
        print("  outro is off (config outro.enabled / media.json \"outro\")")
        return 0
    print(json.dumps({k: v for k, v in p.items() if k not in ("css",)}, indent=1,
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
