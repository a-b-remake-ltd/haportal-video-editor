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

Four styles, all ≈3.4-4.5 s after speech, all brand-coloured:
  gate    (default when the logo has a MARK with an opening or a hole — brand_from_logo.py
          writes logo.mark)  "the frame becomes the logo": the footage closes into a door
          shaped like the mark's opening (an arched door for an arch, an oval for a ring),
          flies into it, the mark draws itself around it, the speaker "steps through" into
          a brand light, and the words slide out from behind the mark. 4.5 s.
  portal  (default otherwise)  speaker → circle around the face → flies into the logo's
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
          (--render on the portal also PROVES the circle drew: 3 frames, pixel test)
  python3 scripts/outro.py selftest                   # negative tests of the outro gates
"""
from __future__ import annotations

import json
import math
import os
import re
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
DURATION = {"gate": 4.5, "portal": 4.4, "line": 3.8, "impact": 3.4}
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


# ------------------------------------------------- tweened CSS strings (GSAP)
def cssn(v):
    """A number for INSIDE a tweened CSS string, written the way JavaScript prints it
    ("340", "117.479", never "340.0" or ".5").

    WHY: GSAP interpolates a string like clipPath number by number, and finds each END
    number's unit as `token.substr(String(parseFloat(token)).length)`. Python's
    f"{340.0}px" gives "340.0px": parseFloat → 340, String(340) is 3 characters, so the
    "unit" comes out as ".0px", which never equals the start's "px" (the browser hands
    GSAP the start already normalised to "1465px"). GSAP then runs a unit conversion that
    yields garbage, the browser rejects every in-between value, and the clip-path JUMPS
    from the first value to the last when the tween ends. That is why the portal's circle
    never rendered: the frame stayed full, then popped to the small circle. Any value that
    happens to be whole (a clamped door top of 30.0) breaks the same way."""
    x = round(float(v), 3)
    if x == 0:
        return "0"
    if x == int(x):
        return str(int(x))
    return repr(x)


_TWEEN_STR = re.compile(r'(clipPath|filter|transformOrigin)\s*:\s*"([^"]*)"')
_CSS_NUM = re.compile(r'(?<![\w#.])([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)([a-z%]*)')


def tween_string_problems(js_lines):
    """The gate for every string GSAP tweens in the outro timeline. Two ways a CSS string
    silently stops interpolating (the tween JUMPS at its end instead):
      * a number not in JavaScript's own form ("340.0px", ".5px", "1e-05px") — see cssn();
      * an inset() the browser can shorten: Chrome serialises inset() like the margin
        shorthand, so equal right/left (or radii 2 and 4) drop values, the two ends end up
        with different number counts, and GSAP pairs them wrongly. The gate and line
        styles keep every value distinct for exactly this reason.
    Returns a list of human-readable problems ([] = clean)."""
    out = []
    for ln in js_lines:
        for prop, val in _TWEEN_STR.findall(ln):
            for num, unit in _CSS_NUM.findall(val):
                if num != cssn(num):
                    out.append(f"{prop} \"{val}\": {num}{unit} is not written the way "
                               f"JavaScript prints it ({cssn(num)}{unit}) — GSAP reads its "
                               f"unit as {(num + unit)[len(cssn(num)):]!r} and the tween jumps")
            for body in re.findall(r"inset\(([^)]*)\)", val):
                nums = [float(n) for n, _ in _CSS_NUM.findall(body)]
                if len(nums) >= 4 and nums[1] == nums[3]:
                    out.append(f"{prop} \"{val}\": inset right == left — the browser "
                               f"shortens it and GSAP cannot interpolate")
                if "round" in body and len(nums) >= 8 and nums[5] == nums[7]:
                    out.append(f"{prop} \"{val}\": inset radii 2 == 4 — the browser "
                               f"shortens it and GSAP cannot interpolate")
    return out


def check_tween_strings(js_lines, where="outro"):
    bad = tween_string_problems(js_lines)
    if bad:
        sys.exit(f"{where}: {len(bad)} tweened CSS string(s) would not interpolate:\n  " +
                 "\n  ".join(bad))


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
    # "auto" (or no style) is resolved in plan() once the brand is loaded: gate when the
    # logo has a mark with an opening or a hole, portal otherwise.
    style = base.get("style") or "auto"
    if style not in STYLES + ("auto",):
        sys.exit(f"outro.style {style!r} — choose one of {', '.join(STYLES)} or auto")
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
                # RGB rule OR the YCbCr chroma box (Cb 77-127, Cr 137-175): under cool or
                # side light the RGB rule keeps only the warm half of the face (measured:
                # the centre landed 100 px off on a sunset-lit shot); chroma is far less
                # sensitive to the light's colour and level.
                cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
                cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
                if (r > 95 and r > g + 16 and g > b + 6 and 40 < r - b < 130) or \
                        (r > 60 and 77 <= cb <= 127 and 137 <= cr <= 175):
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
        # Centre on the FOREHEAD band (hairline to ~brow): the whole skin run includes an
        # ear or the neck on one side when the head is turned, which pulled the centre
        # ~30 px off the nose on a test shot. The forehead has no ears.
        mids = []
        for y in range(top, min(r1, top + max(2, int(0.3 * (b_ - a))))):
            row = [x for x in range(a, b_) if mask[y * sx + x]]
            if len(row) >= 0.3 * (b_ - a):
                mids.append((row[0] + row[-1] + 1) / 2.0)
        if len(mids) >= 2:
            cx = sorted(mids)[len(mids) // 2] * k
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
        if style == "auto":
            style = "gate" if gate_available(lg) else "portal"
        if style == "gate" and not gate_available(lg, any_shape=True):
            sys.exit("outro.style gate needs logo.mark in brand.json — rerun "
                     "scripts/brand_from_logo.py on the logo (it found no distinct mark: "
                     "choose portal, line or impact)")
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
        if style == "gate":
            return _plan_gate(cfg, st, brand, col, aroll, aroll_end, O, E, F, fz0, s0, y0,
                              g, W, H, lang_dir, root, say)

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
            f"      tl.fromTo({FOOT}, {{ clipPath: \"circle({cssn(Rbig)}px at {cssn(fcx)}px {cssn(fcy)}px)\" }}, "
            f"{{ clipPath: \"circle({cssn(R0)}px at {cssn(fcx)}px {cssn(fcy)}px)\", duration: 0.6, "
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
                f"clipPath: \"circle({cssn(hr * 0.98)}px at {cssn(lox)}px {cssn(loy)}px)\" }}, "
                f"{{ rotation: 0, scale: 1, clipPath: \"circle({cssn(ring)}px at {cssn(lox)}px {cssn(loy)}px)\", "
                f"duration: 0.62, ease: \"power3.out\", immediateRender: false }}, {r(O + 0.86)});",
                f"      tl.fromTo(\"#ologo\", {{ clipPath: \"circle({cssn(ring)}px at {cssn(lox)}px {cssn(loy)}px)\" }}, "
                f"{{ clipPath: \"circle({cssn(cover_r)}px at {cssn(lox)}px {cssn(loy)}px)\", duration: 0.75, "
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
                f"      tl.fromTo(\"#ologo\", {{ scale: 1.06, clipPath: \"circle(0px at {cssn(lox)}px {cssn(loy)}px)\" }}, "
                f"{{ scale: 1, clipPath: \"circle({cssn(iris_end - 1.5)}px at {cssn(lox)}px {cssn(loy)}px)\", "
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
        # distinct values on both ends: the browser collapses "inset(0px 0px 0px 0px)" to
        # "inset(0px)" and GSAP then cannot interpolate (see _plan_gate)
        inset_to = (f"inset(0px {W}.01px 0.02px 0.03px)" if rtl
                    else f"inset(0px 0.01px 0.02px {W}.03px)")
        drift = -60 if rtl else 60
        js += [
            f"      tl.fromTo(\"#owipe\", {{ x: {x_from} }}, {{ x: {x_to}, duration: 0.55, "
            f"ease: \"power2.inOut\", immediateRender: false }}, {r(O)});",
            f"      tl.fromTo({FOOT}, {{ clipPath: \"inset(0px 0.01px 0.02px 0.03px)\" }}, "
            f"{{ clipPath: \"{inset_to}\", duration: 0.55, ease: \"power2.inOut\", "
            f"immediateRender: false }}, {r(O)});",
            f"      tl.fromTo({FOOT}, {{ x: 0, y: {r(y0)}, scale: {s0} }}, {{ x: {drift}, y: {r(y0)}, "
            f"scale: {s0}, duration: 0.55, ease: \"power2.in\", immediateRender: false }}, {r(O)});",
            f"      tl.set(\"#ofreeze\", {{ opacity: 0 }}, {r(O + 0.6)});",
            f"      tl.set(\"#ologo\", {{ opacity: 1 }}, {r(O + 0.4)});",
            f"      tl.fromTo(\"#ologo\", {{ x: {-drift * 0.6}, "
            f"clipPath: \"{'inset(0% 0.01% 0.02% 100%)' if rtl else 'inset(0% 100% 0.02% 0.03%)'}\" }}, "
            f"{{ x: 0, clipPath: \"inset(0% 0.01% 0.02% 0.03%)\", duration: 0.8, ease: \"power3.out\", "
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
    if style == "portal":
        # what `preview --render` needs to PROVE the circle drew (check_circle_render)
        info["circle_close"] = {
            "x": r(fcx), "y": r(fcy), "r_from": Rbig, "r_to": r(R0), "t0": r(O + 0.02),
            "dur": 0.6, "scale": s0, "origin": [r(oxo), r(oyo)],
            "bg": ({"kind": "dark", "a": col["grad_a"], "b": col["grad_b"], "angle": 170}
                   if bg_kind == "dark" else {"kind": "solid", "hex": bg_hex})}
    check_tween_strings(js, f"outro ({style})")
    return {"style": style, "start": O, "end": E, "aroll_end": round(aroll_end, 3),
            "freeze_start": fz0, "elements": els, "css": css, "js": js, "sfx": sfx,
            "bed": {"swell_from": r(O + 0.1), "fade_from": r(E - 1.0), "fade_to": r(E - F),
                    "voice_ref": voice_ref},
            "info": info}


# ---------------------------------------------------------------------- gate
# Sizes for the gate lockup. Wider than the portal's on purpose: the mark is only part of
# the logo, and its opening has to hold a face that still reads on a phone.
GATE_AREA = 190000
GATE_MAX_W = 660
GATE_MAX_H = 420
GATE_MAX_H_STACKED = 580
GATE_MIN_OPEN = 84          # px on screen: the opening's width, the door's width on landing
GATE_DOOR_W = (440, 600)    # the door around head and shoulders, in composition px
GATE_DOOR_K = 1.95          # door width = 1.95 face widths (the reference: 540 px for a 280 px face)
GATE_ORBS = 7
# The background the logo sits on in the gate: the last frame, blurred and dimmed ~90 %.
GATE_BG = "#10161c"
# The cue the sound pipeline places from its own library (scripts/sfx.py), with the
# reference base volumes; the outro's synthesised stand-ins play until it does.
GATE_CUES = (("soft_whoosh", 0.05, 0.30, "page"), ("portal_suck", 1.12, 0.30, "rush"),
             ("logo_sting", 1.70, 0.26, "shimmer"))


def gate_available(lg, any_shape=False):
    """Does brand.json carry a mark the gate can fly into? By default only a mark with an
    opening (arch) or a hole makes gate the AUTOMATIC choice; a solid mark ("none") still
    works when the user asks for gate (the door lands under the mark and it draws over)."""
    m = (lg or {}).get("mark") or {}
    shape = (m.get("opening") or {}).get("shape")
    if not m or not (m.get("files") or {}).get("mark"):
        return False
    return shape in ("arch", "hole", "none") if any_shape else shape in ("arch", "hole")


def _face_of(o, W, H):
    """(x, y, width) from one framing record, in composition px. Accepts {cx,cy,w},
    {x,y,w|width}, [x, y(, w)], and normalised 0-1 values."""
    if o is None:
        return None
    if isinstance(o, (list, tuple)) and len(o) >= 2:
        x, y, w = float(o[0]), float(o[1]), float(o[2]) if len(o) > 2 else 0.0
    elif isinstance(o, dict):
        x = o.get("cx", o.get("x"))
        y = o.get("cy", o.get("y"))
        w = o.get("w", o.get("width", o.get("face_w", 0)))
        if x is None or y is None:
            return None
        x, y, w = float(x), float(y), float(w or 0)
    else:
        return None
    if 0 < x <= 1.0 and 0 < y <= 1.0:
        x, y, w = x * W, y * H, w * W
    return x, y, w


def read_framing(path, t_end, W, H):
    """The face from build/framing.json (the framing map written before the edit is
    designed — references/layout.md). The map is the single source of truth for where the
    speaker is; the outro's own skin-mask measurement is only the fallback. Tolerant of the
    record's shape: a top-level face / face_center(+face_width), or a list of timed
    samples ("samples" / "frames" / "faces", each with "t"), nearest to the A-roll end."""
    if not path or not os.path.exists(path):
        return None
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(d, dict):
        for k in ("samples", "frames", "faces", "timeline"):
            seq = d.get(k)
            if isinstance(seq, list) and seq and isinstance(seq[0], dict):
                best = min(seq, key=lambda s: abs(float(s.get("t", s.get("time", 0))) - t_end))
                f = _face_of(best.get("face", best), W, H)
                if f:
                    return f
        if "face" in d:
            f = _face_of(d["face"], W, H)
            if f:
                if not f[2] and d.get("face_width"):
                    f = (f[0], f[1], float(d["face_width"]))
                return f
        if "face_center" in d:
            f = _face_of(d["face_center"], W, H)
            if f:
                return f[0], f[1], float(d.get("face_width", d.get("face_w", 0)) or 0)
    return None


def _gate_orbs(door, gx0, gy0, gx1, gy1, light):
    """Seven soft light orbs around the door — positions from a FIXED seed in Python (no
    Math.random() in the page: renders must be identical), inside the safe zone, off the
    door, spread apart. Colours: the brand light and two warm bokeh tints."""
    import random
    rng = random.Random(7)
    L, T, Rr, B = door
    cols = [_mix(light, "#ffffff", 0.55), "#FFE2B8", _mix(light, "#ffffff", 0.7), "#FFD7A0",
            "#DCE8EE", _mix(light, "#ffffff", 0.6), "#DCE8EE"]
    sizes = [46, 64, 48, 44, 34, 30, 26]
    out, tries = [], 0
    while len(out) < GATE_ORBS and tries < 4000:
        tries += 1
        d = sizes[len(out)]
        x = rng.uniform(gx0 + 30 + d / 2, gx1 - 30 - d / 2)
        y = rng.uniform(gy0 + 40 + d / 2, gy1 - 60 - d / 2)
        if L - 50 < x < Rr + 50 and T - 50 < y < B + 30:
            continue
        if any(math.hypot(x - a, y - b) < (160 if tries < 2000 else 90) for a, b, _, _ in out):
            continue
        out.append((x, y, d, cols[len(out)]))
    return out


def _plan_gate(cfg, st, brand, col, aroll, aroll_end, O, E, F, fz0, s0, y0, g, W, H,
               lang_dir, root, say):
    """The gate outro (references/outro.md, "gate"). Geometry first, then markup, CSS,
    timeline and sound — all in composition px. Runs with cwd = the project root."""
    r = lambda v: round(float(v), 3)
    gx0, gy0, gx1, gy1 = g["safe"]
    cxs = g["center_x"]
    lg = brand["logo"]
    mk = lg["mark"]
    op = mk["opening"]
    shape = op.get("shape", "none")
    files = mk.get("files") or {}
    light = col.get("hl_on_dark") or col["primary"]
    lr, lgc, lb = _rgb(light)
    light_rgb = f"{lr},{lgc},{lb}"

    # ------------------------------------------------------------ the lockup
    c0x, c0y, c1x, c1y = mk.get("content") or [0, 0, lg["w"], lg["h"]]
    cw, ch = float(c1x - c0x), float(c1y - c0y)
    asp = cw / ch
    lw = min(GATE_MAX_W, math.sqrt(GATE_AREA * asp))
    lh = lw / asp
    # a stacked lockup (mark over the words) is tall and narrow: equal area would leave its
    # words tiny, so it may use more of the safe zone's height
    max_h = GATE_MAX_H if asp >= 1.0 else GATE_MAX_H_STACKED
    if lh > max_h:
        lw, lh = max_h * asp, max_h
    sc = lw / cw
    if float(op["w"]) * sc < GATE_MIN_OPEN:
        grow = min(GATE_MIN_OPEN / (float(op["w"]) * sc), (gx1 - gx0 - 60) / lw,
                   (GATE_MAX_H + 140) / lh)
        lw, lh, sc = lw * grow, lh * grow, sc * grow
    tagline = (st.get("tagline") or "").strip()
    words = tagline.split()
    tfs = 0
    if words:
        n = len(tagline)
        tfs = int(max(34, min(62, (gx1 - gx0 - 60) / (0.5 * n))))
        if 0.5 * n * 34 > gx1 - gx0 - 20:
            say(f"tagline is {n} chars — too long for one line inside the safe zone; "
                f"shorten it (≤ ~45 chars)")
    tag_gap = round(0.62 * tfs) if words else 0
    block_below = (tag_gap + round(1.2 * tfs)) if words else 0
    cy = float(st.get("center_y", 860))
    ly = cy - lh / 2
    ly = max(gy0 + 40, min(ly, gy1 - 40 - lh - block_below))
    lx = cxs - lw / 2
    # Grid shift: a lockup that pokes into the right rail (or past the left margin) moves
    # as ONE piece — every coordinate below is derived after the shift, so the door's
    # landing, the orbs' sink target and the words all move with it.
    dx = 0.0
    if lx + lw > gx1 - 12:
        dx = (gx1 - 12) - (lx + lw)
    if lx + dx < gx0 + 12:
        dx = (gx0 + 12) - lx
    lx += dx

    def S(x, y):
        return lx + (float(x) - c0x) * sc, ly + (float(y) - c0y) * sc

    mx, my = S(mk["x"], mk["y"])
    mw, mh = mk["w"] * sc, mk["h"] * sc
    ocx, otop = S(op["cx"], op["top"])
    ow, oh = float(op["w"]) * sc, float(op["h"]) * sc
    if shape == "arch":
        e = 0.04 * ow                     # overscan: the door's edge slides under the ink
        Lw, Lh = ow + 2 * e, oh + e
        Lx, Ly = ocx - Lw / 2, otop - e
        sink = (ocx, otop + oh / 2)
    elif shape == "hole":
        e = 0.05 * ow
        Lw, Lh = ow + 2 * e, oh + 2 * e
        Lx, Ly = ocx - Lw / 2, otop - e
        sink = (ocx, otop + oh / 2)
    else:                                 # a solid mark: the door lands UNDER it
        Lw, Lh = 0.8 * mw, 0.8 * mh
        Lx, Ly = mx + 0.1 * mw, my + 0.1 * mh
        sink = (mx + mw / 2, my + mh / 2)
    A = Lw / Lh

    # ------------------------------------------------------------- the door
    fr = st.get("face")
    if fr:
        fx, fy, fw, how = float(fr[0]), float(fr[1]), 0.0, "config"
    else:
        got = read_framing(os.path.join("build", "framing.json"), aroll_end, W, H)
        if got:
            (fx, fy, fw), how = got, "build/framing.json"
        else:
            fx, fy, fw, how = measure_face(aroll, aroll_end, W, H)
    Dw = max(GATE_DOOR_W[0], min(GATE_DOOR_W[1], GATE_DOOR_K * fw)) if fw else 540.0
    Dh = Dw / A
    if Dh > 0.62 * H:
        Dh = 0.62 * H
        Dw = Dh * A
    dcx = min(max(fx, Dw / 2 + 20), W - Dw / 2 - 20)
    T = min(max(fy - Dh / 2, 30.0), H - 30 - Dh)
    dcy = T + Dh / 2
    Ld, Rd, Bd = dcx - Dw / 2, W - (dcx + Dw / 2), H - (T + Dh)
    # The browser NORMALISES a clip-path string before GSAP interpolates it: equal values
    # collapse ("inset(0px 0px 0px 0px round 0px 0px 0px 0px)" reads back as "inset(0px)"),
    # the two ends then have different number counts and GSAP jumps at the END instead of
    # tweening — the door popped in fully closed on a render. So every one of the eight
    # numbers is made distinct by a sub-pixel epsilon (invisible), on both ends. One radius
    # per corner too: the elliptical "a b c d / e f g h" form normalises the same way.
    def inset(vals, corners):
        v = [cssn(x + 0.01 * i) for i, x in enumerate(list(vals) + list(corners))]
        return (f"inset({v[0]}px {v[1]}px {v[2]}px {v[3]}px "
                f"round {v[4]}px {v[5]}px {v[6]}px {v[7]}px)")
    if shape == "arch":
        corners = (Dw / 2, Dw / 2, 0, 0)
    elif shape == "hole":
        corners = (min(Dw, Dh) / 2,) * 4   # a round hole → a circle; an oval → a stadium
    else:
        corners = (0.2 * min(Dw, Dh),) * 4
    full = inset((0, 0, 0, 0), (0, 0, 0, 0))
    door = inset((T, Rd, Bd, Ld), corners)
    # Fly the door into the opening. With transform-origin o (the door centre) and scale s,
    # a point p lands at o + (p - o)·s + t. Pin the door's top-centre p = (dcx, T) to the
    # landing's top-centre q:  t = q - (o + (p - o)·s).
    s1 = Lw / Dw
    qx, qy = Lx + Lw / 2, Ly
    tx = qx - (dcx + (dcx - dcx) * s1)
    ty = qy - (dcy + (T - dcy) * s1)
    say(f"gate  start {O:.2f}s → end {E:.2f}s  mark {shape} ({mk.get('how')}), "
        f"face {fx:.0f},{fy:.0f} ({how}); door {Dw:.0f}x{Dh:.0f} at {Ld:.0f},{T:.0f} → "
        f"{Lw:.1f}x{Lh:.1f} at {Lx:.1f},{Ly:.1f} (s {s1:.4f}, t {tx:.1f},{ty:.1f})"
        + (f"; grid shift {dx:+.0f}px" if dx else ""))

    # --------------------------------------------------------------- files
    freeze = extract_last_frame(aroll, "assets/outro_last.png", W, H)
    sfx_dir = cfg.get("audio", {}).get("sfx_dir", "assets/sfx")
    synth_sfx(sfx_dir)
    voice_ref = voice_reference(aroll)

    def ondark(key, need, dst):
        """The part's own colours on the dark end background when they read; a white
        silhouette when they do not (a navy wordmark on a dimmed frame vanishes)."""
        src = files.get(key)
        if not src or not os.path.exists(src):
            sys.exit(f"brand.json logo.mark.files.{key} = {src!r} not found — rerun "
                     f"scripts/brand_from_logo.py")
        cov = logo_coverage(src, [GATE_BG])[GATE_BG]
        if cov >= need:
            return src, "colour"
        return silhouette(src, "#ffffff", dst), f"white ({cov:.0%} read on dark)"

    mark_file, mark_var = ondark("mark", 0.6, "assets/outro/og_mark.png")
    parts = {}
    for side in ("left", "right", "below", "above"):
        pr = (mk.get("parts") or {}).get(side)
        if pr and files.get(side):
            f, var = ondark(side, 0.85, f"assets/outro/og_word_{side}.png")
            px_, py_ = S(pr["x"], pr["y"])
            parts[side] = dict(file=f, var=var, x=px_, y=py_, w=pr["w"] * sc, h=pr["h"] * sc)
    say(f"gate  mark {mark_var}; words " +
        (", ".join(f"{k} {v['var']}" for k, v in parts.items()) or "none"))

    # ------------------------------------------------------------ elements
    FOOT = '"#aroll, #ofreeze"'
    span = r(E - O)
    els = [dict(tag="div", id="og-bg", cls="og-bg", start=O, dur=span,
                extra=' data-grid="bleed" data-layout-ignore',
                inner=f'<img id="og-bgimg" src="{freeze}" alt="" /><div id="og-dim"></div>'),
           dict(tag="img", id="ofreeze", cls="ofreeze", start=fz0, dur=r(E - fz0),
                extra=f' src="{freeze}" alt="" data-grid="bleed" data-layout-ignore')]
    inner = ['<div id="og-in">']
    fl_d = max(300.0, min(700.0, 5.4 * Lw))
    inner.append('<div id="og-flash" data-grid="bleed"></div>')
    if shape in ("arch", "hole"):
        inner.append('<div id="og-light" data-grid="bleed"></div>')
    word_js = []
    for side, p in parts.items():
        if side == "left":
            wx, wy = 0.0, p["y"] - 80
            ww, wh = mx + 0.04 * mw, p["h"] + 160
            frm = ("x", (wx + ww) - p["x"] + 8)
        elif side == "right":
            wx, wy = mx + 0.96 * mw, p["y"] - 80
            ww, wh = W - wx, p["h"] + 160
            frm = ("x", -((p["x"] + p["w"]) - wx) - 8)
        elif side == "below":
            wx, wy = 0.0, my + 0.96 * mh
            ww, wh = float(W), H - wy
            frm = ("y", -((p["y"] + p["h"]) - wy) - 8)
        else:
            wx, wy = 0.0, 0.0
            ww, wh = float(W), my + 0.04 * mh
            frm = ("y", (wy + wh) - p["y"] + 8)
        wid = f"og-w{side[0]}"
        inner.append(f'<div class="og-wrap" data-grid="bleed" style="left:{r(wx)}px;top:{r(wy)}px;'
                     f'width:{r(ww)}px;height:{r(wh)}px">'
                     f'<img id="{wid}" class="og-word" src="{p["file"]}" alt="" '
                     f'style="left:{r(p["x"] - wx)}px;top:{r(p["y"] - wy)}px;'
                     f'width:{r(p["w"])}px;height:{r(p["h"])}px" /></div>')
        ax, d0 = frm
        word_js.append(f"      tl.fromTo(\"#{wid}\", {{ {ax}: {r(d0)}, opacity: 1, filter: \"blur(10px)\" }}, "
                       f"{{ {ax}: 0, opacity: 1, filter: \"blur(0px)\", duration: 0.6, "
                       f"ease: \"expo.out\", immediateRender: false }}, {r(O + 1.5)});")
    # the mark: its own pixels, revealed by a stroke MASK drawn along the mark's traced
    # centreline (two halves rising from the base to the top, like a pen drawing an arch)
    pad = 40
    vb = f"{-pad} {-pad} {r(mw + 2 * pad)} {r(mh + 2 * pad)}"
    paths = []
    if shape == "arch":
        oxl = (float(op["cx"]) - float(op["w"]) / 2 - mk["x"]) * sc
        oxr = (float(op["cx"]) + float(op["w"]) / 2 - mk["x"]) * sc
        sw = max(4.0, (oxl + (mw - oxr)) / 2)
        xL, xR = oxl / 2, (oxr + mw) / 2
        rc = (xR - xL) / 2
        cxm = (xL + xR) / 2
        acy = (float(op["top"]) - mk["y"]) * sc + ow / 2
        yb = mh + 2
        paths = [f"M{r(xL)} {r(yb)} V{r(acy)} A{r(rc)} {r(rc)} 0 0 1 {r(cxm)} {r(acy - rc)}",
                 f"M{r(xR)} {r(yb)} V{r(acy)} A{r(rc)} {r(rc)} 0 0 0 {r(cxm)} {r(acy - rc)}"]
    elif shape == "hole":
        hcx = (float(op["cx"]) - mk["x"]) * sc
        hcy = (float(op.get("cy", float(op["top"]) + float(op["h"]) / 2)) - mk["y"]) * sc
        hr = ow / 2
        R = min(mw, mh) / 2
        sw = max(4.0, R - hr)
        rc = (hr + R) / 2
        paths = [f"M{r(hcx)} {r(hcy + rc)} A{r(rc)} {r(rc)} 0 0 1 {r(hcx)} {r(hcy - rc)}",
                 f"M{r(hcx)} {r(hcy + rc)} A{r(rc)} {r(rc)} 0 0 0 {r(hcx)} {r(hcy - rc)}"]
    if paths:
        mask = (f'<defs><mask id="og-mask" maskUnits="userSpaceOnUse" x="{-pad}" y="{-pad}" '
                f'width="{r(mw + 2 * pad)}" height="{r(mh + 2 * pad)}">' +
                "".join(f'<path id="og-d{i + 1}" class="og-draw" d="{d}" pathLength="100" '
                        f'stroke-width="{r(sw * 1.9)}" />' for i, d in enumerate(paths)) +
                f'<rect id="og-mfull" x="{-pad}" y="{-pad}" width="{r(mw + 2 * pad)}" '
                f'height="{r(mh + 2 * pad)}" fill="#ffffff" /></mask></defs>')
        img = (f'<image href="{mark_file}" x="0" y="0" width="{r(mw)}" height="{r(mh)}" '
               f'preserveAspectRatio="none" mask="url(#og-mask)" />')
    else:
        mask = ""
        img = (f'<image href="{mark_file}" x="0" y="0" width="{r(mw)}" height="{r(mh)}" '
               f'preserveAspectRatio="none" />')
    inner.append(f'<svg id="og-mark" class="og-mark" width="{r(mw + 2 * pad)}" '
                 f'height="{r(mh + 2 * pad)}" viewBox="{vb}" '
                 f'style="left:{r(mx - pad)}px;top:{r(my - pad)}px">{mask}{img}</svg>')
    tg_y = ly + lh + tag_gap
    if words:
        spans = " ".join(f'<span id="og-tw{i}" class="og-tw {"og-tw1" if i == 0 else "og-tw2"}">'
                         f'{_esc(w_)}</span>' for i, w_ in enumerate(words))
        inner.append(f'<div id="og-tag">{spans}</div>')
    orbs = _gate_orbs((Ld, T, W - Rd, H - Bd), gx0, gy0, gx1, gy1, light)
    for i, (ox_, oy_, d, c) in enumerate(orbs):
        inner.append(f'<i id="og-orb{i}" class="og-orb" data-grid="bleed" style="left:{r(ox_ - d / 2)}px;'
                     f'top:{r(oy_ - d / 2)}px;width:{d}px;height:{d}px;background:'
                     f'{_mix(c, "#ffffff", 0.5)};box-shadow:0 0 {d}px {round(d / 3)}px {c}aa"></i>')
    inner.append('</div>')
    els.append(dict(tag="div", id="og-lock", cls="og-lock", start=O, dur=span,
                    inner="".join(inner), extra=' data-layout-allow-overflow'))
    els.append(dict(tag="div", id="og-fade", cls="og-fade", start=O, dur=span,
                    extra=' data-grid="bleed" data-layout-ignore'))

    # ------------------------------------------------------------------ css
    if shape == "arch":
        li_x, li_y, li_w, li_h = ocx - ow / 2, otop, ow, oh
        li_r = f"{r(ow / 2)}px {r(ow / 2)}px 0 0"
    else:
        li_x, li_y, li_w, li_h = ocx - ow / 2, otop, ow, oh
        li_r = "50%"
    mark_extra = "" if paths else "#og-mark { clip-path: inset(100% 0.01% 0.02% 0.03%); }"
    face_pct = (round(100 * dcx / W), round(100 * dcy / H))
    css = string.Template(open(os.path.join(TEMPLATE_DIR, "gate.css"),
                               encoding="utf-8").read()).substitute(
        W=W, H=H, bgw=W + 120, bgh=H + 120, dim_x=face_pct[0], dim_y=face_pct[1],
        push_ox=r(sink[0]), push_oy=r(sink[1]),
        fl_x=r(sink[0] - fl_d / 2), fl_y=r(sink[1] - fl_d / 2), fl_d=r(fl_d),
        light_rgb=light_rgb, li_x=r(li_x), li_y=r(li_y), li_w=r(li_w), li_h=r(li_h),
        li_r=li_r, mark_extra=mark_extra, tg_x=gx0, tg_w=gx1 - gx0, tg_y=r(tg_y),
        dir=lang_dir, tfs=tfs, tag_light=_mix(light, "#ffffff", 0.45))

    # ------------------------------------------------------------------- js
    L = lambda t: r(O + t)
    js = [f"      // ---- OUTRO (gate) — scripts/outro.py. Starts {O}s, ends {E}s. The frame "
          f"becomes the logo: door {r(Dw)}x{r(Dh)} → the mark's {shape}.",
          f"      tl.set(\"#ofreeze\", {{ x: 0, y: {r(y0)}, scale: {s0} }}, 0);",
          # reset the camera: no punch-in, sway or panel offset may leak into the outro
          f"      tl.set({FOOT}, {{ transformOrigin: \"{cssn(dcx)}px {cssn(dcy)}px\", x: 0, y: 0, "
          f"scale: 1, rotation: 0 }}, {r(O)});",
          f"      if (document.querySelector(\"#cam\")) tl.set(\"#cam\", {{ x: 0, y: 0, scale: 1, "
          f"rotation: 0 }}, {r(O)});",
          # 1. the blurred, darkened last frame behind
          f"      tl.fromTo(\"#og-bg\", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.2, "
          f"immediateRender: false }}, {r(O)});",
          f"      tl.fromTo(\"#og-bgimg\", {{ scale: 1 }}, {{ scale: 1.3, duration: {span}, "
          f"ease: \"none\", immediateRender: false }}, {r(O)});",
          f"      tl.fromTo(\"#og-dim\", {{ opacity: 0.35 }}, {{ opacity: 1, duration: 1.0, "
          f"ease: \"power2.inOut\", immediateRender: false }}, {L(0.7)});",
          # 2. the frame closes into the door around head and shoulders
          f"      tl.fromTo({FOOT}, {{ clipPath: \"{full}\" }}, {{ clipPath: \"{door}\", "
          f"duration: 0.68, ease: \"power3.inOut\", immediateRender: false }}, {L(0.02)});",
          # 4. the door flies into the mark's opening, with a motion blur
          f"      tl.fromTo({FOOT}, {{ x: 0, y: 0, scale: 1 }}, {{ x: {r(tx)}, y: {r(ty)}, "
          f"scale: {round(s1, 5)}, duration: 0.66, ease: \"power3.inOut\", "
          f"immediateRender: false }}, {L(0.76)});",
          f"      tl.fromTo({FOOT}, {{ filter: \"blur(0px)\" }}, {{ filter: \"blur(14px)\", "
          f"duration: 0.3, ease: \"power2.in\", immediateRender: false }}, {L(0.76)});",
          f"      tl.fromTo({FOOT}, {{ filter: \"blur(14px)\" }}, {{ filter: \"blur(0px)\", "
          f"duration: 0.32, ease: \"power2.out\", immediateRender: false }}, {L(1.08)});"]
    # 3. orbs in, then sucked into the opening (per-orb ids: no overlapping selector tweens)
    for i, (ox_, oy_, d, c) in enumerate(orbs):
        t_in, t_sink = O + 0.25 + 0.03 * i, O + 0.82 + 0.035 * i
        js.append(f"      tl.fromTo(\"#og-orb{i}\", {{ opacity: 0, scale: 0.6 }}, {{ opacity: 1, "
                  f"scale: 1.25, duration: 0.4, ease: \"power2.out\", immediateRender: false }}, "
                  f"{r(t_in)});")
        js.append(f"      tl.fromTo(\"#og-orb{i}\", {{ x: 0, y: 0, scale: 1.25, opacity: 1 }}, "
                  f"{{ x: {r(sink[0] - ox_)}, y: {r(sink[1] - oy_)}, scale: 0.12, opacity: 0.9, "
                  f"duration: 0.55, ease: \"power3.in\", immediateRender: false }}, {r(t_sink)});")
        js.append(f"      tl.fromTo(\"#og-orb{i}\", {{ opacity: 0.9 }}, {{ opacity: 0, duration: 0.08, "
                  f"immediateRender: false }}, {r(t_sink + 0.56)});")
    # 5. the mark draws itself; flash + glow on lock
    if paths:
        js.append(f"      tl.fromTo(\"#og-d1, #og-d2\", {{ strokeDashoffset: 100 }}, "
                  f"{{ strokeDashoffset: 0, duration: 0.34, ease: \"power2.inOut\", "
                  f"immediateRender: false }}, {L(1.14)});")
        # the traced centreline is an approximation (and a mark may have pieces off it —
        # KO's K beside its ring): once drawn, fade the WHOLE mark in, quickly
        js.append(f"      tl.fromTo(\"#og-mfull\", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.16, "
                  f"ease: \"power1.out\", immediateRender: false }}, {L(1.36)});")
    else:
        js.append(f"      tl.fromTo(\"#og-mark\", {{ clipPath: \"inset(100% 0.01% 0.02% 0.03%)\" }}, "
                  f"{{ clipPath: \"inset(0.001% 0.01% 0.02% 0.03%)\", duration: 0.34, ease: \"power2.inOut\", "
                  f"immediateRender: false }}, {L(1.14)});")
    js += [f"      tl.fromTo(\"#og-flash\", {{ opacity: 0, scale: 0.4 }}, {{ opacity: 1, scale: 1, "
           f"duration: 0.12, ease: \"power2.out\", immediateRender: false }}, {L(1.4)});",
           f"      tl.fromTo(\"#og-flash\", {{ opacity: 1, scale: 1 }}, {{ opacity: 0, scale: 1.5, "
           f"duration: 0.7, ease: \"power2.out\", immediateRender: false }}, {L(1.52)});",
           f"      tl.fromTo(\"#og-mark\", {{ filter: \"drop-shadow(0px 0px 0px rgba({light_rgb},0))\" }}, "
           f"{{ filter: \"drop-shadow(0px 0px 26px rgba({light_rgb},0.9))\", duration: 0.2, "
           f"immediateRender: false }}, {L(1.42)});",
           f"      tl.fromTo(\"#og-mark\", {{ filter: \"drop-shadow(0px 0px 26px rgba({light_rgb},0.9))\" }}, "
           f"{{ filter: \"drop-shadow(0px 0px 12px rgba({light_rgb},0.55))\", duration: 0.9, "
           f"ease: \"power2.out\", immediateRender: false }}, {L(1.65)});",
           # 6. the speaker steps through: fades inside the opening as the light fills it
           f"      tl.fromTo({FOOT}, {{ opacity: 1 }}, {{ opacity: 0, duration: 0.5, "
           f"ease: \"power2.inOut\", immediateRender: false }}, {L(1.58)});"]
    if shape in ("arch", "hole"):
        js.append(f"      tl.fromTo(\"#og-light\", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.5, "
                  f"ease: \"power2.inOut\", immediateRender: false }}, {L(1.58)});")
    # 7. the words slide out from behind the mark
    js += word_js
    # 8. the tagline, word by word (the reference's word(): faint grey → colour)
    step = min(0.2, 1.2 / max(1, len(words)))
    for i in range(len(words)):
        js.append(f"      tl.fromTo(\"#og-tw{i}\", {{ opacity: 0.18, filter: \"blur(6px) grayscale(1)\" }}, "
                  f"{{ opacity: 1, filter: \"blur(0px) grayscale(0)\", duration: 0.22, "
                  f"ease: \"power2.out\", immediateRender: false }}, {r(O + 2.12 + step * i)});")
    # 9. slow push on the opening, fade to black
    js.append(f"      tl.fromTo(\"#og-in\", {{ scale: 1 }}, {{ scale: 1.045, duration: {r(E - O - 1.4)}, "
              f"ease: \"none\", immediateRender: false }}, {L(1.4)});")
    js.append(f"      tl.fromTo(\"#og-fade\", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.4, "
              f"ease: \"power1.in\", immediateRender: false }}, {r(E - 0.4 - F)});")

    # ---------------------------------------------------------------- sound
    # Cues for the sound pipeline (scripts/sfx.py places them from its library, scaled to
    # the voice; exempt = deliberate beats that stay on the frame, not slid off words),
    # plus synthesised stand-ins so the outro is never silent until it does.
    cues, sfx = [], []
    for name, t, base, synth in GATE_CUES:
        cues.append({"name": name, "t": r(O + t), "base_vol": base, "exempt": True,
                     "stand_in": f"osfx_{synth}"})
        path = os.path.join(sfx_dir, f"outro_{synth}.wav")
        full_ = path if os.path.isabs(path) else os.path.join(root, path)
        dur = min(wav_duration(full_), E - (O + t))
        if dur > 0.05:
            sfx.append({"id": f"osfx_{synth}", "src": path, "start": r(O + t),
                        "duration": r(dur), "volume": cue_volume(cfg, voice_ref, synth, full_),
                        "cue": name})

    info = {"style": "gate", "mark": shape, "mark_how": mk.get("how"),
            "mark_variant": mark_var, "words": {k: v["var"] for k, v in parts.items()},
            "face": [round(fx), round(fy), round(fw)], "face_how": how,
            "door": {"x": round(Ld), "y": round(T), "w": round(Dw), "h": round(Dh),
                     "origin": [r(dcx), r(dcy)], "clip": door},
            "land": {"x": r(Lx), "y": r(Ly), "w": r(Lw), "h": r(Lh), "scale": round(s1, 5),
                     "tx": r(tx), "ty": r(ty)},
            "opening_screen": {"cx": r(ocx), "top": r(otop), "w": r(ow), "h": r(oh)},
            "logo_box": [round(lx), round(ly), round(lx + lw), round(ly + lh)],
            "grid_shift": r(dx), "tagline_top": r(tg_y) if words else None,
            "voice_ref_lufs": voice_ref,
            # also here so build/outro.json (written from info) carries them to sfx.py
            "cues": cues}
    check_tween_strings(js, "outro (gate)")
    return {"style": "gate", "start": O, "end": E, "aroll_end": round(aroll_end, 3),
            "freeze_start": fz0, "elements": els, "css": css, "js": js, "sfx": sfx,
            "cues": cues,
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


# ------------------------------------------------------- render self-check
CHECK_SCALE = 4             # frames are read at W/4 x H/4: patches, not pixels, matter
CHECK_BG_TOL = 22           # mean |ΔRGB| for "this patch IS the end background"
CHECK_SAME_TOL = 22         # mean |ΔRGB| for "this patch is the footage it was before"


def _power3_inout(p):
    p = max(0.0, min(1.0, p))
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def _bg_at(bg, x, y, W, H):
    """The end background's colour at composition px (x, y): a solid hex, or the dark
    style's linear-gradient(170deg, a, b) evaluated the way CSS lays it out."""
    if bg.get("kind") != "dark":
        return _rgb(bg["hex"])
    a, b = _rgb(bg["a"]), _rgb(bg["b"])
    th = math.radians(float(bg.get("angle", 170)))
    dx, dy = math.sin(th), -math.cos(th)
    L = abs(W * dx) + abs(H * dy)
    t = max(0.0, min(1.0, ((x - W / 2) * dx + (y - H / 2) * dy) / L + 0.5))
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def _patch(buf, w, h, x, y, k=2):
    """Mean RGB of a (2k+1)² patch around (x, y) in a w x h rgb24 frame."""
    acc, n = [0.0, 0.0, 0.0], 0
    for yy in range(max(0, int(y) - k), min(h, int(y) + k + 1)):
        for xx in range(max(0, int(x) - k), min(w, int(x) + k + 1)):
            p = (yy * w + xx) * 3
            for i in range(3):
                acc[i] += buf[p + i]
            n += 1
    return tuple(v / max(1, n) for v in acc)


def _dist(a, b):
    return sum(abs(a[i] - b[i]) for i in range(3)) / 3.0


def circle_frame_verdict(before, frame, cc, t, W, H):
    """Is the portal's closing circle VISIBLE in `frame` (rgb24 at W/CHECK_SCALE)?

    `before` is the same view just before the outro (full footage). At time t the circle
    has radius r(t) (power3.inOut from r_from to r_to), drawn in the A-roll's own box and
    then scaled by the element's transform. Inside it the footage must still be there
    (≈ before); a ring of points well OUTSIDE it must be the end background, not the
    footage. Only points where the footage and the background actually differ can tell the
    two apart; none → inconclusive. Returns (ok | None, detail)."""
    k = CHECK_SCALE
    w, h = W // k, H // k
    p = (t - cc["t0"]) / cc["dur"]
    rad = cc["r_from"] + (cc["r_to"] - cc["r_from"]) * _power3_inout(p)
    s = float(cc.get("scale", 1.0))
    ox, oy = cc["origin"]
    cx, cy = ox + (cc["x"] - ox) * s, oy + (cc["y"] - oy) * s
    rs = rad * s
    inside = _dist(_patch(frame, w, h, cx / k, cy / k), _patch(before, w, h, cx / k, cy / k))
    tested = clipped = 0
    for i in range(24):
        a = 2 * math.pi * i / 24
        x, y = cx + (rs + 70) * math.cos(a), cy + (rs + 70) * math.sin(a)
        if not (16 <= x <= W - 16 and 16 <= y <= H - 16):
            continue
        bg = _bg_at(cc["bg"], x, y, W, H)
        was = _patch(before, w, h, x / k, y / k)
        if _dist(was, bg) < 2 * CHECK_BG_TOL:
            continue                       # footage looks like the background here
        tested += 1
        if _dist(_patch(frame, w, h, x / k, y / k), bg) <= CHECK_BG_TOL:
            clipped += 1
    detail = (f"t {t:.2f}s r {rs:.0f}px: inside Δ{inside:.0f} vs footage, "
              f"outside {clipped}/{tested} points show the background")
    if tested < 3:
        return None, detail + " (inconclusive: footage ≈ background there)"
    return (inside <= CHECK_SAME_TOL and clipped >= 0.8 * tested), detail


def check_circle_render(mp4, cc, W, H, say=print):
    """The self-check `outro.py preview --style portal --render` runs on its own render:
    three frames late in the circle's close must SHOW the circle (footage inside, the end
    background outside). This is the test that would have caught the portal never
    clipping: every frame was full-screen footage until the circle popped in at the end.
    Returns True / False / None (inconclusive)."""
    k = CHECK_SCALE
    before = _raw_rgb(mp4, W // k, H // k, at=cc["t0"] - 0.12)
    verdicts = []
    for f in (0.66, 0.85, 0.96):
        t = cc["t0"] + f * cc["dur"]
        ok, detail = circle_frame_verdict(before, _raw_rgb(mp4, W // k, H // k, at=t),
                                          cc, t, W, H)
        say(f"  circle check {'✓' if ok else ('?' if ok is None else '✗')} {detail}")
        verdicts.append(ok)
    if any(v is False for v in verdicts):
        return False
    return None if all(v is None for v in verdicts) else True


def selftest():
    """Negative tests for the two outro gates — run `outro.py selftest`. Exit 1 on any
    failure. No render needed: the circle check runs on synthetic frames."""
    fails = []

    def expect(cond, what):
        print(f"  {'✓' if cond else '✗'} {what}")
        if not cond:
            fails.append(what)

    # tween strings
    expect(tween_string_problems(['tl.fromTo(a, { clipPath: "circle(1465px at 540px 565.12px)" }, '
                                  '{ clipPath: "circle(340.0px at 540.0px 565.12px)" }, 1);']),
           "rejects circle(340.0px …) — GSAP reads the unit as '.0px'")
    expect(tween_string_problems(['tl.to(a, { clipPath: "circle(.5px at 1px 2px)" }, 1);']),
           "rejects .5px")
    expect(tween_string_problems(['tl.to(a, { clipPath: "inset(0px 4px 0px 4px)" }, 1);']),
           "rejects inset with right == left")
    expect(not tween_string_problems([
        'tl.fromTo(a, { clipPath: "circle(1465px at 540px 565.12px)" }, '
        '{ clipPath: "circle(340px at 540px 565.12px)" }, 1);',
        'tl.to(a, { clipPath: "inset(0px 0.01px 0.02px 1080.03px)", '
        'filter: "drop-shadow(0px 0px 26px rgba(1,2,3,0.9))" }, 1);']),
        "accepts canonical circle / inset / filter strings")
    expect(cssn(340.0) == "340" and cssn(0.5) == "0.5" and cssn(-0.0) == "0"
           and cssn(117.4789) == "117.479", "cssn writes numbers the way JS prints them")

    # circle pixel check on synthetic frames (paper background, a bright footage)
    W, H, k = 1080, 1920, CHECK_SCALE
    w, h = W // k, H // k
    paper = "#f7f5f0"
    cc = {"x": 540, "y": 565, "r_from": 1465, "r_to": 340, "t0": 0.0, "dur": 0.6,
          "scale": 1.02, "origin": [540, 576], "bg": {"kind": "solid", "hex": paper}}

    def frame(rad):
        foot = bytearray(w * h * 3)
        pr = _rgb(paper)
        ox, oy = cc["origin"]
        cx, cy = ox + (cc["x"] - ox) * 1.02, oy + (cc["y"] - oy) * 1.02
        for y in range(h):
            for x in range(w):
                X, Y = x * k + k / 2, y * k + k / 2
                p = (y * w + x) * 3
                if rad is None or math.hypot(X - cx, Y - cy) <= rad * 1.02:
                    foot[p:p + 3] = bytes((40, 60 + int(Y // 30) % 120, 160))
                else:
                    foot[p:p + 3] = bytes(pr)
        return bytes(foot)

    before = frame(None)
    t = 0.57
    rad = 1465 + (340 - 1465) * _power3_inout(t / 0.6)
    ok, d = circle_frame_verdict(before, frame(rad), cc, t, W, H)
    expect(ok is True, f"a drawn circle passes ({d})")
    ok, d = circle_frame_verdict(before, before, cc, t, W, H)
    expect(ok is False, f"full-frame footage (the bug) fails ({d})")
    ok, d = circle_frame_verdict(before, frame(0), cc, t, W, H)
    expect(ok is False, f"an empty frame (circle lost) fails ({d})")
    print(f"  {'all passed' if not fails else str(len(fails)) + ' FAILED'}")
    return 1 if fails else 0


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
      // init like build_index.py: outside the HyperFrames runtime (grid.py check, a
      // plain browser) the registry does not exist and the page would never register
      window.__timelines = window.__timelines || {{}};
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
    lgj = bj.get("logo") or {}
    want = [lgj.get(k) for k in ("src", "trimmed", "on_dark", "on_light", "knocked")]
    want += list(((lgj.get("mark") or {}).get("files") or {}).values())   # the gate's parts
    for p in want:
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
    if p.get("cues"):
        print("  cues for the sound pipeline: " +
              ", ".join(f"{c['name']} @{c['t']}" for c in p["cues"]))
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
        cc = p["info"].get("circle_close")
        if cc:
            v = check_circle_render(mp4, cc, W, H)
            if v is False:
                sys.exit("  ✗ the portal circle does not show in the render — the clip-path "
                         "is not interpolating (see cssn / tween_string_problems)")
            if v is None:
                print("  ? circle check inconclusive (footage looks like the background); "
                      "LOOK at the frames")
    return 0


# -------------------------------------------------------------------- cli
def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("cmd", choices=["plan", "face", "sfx", "preview", "selftest"])
    ap.add_argument("--style", choices=STYLES + ("auto",), default=None)
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
    if a.cmd == "selftest":
        return selftest()
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
            a.style = (cfg.get("outro") or {}).get("style") or "auto"
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
