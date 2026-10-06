#!/usr/bin/env python3
"""Colour maths for brand-derived motion graphics. Pure standard library.

Why this exists: a palette is not enough. A brand's gold that looks rich on a navy card
is unreadable as text on a bone/cream "paper" scene (a real note from a past edit: the
fix was a DARKER gold for text on light backgrounds). So every brand colour the build
uses as TEXT gets a role and a contrast-checked variant, and the maths that produces
those variants lives here, testable on its own:

  * hex <-> rgb, sRGB <-> CIE Lab / LCh (D65)
  * Delta E (CIEDE2000) — "are these two clusters really different colours?"
  * WCAG 2.x relative luminance + contrast ratio — "can this be read?"
  * adjust_to_contrast() — move a colour along L* (keeping its hue, trimming chroma only
    as far as the sRGB gamut forces) until it reaches a target contrast against a
    background. Moving in Lab keeps "gold" reading as gold, where an HSL darken drifts
    it towards olive/brown.
  * a small DETERMINISTIC weighted k-means (same logo in, same palette out — a brand
    file that changes between runs is a bug report waiting to happen).
"""
from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

RGB = Tuple[int, int, int]
LAB = Tuple[float, float, float]

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


# ------------------------------------------------------------------ hex / rgb

def hex_to_rgb(h: str) -> RGB:
    """'#abc', 'abc', '#aabbcc' or '#aabbccdd' (alpha dropped) -> (r, g, b)."""
    s = h.strip().lstrip("#")
    if len(s) in (3, 4):
        s = "".join(c * 2 for c in s[:3])
    if len(s) == 8:
        s = s[:6]
    if len(s) != 6:
        raise ValueError(f"not a hex colour: {h!r}")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def rgb_to_hex(rgb: Sequence[float]) -> str:
    r, g, b = (int(round(max(0, min(255, v)))) for v in rgb[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def as_rgb(c) -> RGB:
    """Accept '#hex' or an (r, g, b) tuple."""
    if isinstance(c, str):
        return hex_to_rgb(c)
    return (int(c[0]), int(c[1]), int(c[2]))


def rgb_triplet(c) -> str:
    """'r, g, b' — the form `rgba(var(--brand-primary-rgb), .3)` needs."""
    r, g, b = as_rgb(c)
    return f"{r}, {g}, {b}"


# ----------------------------------------------------------- sRGB <-> linear

def _lin(v: float) -> float:
    v = v / 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _unlin(v: float) -> float:
    v = 12.92 * v if v <= 0.0031308 else 1.055 * (v ** (1 / 2.4)) - 0.055
    return v * 255.0


def luminance(c) -> float:
    """WCAG 2.x relative luminance, 0 (black) .. 1 (white)."""
    r, g, b = as_rgb(c)
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a, b) -> float:
    """WCAG contrast ratio, 1.0 .. 21.0. 4.5 = body text, 3.0 = large/bold text."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------- Lab / LCh

_XN, _YN, _ZN = 0.95047, 1.0, 1.08883          # D65 reference white


def _f(t: float) -> float:
    return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116


def _finv(t: float) -> float:
    return t ** 3 if t ** 3 > 216 / 24389 else (116 * t - 16) / (24389 / 27)


def rgb_to_lab(c) -> LAB:
    r, g, b = (_lin(v) for v in as_rgb(c))
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / _XN
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / _YN
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / _ZN
    fx, fy, fz = _f(x), _f(y), _f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def lab_to_rgb_unclipped(lab: Sequence[float]) -> Tuple[float, float, float]:
    """Lab -> sRGB 0..255 WITHOUT clipping (values outside 0..255 = out of gamut)."""
    L, a, b = lab
    fy = (L + 16) / 116
    fx = fy + a / 500
    fz = fy - b / 200
    x, y, z = _finv(fx) * _XN, _finv(fy) * _YN, _finv(fz) * _ZN
    rl = 3.2404542 * x - 1.5371385 * y - 0.4985314 * z
    gl = -0.9692660 * x + 1.8760108 * y + 0.0415560 * z
    bl = 0.0556434 * x - 0.2040259 * y + 1.0572252 * z

    def enc(v):
        if v < 0:
            return -_unlin(-v)
        return _unlin(v)
    return (enc(rl), enc(gl), enc(bl))


def in_gamut(lab: Sequence[float], tol: float = 0.5) -> bool:
    return all(-tol <= v <= 255 + tol for v in lab_to_rgb_unclipped(lab))


def lab_to_rgb(lab: Sequence[float]) -> RGB:
    """Lab -> sRGB, clipped. Prefer lch_to_rgb_mapped() when chroma may be out of gamut."""
    r, g, b = lab_to_rgb_unclipped(lab)
    return (int(round(max(0, min(255, r)))), int(round(max(0, min(255, g)))),
            int(round(max(0, min(255, b)))))


def lab_to_lch(lab: Sequence[float]) -> Tuple[float, float, float]:
    L, a, b = lab
    return (L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360)


def lch_to_lab(lch: Sequence[float]) -> LAB:
    L, C, H = lch
    return (L, C * math.cos(math.radians(H)), C * math.sin(math.radians(H)))


def lch_to_rgb_mapped(L: float, C: float, H: float) -> RGB:
    """LCh -> sRGB, reducing CHROMA (never hue or lightness) until it fits the gamut.

    Clipping each channel independently shifts the hue (a dark gold clips to olive);
    trimming chroma keeps the colour recognisably the same colour, just less saturated.
    """
    L = max(0.0, min(100.0, L))
    if in_gamut(lch_to_lab((L, C, H))):
        return lab_to_rgb(lch_to_lab((L, C, H)))
    lo, hi = 0.0, C
    for _ in range(24):
        mid = (lo + hi) / 2
        if in_gamut(lch_to_lab((L, mid, H))):
            lo = mid
        else:
            hi = mid
    return lab_to_rgb(lch_to_lab((L, lo, H)))


def chroma(c) -> float:
    """CIE LCh chroma: ~0 for greys, 30+ reads as clearly coloured, 60+ vivid."""
    return lab_to_lch(rgb_to_lab(c))[1]


def lightness(c) -> float:
    return rgb_to_lab(c)[0]


def hue(c) -> float:
    return lab_to_lch(rgb_to_lab(c))[2]


def is_chromatic(c, min_chroma: float = 12.0) -> bool:
    """True when the colour reads as a COLOUR, not as black/white/grey.

    Very dark and very light colours carry little visible chroma even with a non-zero C*,
    so the threshold relaxes at the extremes (a navy at L*13 with C*18 is still navy).
    """
    L, C, _ = lab_to_lch(rgb_to_lab(c))
    if L > 96:
        return C > min_chroma * 1.4
    return C > min_chroma


# ------------------------------------------------------------------ HSL / HSV

def rgb_to_hsl(c) -> Tuple[float, float, float]:
    r, g, b = (v / 255.0 for v in as_rgb(c))
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2
    if mx == mn:
        return (0.0, 0.0, l)
    d = mx - mn
    s = d / (2 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return (h * 60.0, s, l)


def hsl_to_rgb(h: float, s: float, l: float) -> RGB:
    c = (1 - abs(2 * l - 1)) * s
    hp = (h % 360) / 60.0
    x = c * (1 - abs(hp % 2 - 1))
    r1, g1, b1 = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][int(hp) % 6]
    m = l - c / 2
    return (int(round((r1 + m) * 255)), int(round((g1 + m) * 255)), int(round((b1 + m) * 255)))


def saturation(c) -> float:
    """HSV saturation 0..1 — a cheap 'how vivid' that ignores lightness."""
    r, g, b = as_rgb(c)
    mx = max(r, g, b)
    return 0.0 if mx == 0 else (mx - min(r, g, b)) / mx


# ------------------------------------------------------------------- Delta E

def delta_e76(a, b) -> float:
    la, lb = rgb_to_lab(a), rgb_to_lab(b)
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(la, lb)))


def delta_e2000(a, b, lab: bool = False) -> float:
    """CIEDE2000. ~1 = just noticeable, ~5 = clearly different shade, 20+ = a different
    colour. Pass lab=True when a and b are already Lab triples."""
    L1, a1, b1 = a if lab else rgb_to_lab(a)
    L2, a2, b2 = b if lab else rgb_to_lab(b)
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dLp = L2 - L1
    dCp = C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lbp = (L1 + L2) / 2
    Cbp = (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360) / 2
    else:
        hbp = (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dtheta = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7))
    Sl = 1 + (0.015 * (Lbp - 50) ** 2) / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1 + 0.045 * Cbp
    Sh = 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dtheta)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2
                     + Rt * (dCp / Sc) * (dHp / Sh))


delta_e = delta_e2000


# ----------------------------------------------------------- adjust / mix

def set_lightness(c, L: float) -> RGB:
    """Same hue and chroma (gamut permitting), new L*."""
    l0, C, H = lab_to_lch(rgb_to_lab(c))
    return lch_to_rgb_mapped(L, C, H)


def _deep_lch(L0: float, C: float, H: float, L: float) -> Tuple[float, float, float]:
    """LCh for the colour at L0 taken DOWN to L, corrected so it still reads as itself.

    A yellow/gold darkened at constant hue and chroma turns olive-green — the eye reads dark
    yellow as olive. Designers fix it by hand (the gold-on-bone lesson: #D2AE38 became
    #8E6E16, not an olive). So when darkening, chroma eases off (to at most -40 %) and hues in
    the yellow band (60-110 deg) rotate up to 6 deg towards orange, both in proportion to how
    far L* dropped (chroma x0.8 at a one-third drop). Lightening is left untouched.
    """
    if L >= L0 or L0 <= 0:
        return L, C, H
    f = min(1.0, (L0 - L) / L0)
    C2 = C * max(0.6, 1.0 - 0.55 * f)
    band = max(0.0, 1.0 - abs(H - 85.0) / 25.0)       # 1 at 85 deg, 0 at 60 / 110 deg
    return L, C2, H - 10.0 * f * band


def deepen(c, L: float) -> RGB:
    """`c` at a lower L* with the dark-yellow-goes-olive correction (see _deep_lch)."""
    L0, C, H = lab_to_lch(rgb_to_lab(c))
    return lch_to_rgb_mapped(*_deep_lch(L0, C, H, L))


def lighten(c, amount: float) -> RGB:
    """Raise L* by `amount` (0..100 scale), hue preserved."""
    return set_lightness(c, lightness(c) + amount)


def darken(c, amount: float) -> RGB:
    return set_lightness(c, lightness(c) - amount)


def lighten_hsl(c, amount: float) -> RGB:
    h, s, l = rgb_to_hsl(c)
    return hsl_to_rgb(h, s, max(0.0, min(1.0, l + amount)))


def darken_hsl(c, amount: float) -> RGB:
    return lighten_hsl(c, -amount)


def mix(a, b, t: float) -> RGB:
    """Linear-light mix: t=0 -> a, t=1 -> b (gamma-correct, no muddy midpoint)."""
    ra, rb = as_rgb(a), as_rgb(b)
    return tuple(int(round(_unlin(_lin(x) * (1 - t) + _lin(y) * t))) for x, y in zip(ra, rb))


def adjust_to_contrast(c, against, target: float = 4.5, direction: Optional[str] = None,
                       ) -> RGB:
    """The closest colour to `c` (same hue) that reaches `target` contrast vs `against`.

    direction: 'darker' / 'lighter' / None (auto: darken against a light background,
    lighten against a dark one). The search walks L* only, so the result keeps the
    brand hue; chroma is trimmed only where the sRGB gamut forces it. If the colour
    already passes it is returned unchanged — we never move a brand colour for nothing.
    Darkening applies the dark-yellow correction of _deep_lch (gold must not go olive).
    """
    rgb = as_rgb(c)
    if contrast(rgb, against) >= target:
        return rgb
    if direction is None:
        direction = "darker" if luminance(against) > 0.18 else "lighter"
    L0, C, H = lab_to_lch(rgb_to_lab(rgb))

    def at(L):
        return lch_to_rgb_mapped(*_deep_lch(L0, C, H, L))

    lo, hi = (0.0, L0) if direction == "darker" else (L0, 100.0)
    end = at(lo if direction == "darker" else hi)
    if contrast(end, against) < target:
        # Even black/white in that direction cannot reach it — return the extreme.
        return BLACK if direction == "darker" else WHITE
    best = end
    for _ in range(30):
        mid = (lo + hi) / 2
        cand = at(mid)
        ok = contrast(cand, against) >= target
        if direction == "darker":
            if ok:
                best, lo = cand, mid
            else:
                hi = mid
        else:
            if ok:
                best, hi = cand, mid
            else:
                lo = mid
    # rounding to 8-bit can land a hair under the target — nudge one more step
    step = -0.25 if direction == "darker" else 0.25
    L = lightness(best)
    while contrast(best, against) < target and 0 < L < 100:
        L += step
        best = at(L)
    return best


def best_text_on(bg, candidates=("#0B0B0F", "#FFFFFF")) -> str:
    """Whichever candidate reads best on `bg`."""
    return max(candidates, key=lambda t: contrast(t, bg))


# ------------------------------------------------------------------ k-means

def kmeans(points: Sequence[Sequence[float]], k: int,
           weights: Optional[Sequence[float]] = None, iters: int = 30,
           ) -> Tuple[List[Tuple[float, ...]], List[float], List[int]]:
    """Deterministic weighted k-means.

    Seeding is k-means++ made deterministic: the first centre is the heaviest point, each
    next centre is the point maximising weight * distance^2 to the nearest chosen centre.
    No randomness anywhere, so the same logo always yields the same palette.

    Returns (centres, total weight per centre, label per point). Empty clusters are dropped.
    """
    n = len(points)
    if n == 0:
        return [], [], []
    w = list(weights) if weights is not None else [1.0] * n
    dim = len(points[0])
    k = max(1, min(k, n))

    def d2(p, q):
        return sum((p[i] - q[i]) ** 2 for i in range(dim))

    first = max(range(n), key=lambda i: (w[i], -i))
    centres = [tuple(points[first])]
    nearest = [d2(p, centres[0]) for p in points]
    while len(centres) < k:
        j = max(range(n), key=lambda i: (w[i] * nearest[i], -i))
        if nearest[j] <= 1e-9:
            break
        centres.append(tuple(points[j]))
        for i, p in enumerate(points):
            dd = d2(p, centres[-1])
            if dd < nearest[i]:
                nearest[i] = dd
    labels = [0] * n
    for _ in range(iters):
        changed = False
        for i, p in enumerate(points):
            best = min(range(len(centres)), key=lambda c: d2(p, centres[c]))
            if best != labels[i]:
                labels[i] = best
                changed = True
        sums = [[0.0] * dim for _ in centres]
        tot = [0.0] * len(centres)
        for i, p in enumerate(points):
            c = labels[i]
            tot[c] += w[i]
            for d in range(dim):
                sums[c][d] += p[d] * w[i]
        centres = [tuple(s[d] / tot[c] for d in range(dim)) if tot[c] > 0 else centres[c]
                   for c, s in enumerate(sums)]
        if not changed and _ > 0:
            break
    tot = [0.0] * len(centres)
    for i in range(n):
        tot[labels[i]] += w[i]
    keep = [c for c in range(len(centres)) if tot[c] > 0]
    remap = {c: j for j, c in enumerate(keep)}
    return ([centres[c] for c in keep], [tot[c] for c in keep],
            [remap.get(l, 0) for l in labels])


def histogram(pixels: Iterable[Tuple[int, int, int]], bits: int = 5) -> List[Tuple[RGB, float]]:
    """Quantise to `bits` per channel; return [(mean rgb of the bin, count)].

    Clustering a few thousand bins instead of every pixel is what makes pure-Python
    k-means fast enough; the bin MEAN (not its corner) keeps the exact brand colour.
    """
    sh = 8 - bits
    acc = {}
    for r, g, b in pixels:
        key = (r >> sh, g >> sh, b >> sh)
        e = acc.get(key)
        if e is None:
            acc[key] = [r, g, b, 1]
        else:
            e[0] += r
            e[1] += g
            e[2] += b
            e[3] += 1
    return [((e[0] / e[3], e[1] / e[3], e[2] / e[3]), float(e[3])) for e in acc.values()]


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if len(args) == 2:
        a, b = args
        print(f"{a} vs {b}: contrast {contrast(a, b):.2f}:1  dE2000 {delta_e2000(a, b):.1f}")
        print(f"  {a} darkened for 4.5:1 on {b}: {rgb_to_hex(adjust_to_contrast(a, b, 4.5))}")
    else:
        print("usage: colorkit.py <#fg> <#bg>   # contrast, dE and the 4.5:1 fix")
