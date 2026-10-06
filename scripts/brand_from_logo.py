#!/usr/bin/env python3
"""Derive the brand's colour ROLES, logo variants and outro "holes" from a client logo.

Why roles and not a palette: a brand colour has to work as text on dark footage, as text
on a light "paper" scene, as a card background under white text, and as a glow. One hex
cannot do all four — a gold that sings on navy is unreadable on bone (a real note from a
past edit; the fix was a darker gold for text on light). So every role here is
contrast-checked and, where needed, moved along L* (hue kept) until it passes.

  python3 scripts/brand_from_logo.py logo.png  [--out brand/] [--primary #hex] [--accent #hex]

Inputs: PNG / JPG / WEBP (decoded by ffmpeg to raw RGBA — stdlib only) or SVG (colours
parsed from the source AND rasterised by headless Chrome on a transparent background for
pixel weights). Opaque logos (a JPG on a white box) get their background flood-filled
away from the border, so they work like a transparent PNG.

Outputs in --out (the contract the build and the outro consume):
  brand.json        logo paths/size/aspect/holes/monochrome, colour roles, palette,
                    contrast ratios, notes
  brand.css         :root { --brand-primary ... --brand-grad-b }  (build_index.py inlines it)
  logo_trim.png     transparent margins cropped
  logo_on_dark.png  white silhouette, same alpha     (for dark footage / dark cards)
  logo_on_light.png ink silhouette, same alpha       (for paper scenes)
  palette.png       swatch sheet of the roles + contrast-checked text samples — LOOK at it

  mark.png          the logo's SYMBOL alone (see "Mark" below)          — gate outro
  word_left.png / word_right.png / word_below.png / word_above.png
                    the rest of the logo split around the mark, each at its own box

Holes: transparent (or white/background-filled) regions fully enclosed by the logo — the
counter of an "O". The outro shrinks the speaker into a circle and lands it in the largest,
roundest one. Coordinates are in logo_trim.png pixels.

Mark: the logo is split into connected components on its alpha; the mark is the component
that is unlike the letters — a colour most other components do not share, an OPENING (empty
space inside its box reachable from one side only: an arch / gate open at the bottom), an
enclosed hole (a ring), or plain size. Same-coloured pieces inside its box join it. Every
other component is a word part, by where it sits. brand.json logo.mark (trimmed-logo px):
  {x, y, w, h,                               mark.png's box
   colour, how ("colour" | "shape" | "hole" | "only symbol"), score,
   opening: {shape: "arch"|"hole"|"none", cx, top, w, h [, cy] [, side]},
            arch: the space between the legs, top = inside of the arc, h runs to the
            mark's bottom edge (the door's flat foot lands there); hole: the counter's box;
            none: the mark's own box (a solid symbol — the door lands UNDER it)
   parts: {left|right|below|above: {x, y, w, h} | null},
   files: {mark, left, right, below, above},  paths of the PNGs above
   fill: true when the logo paints a translucent light inside the opening (dropped from
         mark.png so the door stays clear; the outro redraws it as its own light),
   content: [x0, y0, x1, y1]                  the union of the parts (no empty margin)}
No distinct mark (every component looks like a letter) → logo.mark is null; the gate outro
is then unavailable and portal / line / impact work as before.
"""
from __future__ import annotations

import json
import math
import os
import re
import struct
import sys
import tempfile
import zlib
from collections import Counter, deque
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402
import colorkit as ck  # noqa: E402

WORK_MAX = 1600          # long side for variants/holes — plenty for a 1080x1920 frame
STATS_TARGET = 360       # long side of the sampling grid for colour statistics
DARK_BG = "#000000"
DEFAULT_INK = "#0B0B0F"
DEFAULT_PAPER = "#F7F5F0"
WHITE = "#FFFFFF"
MONO_SUGGESTIONS = [("#2F6BFF", "electric blue — tech, trust"),
                    ("#F2A93B", "warm amber — energy, optimism"),
                    ("#E5484D", "signal red — urgency, bold")]

CSS_NAMED = {"black": "#000000", "white": "#FFFFFF", "red": "#FF0000", "green": "#008000",
             "blue": "#0000FF", "yellow": "#FFFF00", "orange": "#FFA500", "purple": "#800080",
             "navy": "#000080", "gold": "#FFD700", "gray": "#808080", "grey": "#808080",
             "silver": "#C0C0C0", "maroon": "#800000", "teal": "#008080", "lime": "#00FF00",
             "aqua": "#00FFFF", "cyan": "#00FFFF", "magenta": "#FF00FF", "fuchsia": "#FF00FF",
             "olive": "#808000", "pink": "#FFC0CB", "brown": "#A52A2A", "crimson": "#DC143C",
             "darkblue": "#00008B", "darkred": "#8B0000", "darkgreen": "#006400",
             "goldenrod": "#DAA520", "indigo": "#4B0082", "coral": "#FF7F50",
             "tomato": "#FF6347", "turquoise": "#40E0D0", "royalblue": "#4169E1",
             "midnightblue": "#191970", "whitesmoke": "#F5F5F5", "ivory": "#FFFFF0",
             "beige": "#F5F5DC", "darkgray": "#A9A9A9", "darkgrey": "#A9A9A9",
             "lightgray": "#D3D3D3", "lightgrey": "#D3D3D3", "dimgray": "#696969"}


def say(msg=""):
    print(msg, flush=True)


# ======================================================================= decode

class Img:
    """Flat RGBA buffer. Pure Python on purpose: no PIL/numpy needed to brand a reel."""

    def __init__(self, w: int, h: int, rgba: bytearray):
        self.w, self.h, self.px = w, h, rgba

    def alpha(self) -> bytes:
        return bytes(self.px[3::4])


def _probe_size(path: str) -> Tuple[int, int]:
    out = hfcfg.probe(path, "stream=width,height", stream="v:0")
    try:
        w, h = (int(v) for v in out.split(",")[:2])
        return w, h
    except ValueError:
        sys.exit(f"ffprobe could not read {path} as an image ({out!r})")


def decode(path: str, max_side: int = WORK_MAX) -> Img:
    """Any ffmpeg-readable image -> RGBA, long side capped at max_side (area-averaged)."""
    w, h = _probe_size(path)
    s = min(1.0, max_side / float(max(w, h)))
    tw, th = max(1, int(round(w * s))), max(1, int(round(h * s)))
    r = hfcfg.run(["ffmpeg", "-v", "error", "-i", path, "-frames:v", "1",
                   "-vf", f"scale={tw}:{th}:flags=area,format=rgba",
                   "-f", "rawvideo", "-pix_fmt", "rgba", "-"], text=False)
    if r.returncode != 0 or len(r.stdout) < tw * th * 4:
        sys.exit(f"ffmpeg could not decode {path}:\n{(r.stderr or b'').decode()[-600:]}")
    return Img(tw, th, bytearray(r.stdout[:tw * th * 4]))


def _svg_size(src: str) -> Tuple[float, float]:
    vb = re.search(r"viewBox\s*=\s*[\"']\s*([-\d.eE]+)[\s,]+([-\d.eE]+)[\s,]+([\d.eE]+)"
                   r"[\s,]+([\d.eE]+)", src)
    if vb:
        return float(vb.group(3)), float(vb.group(4))
    wm = re.search(r"<svg[^>]*\swidth\s*=\s*[\"']([\d.]+)", src)
    hm = re.search(r"<svg[^>]*\sheight\s*=\s*[\"']([\d.]+)", src)
    if wm and hm:
        return float(wm.group(1)), float(hm.group(1))
    return 1000.0, 1000.0


def svg_colours(src: str) -> List[str]:
    """Every explicit colour in the SVG source (fill / stroke / stop-color / style / CSS)."""
    out = []
    pat = re.compile(r"(?:fill|stroke|stop-color|color|flood-color)\s*[:=]\s*[\"']?\s*"
                     r"(#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|[a-zA-Z]+)")
    for m in pat.finditer(src):
        v = m.group(1).strip()
        hx = None
        if v.startswith("#"):
            try:
                hx = ck.rgb_to_hex(ck.hex_to_rgb(v))
            except ValueError:
                hx = None
        elif v.lower().startswith("rgb"):
            nums = re.findall(r"[\d.]+%?", v)[:3]
            if len(nums) == 3:
                vals = [float(n[:-1]) * 2.55 if n.endswith("%") else float(n) for n in nums]
                hx = ck.rgb_to_hex(vals)
        else:
            hx = CSS_NAMED.get(v.lower())
        if hx and hx not in out:
            out.append(hx)
    return out


def rasterise_svg(path: str, tmpdir: str, long_side: int = 1200) -> str:
    """SVG -> transparent PNG through headless Chrome (correct for gradients, masks, text)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        src = f.read()
    vw, vh = _svg_size(src)
    s = long_side / max(vw, vh)
    w, h = max(1, int(round(vw * s))), max(1, int(round(vh * s)))
    html = os.path.join(tmpdir, "svg.html")
    png = os.path.join(tmpdir, "svg.png")
    with open(html, "w", encoding="utf-8") as f:
        f.write(f'<!doctype html><meta charset="utf-8"><style>html,body{{margin:0;padding:0;'
                f'background:transparent;overflow:hidden}}img{{display:block;width:{w}px;'
                f'height:{h}px}}</style><img src="file://{os.path.abspath(path)}">')
    chrome = hfcfg.chrome_path()
    hfcfg.run([chrome, "--headless", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
               "--allow-file-access-from-files", "--default-background-color=00000000",
               "--virtual-time-budget=1500", f"--screenshot={png}", f"--window-size={w},{h}",
               "file://" + os.path.abspath(html)])
    if not os.path.exists(png):
        sys.exit("Chrome produced no screenshot of the SVG — set CHROME_PATH, or export the "
                 "logo as a transparent PNG")
    return png


# =================================================================== PNG write

def write_png(path: str, img: Img) -> None:
    """Minimal RGBA PNG writer (stdlib)."""
    w, h = img.w, img.h
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)
        raw += img.px[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
        f.write(chunk(b"IEND", b""))


# =========================================================== background removal

def _dist2(px, i, bg) -> int:
    return (px[i] - bg[0]) ** 2 + (px[i + 1] - bg[1]) ** 2 + (px[i + 2] - bg[2]) ** 2


def detect_background(img: Img) -> Tuple[Optional[Tuple[int, int, int]], float]:
    """Dominant colour of the 2-px border ring and the fraction of the ring it covers."""
    w, h, px = img.w, img.h, img.px
    ring = []
    for y in range(h):
        for x in (0, 1, w - 2, w - 1) if w > 3 else range(w):
            ring.append(((y * w + x) * 4))
    for x in range(w):
        for y in (0, 1, h - 2, h - 1) if h > 3 else range(h):
            ring.append(((y * w + x) * 4))
    cnt = Counter((px[i] >> 3, px[i + 1] >> 3, px[i + 2] >> 3) for i in ring)
    (key, _), = cnt.most_common(1)
    members = [i for i in ring if (px[i] >> 3, px[i + 1] >> 3, px[i + 2] >> 3) == key]
    bg = tuple(int(round(sum(px[i + c] for i in members) / len(members))) for c in range(3))
    near = sum(1 for i in ring if _dist2(px, i, bg) <= 30 ** 2)
    return bg, near / float(len(ring))


def remove_background(img: Img, bg, tol_lo: float = 22.0, tol_hi: float = 64.0) -> Img:
    """Flood-fill the background away FROM THE BORDER and derive a soft alpha.

    Connectivity matters: a white counter inside an "O" is not reached from the border, so
    it stays opaque here (the colour version keeps the logo as designed) and is later
    reported as a hole and knocked out of the silhouettes. Edge pixels get a ramped alpha
    and are un-matted (the background's share removed from their colour), so the cut-out
    has no light halo on dark footage.
    """
    w, h, px = img.w, img.h, bytearray(img.px)
    lo2, hi2 = tol_lo ** 2, tol_hi ** 2
    seen = bytearray(w * h)
    dq = deque()
    for x in range(w):
        dq.append(x)
        dq.append((h - 1) * w + x)
    for y in range(h):
        dq.append(y * w)
        dq.append(y * w + w - 1)
    bgr, bgg, bgb = bg
    while dq:
        p = dq.pop()
        if seen[p]:
            continue
        i = p * 4
        d2 = (px[i] - bgr) ** 2 + (px[i + 1] - bgg) ** 2 + (px[i + 2] - bgb) ** 2
        if d2 > hi2:
            continue
        seen[p] = 1
        if d2 <= lo2:
            a = 0.0
        else:
            a = (math.sqrt(d2) - tol_lo) / (tol_hi - tol_lo)
        if a <= 0.0:
            px[i + 3] = 0
        else:
            # un-matte: observed = a*fg + (1-a)*bg  ->  fg = (observed - (1-a)*bg) / a
            for c, b in ((0, bgr), (1, bgg), (2, bgb)):
                px[i + c] = max(0, min(255, int(round((px[i + c] - (1 - a) * b) / a))))
            px[i + 3] = int(round(255 * a * px[i + 3] / 255.0))
        x = p % w
        if x > 0:
            dq.append(p - 1)
        if x < w - 1:
            dq.append(p + 1)
        if p >= w:
            dq.append(p - w)
        if p < (h - 1) * w:
            dq.append(p + w)
    return Img(w, h, px)


# ====================================================================== trim

def bbox(img: Img, thr: int = 8) -> Optional[Tuple[int, int, int, int]]:
    """(x0, y0, x1, y1) exclusive of every pixel with alpha > thr — C-speed via translate."""
    table = bytes(0 if v <= thr else 1 for v in range(256))
    a = img.alpha().translate(table)
    w, h = img.w, img.h
    x0, x1, y0, y1 = w, -1, None, None
    for y in range(h):
        row = a[y * w:(y + 1) * w]
        f = row.find(b"\x01")
        if f < 0:
            continue
        if y0 is None:
            y0 = y
        y1 = y
        x0 = min(x0, f)
        x1 = max(x1, row.rfind(b"\x01"))
    if y0 is None:
        return None
    return x0, y0, x1 + 1, y1 + 1


def crop(img: Img, box) -> Img:
    x0, y0, x1, y1 = box
    w = x1 - x0
    out = bytearray()
    for y in range(y0, y1):
        out += img.px[(y * img.w + x0) * 4:(y * img.w + x1) * 4]
    return Img(w, y1 - y0, out)


# =================================================================== analysis

def sample_colours(img: Img, knock: Optional[bytearray] = None) -> Counter:
    """Exact colours of INTERIOR opaque pixels on a sampling grid.

    A pixel whose 1-px neighbours differ is an anti-aliased edge — a blend of two brand
    colours that would otherwise surface as a fake third colour in the palette.
    """
    w, h, px = img.w, img.h, img.px
    step = max(1, int(math.ceil(max(w, h) / float(STATS_TARGET))))
    out = Counter()
    for y in range(1, h - 1, step):
        for x in range(1, w - 1, step):
            i = (y * w + x) * 4
            if px[i + 3] < 250:
                continue
            r, g, b = px[i], px[i + 1], px[i + 2]
            ok = True
            for j in (i - 4, i + 4, i - w * 4, i + w * 4):
                if px[j + 3] < 250 or abs(px[j] - r) + abs(px[j + 1] - g) + abs(px[j + 2] - b) > 30:
                    ok = False
                    break
            if ok:
                out[(r, g, b)] += 1
    if sum(out.values()) < 50:                  # thin line art: take every opaque pixel
        for y in range(0, h, step):
            for x in range(0, w, step):
                i = (y * w + x) * 4
                if px[i + 3] >= 200:
                    out[(px[i], px[i + 1], px[i + 2])] += 1
    return out


def cluster(colours: Counter, k: int = 8) -> List[dict]:
    """Weighted k-means in Lab over a 5-bit histogram, merged at dE2000 < 8, each cluster
    represented by its most frequent EXACT colour (the designer's hex, not an average)."""
    total = float(sum(colours.values()))
    if not total:
        return []
    hist = ck.histogram(colours.elements(), bits=5) if total < 400000 else \
        ck.histogram([c for c, n in colours.items() for _ in range(max(1, n // 4))], bits=5)
    pts = [ck.rgb_to_lab(rgb) for rgb, _ in hist]
    wts = [n for _, n in hist]
    cents, _, _ = ck.kmeans(pts, min(k, len(pts)), wts)
    # assign every exact colour to its nearest centre
    groups: Dict[int, Counter] = {}
    for rgb, n in colours.items():
        lab = ck.rgb_to_lab(rgb)
        j = min(range(len(cents)), key=lambda c: sum((lab[d] - cents[c][d]) ** 2 for d in range(3)))
        groups.setdefault(j, Counter())[rgb] += n
    cl = []
    for j, g in groups.items():
        n = sum(g.values())
        rep = g.most_common(1)[0][0]
        cl.append({"rgb": rep, "n": n})
    # merge near-duplicates (dE2000 < 8): shading of one ink, not two colours
    cl.sort(key=lambda c: -c["n"])
    merged: List[dict] = []
    for c in cl:
        for m in merged:
            if ck.delta_e2000(c["rgb"], m["rgb"]) < 8:
                m["n"] += c["n"]
                break
        else:
            merged.append(dict(c))
    for m in merged:
        m["share"] = m["n"] / total
        m["hex"] = ck.rgb_to_hex(m["rgb"])
    merged = [m for m in merged if m["share"] >= 0.005]
    merged.sort(key=lambda c: -c["share"])
    return merged


def _is_whiteish(rgb) -> bool:
    L, C, _ = ck.lab_to_lch(ck.rgb_to_lab(rgb))
    return L >= 90 and C <= 10


# ====================================================================== holes

def find_holes(img: Img, bgcol: Optional[Tuple[int, int, int]], white_counts: bool,
               max_side: int = 900) -> Tuple[List[dict], bytearray]:
    """Enclosed empty regions of the TRIMMED logo, plus a full-res knockout mask.

    'Empty' = transparent, or (when the logo is not itself white) an opaque white fill, or
    a pixel still close to a removed background colour. Empty pixels reachable from the
    border are outside; whatever empty area remains is enclosed — a counter. Returns
    (holes sorted largest/roundest first, knockout mask at img resolution where 1 = an
    enclosed white/background fill that silhouettes must drop).
    """
    w, h, px = img.w, img.h, img.px
    kind = bytearray(w * h)          # 0 solid, 1 transparent, 2 white/bg fill
    for p in range(w * h):
        i = p * 4
        if px[i + 3] < 128:
            kind[p] = 1
        elif px[i + 3] >= 200:
            r, g, b = px[i], px[i + 1], px[i + 2]
            if white_counts and r >= 236 and g >= 236 and b >= 236 and \
                    max(r, g, b) - min(r, g, b) <= 14:
                kind[p] = 2
            elif bgcol is not None and (r - bgcol[0]) ** 2 + (g - bgcol[1]) ** 2 + \
                    (b - bgcol[2]) ** 2 <= 40 ** 2:
                kind[p] = 2
    # outside = empty pixels 4-connected to the border (a 1-px virtual frame around it)
    out = bytearray(w * h)
    dq = deque(p for p in list(range(w)) + list(range((h - 1) * w, h * w)) +
               [y * w for y in range(h)] + [y * w + w - 1 for y in range(h)])
    while dq:
        p = dq.pop()
        if out[p] or not kind[p]:
            continue
        out[p] = 1
        x = p % w
        if x > 0:
            dq.append(p - 1)
        if x < w - 1:
            dq.append(p + 1)
        if p >= w:
            dq.append(p - w)
        if p < (h - 1) * w:
            dq.append(p + w)
    # label enclosed components
    label = [0] * (w * h)
    holes = []
    knock = bytearray(w * h)
    nxt = 0
    min_area = max(12, int(0.0004 * w * h))
    for start in range(w * h):
        if not kind[start] or out[start] or label[start]:
            continue
        nxt += 1
        comp = []
        dq = deque([start])
        label[start] = nxt
        while dq:
            p = dq.pop()
            comp.append(p)
            x = p % w
            for q in ((p - 1) if x > 0 else -1, (p + 1) if x < w - 1 else -1,
                      p - w, p + w):
                if 0 <= q < w * h and kind[q] and not out[q] and not label[q]:
                    label[q] = nxt
                    dq.append(q)
        fills = sum(1 for p in comp if kind[p] == 2)
        if fills:
            for p in comp:
                if kind[p] == 2:
                    knock[p] = 1
        area = len(comp)
        if area < min_area:
            continue
        sx = sum(p % w for p in comp)
        sy = sum(p // w for p in comp)
        cx, cy = sx / area, sy / area
        rmax2 = max((p % w - cx) ** 2 + (p // w - cy) ** 2 for p in comp)
        rmax = math.sqrt(rmax2) + 0.5
        boundary_min = float("inf")
        xs = [p % w for p in comp]
        ys = [p // w for p in comp]
        cset = set(comp)
        for p in comp:
            x = p % w
            for q in ((p - 1) if x > 0 else None, (p + 1) if x < w - 1 else None,
                      p - w if p >= w else None, p + w if p < (h - 1) * w else None):
                if q is None or q not in cset:
                    d = math.hypot(x - cx, p // w - cy)
                    if d < boundary_min:
                        boundary_min = d
                    break
        holes.append({
            "cx": round(cx, 1), "cy": round(cy, 1),
            "r": round(math.sqrt(area / math.pi), 1),
            "r_inscribed": round(max(0.0, boundary_min - 0.5), 1),
            "area": area,
            "roundness": round(min(1.0, area / (math.pi * rmax * rmax)), 3),
            "bbox": [min(xs), min(ys), max(xs) + 1, max(ys) + 1],
            "fill": "transparent" if fills * 2 < area else "solid-fill",
        })
    holes.sort(key=lambda o: -(o["area"] * o["roundness"]))
    for o in holes:
        o["cx_n"] = round(o["cx"] / w, 4)
        o["cy_n"] = round(o["cy"] / h, 4)
        o["r_n"] = round(o["r"] / w, 4)
    return holes, knock


# ======================================================================= mark
#
# WHY. The gate outro (scripts/outro.py, style "gate") turns the video frame itself into
# part of the logo: the speaker's frame closes into a door, flies into the logo's SYMBOL,
# and the words slide out from behind it. That needs to know which part of the logo is the
# symbol ("mark") and which parts are words — for ANY logo, not one brand. A mark is the
# component that does not behave like a letter:
#   * its colour differs from most of the other components (a blue arch among navy
#     letters; an orange icon beside a black wordmark),
#   * it has an OPENING — empty space inside its box reachable from one side only (a gate
#     or arch open at the bottom) — or an enclosed HOLE (a ring),
#   * it is big for the row it sits in.
# The letters around it become word_left / word_right (or word_below / word_above for a
# stacked lockup), exported separately so the outro can slide them out from behind it.

MARK_MIN_OPENING = 0.12      # opening area / mark box area for an arch to count
MARK_MIN_HOLE = 0.07         # enclosed hole area / mark box area for a ring to count
MARK_ALPHA = 128             # a pixel belongs to a glyph from this alpha up


def _components(img: Img, knock: Optional[bytearray]):
    """8-connected components of the opaque pixels (alpha >= MARK_ALPHA, knocked-out
    counters excluded). 8-connectivity on purpose: anti-aliased diagonal strokes of one
    glyph touch only at corners. Returns (label array, [component dict])."""
    from array import array
    w, h, px = img.w, img.h, img.px
    n = w * h
    solid = bytearray(n)
    al = px[3::4]
    for p in range(n):
        if al[p] >= MARK_ALPHA and not (knock is not None and knock[p]):
            solid[p] = 1
    lab = array("i", bytes(4 * n))
    comps = []
    for start in range(n):
        if not solid[start] or lab[start]:
            continue
        k = len(comps) + 1
        lab[start] = k
        dq = deque([start])
        x0 = x1 = start % w
        y0 = y1 = start // w
        area = 0
        sr = sg = sb = sn = 0
        while dq:
            p = dq.pop()
            area += 1
            x, y = p % w, p // w
            if x < x0:
                x0 = x
            elif x > x1:
                x1 = x
            if y < y0:
                y0 = y
            elif y > y1:
                y1 = y
            i = p * 4
            if px[i + 3] >= 250:
                sr += px[i]
                sg += px[i + 1]
                sb += px[i + 2]
                sn += 1
            for dy in (-1, 0, 1):
                yy = y + dy
                if yy < 0 or yy >= h:
                    continue
                for dx in (-1, 0, 1):
                    xx = x + dx
                    if (dx or dy) and 0 <= xx < w:
                        q = yy * w + xx
                        if solid[q] and not lab[q]:
                            lab[q] = k
                            dq.append(q)
        if not sn:
            sn = 1
            sr = sg = sb = 128
        comps.append({"id": k, "area": area, "box": [x0, y0, x1 + 1, y1 + 1],
                      "rgb": (round(sr / sn), round(sg / sn), round(sb / sn))})
    return lab, comps


def _shape(lab, w, c):
    """Opening / hole of one component, inside its own box.

    Empty cells of the box are flooded from each side separately. A region reached from
    exactly ONE side is an opening on that side (an arch open at the bottom: the space
    between its legs); a region reached from no side is an enclosed hole. Returns
    {"opening": {...} | None, "hole": {...} | None} in logo pixels."""
    x0, y0, x1, y1 = c["box"]
    bw, bh = x1 - x0, y1 - y0
    k = c["id"]
    n = bw * bh
    if n < 16:
        return {"opening": None, "hole": None}
    empty = bytearray(n)
    for yy in range(bh):
        row = (y0 + yy) * w + x0
        for xx in range(bw):
            if lab[row + xx] != k:
                empty[yy * bw + xx] = 1
    sides = {"top": [x for x in range(bw)],
             "bottom": [(bh - 1) * bw + x for x in range(bw)],
             "left": [y * bw for y in range(bh)],
             "right": [y * bw + bw - 1 for y in range(bh)]}
    reach = {}
    for s, seeds in sides.items():
        seen = bytearray(n)
        dq = deque(q for q in seeds if empty[q])
        for q in dq:
            seen[q] = 1
        while dq:
            p = dq.pop()
            x = p % bw
            for q in ((p - 1) if x > 0 else -1, (p + 1) if x < bw - 1 else -1,
                      p - bw if p >= bw else -1, p + bw if p < n - bw else -1):
                if q >= 0 and empty[q] and not seen[q]:
                    seen[q] = 1
                    dq.append(q)
        reach[s] = seen
    best = None
    for s in sides:
        others = [reach[o] for o in sides if o != s]
        cells = [p for p in range(n) if reach[s][p] and not any(o[p] for o in others)]
        if best is None or len(cells) > len(best[1]):
            best = (s, cells)
    opening = None
    if best and best[1]:
        s, cells = best
        xs = [p % bw for p in cells]
        ys = [p // bw for p in cells]
        ox0, ox1, oy0, oy1 = min(xs), max(xs) + 1, min(ys), max(ys) + 1
        ratio = len(cells) / float(n)
        if s in ("top", "bottom"):
            deep = (oy1 - oy0) / float(bh)
            wide = (ox1 - ox0) / float(bw)
        else:
            deep = (ox1 - ox0) / float(bw)
            wide = (oy1 - oy0) / float(bh)
        if ratio >= 0.03 and deep >= 0.3 and wide >= 0.2:
            # an arch's mouth is open at the bottom: its "top" is the inside of the arc,
            # its height runs to the mark's bottom edge (where the door's flat foot lands)
            opening = {"side": s, "ratio": round(ratio, 3),
                       "x0": x0 + ox0, "x1": x0 + ox1, "y0": y0 + oy0, "y1": y0 + oy1}
    # enclosed: empty, reached from no side — keep the largest connected region
    enc = bytearray(n)
    for p in range(n):
        if empty[p] and not (reach["top"][p] or reach["bottom"][p] or reach["left"][p]
                             or reach["right"][p]):
            enc[p] = 1
    hole, seen = None, bytearray(n)
    for st in range(n):
        if not enc[st] or seen[st]:
            continue
        seen[st] = 1
        dq, cells = deque([st]), []
        while dq:
            p = dq.pop()
            cells.append(p)
            x = p % bw
            for q in ((p - 1) if x > 0 else -1, (p + 1) if x < bw - 1 else -1,
                      p - bw if p >= bw else -1, p + bw if p < n - bw else -1):
                if q >= 0 and enc[q] and not seen[q]:
                    seen[q] = 1
                    dq.append(q)
        if hole is None or len(cells) > hole[0]:
            xs = [p % bw for p in cells]
            ys = [p // bw for p in cells]
            hole = (len(cells), {"ratio": round(len(cells) / float(n), 3),
                                 "x0": x0 + min(xs), "x1": x0 + max(xs) + 1,
                                 "y0": y0 + min(ys), "y1": y0 + max(ys) + 1})
    return {"opening": opening, "hole": hole[1] if hole else None}


def find_mark(img: Img, knock: Optional[bytearray], holes: List[dict], notes: List[str]):
    """Locate the logo's mark and split the rest into word parts. Returns (mark dict or
    None, label array, components, {component id: group name})."""
    w = img.w
    lab, comps = _components(img, knock)
    if not comps:
        return None, lab, comps, {}
    total = float(sum(c["area"] for c in comps))
    big = [c for c in comps if c["area"] >= max(20, 0.004 * total)]
    if not big:
        return None, lab, comps, {}
    amax = max(c["area"] for c in big)
    hs = sorted(c["box"][3] - c["box"][1] for c in big)
    hmed = hs[len(hs) // 2]
    # score every sizeable component
    cands = sorted(big, key=lambda c: -c["area"])[:16]
    for c in cands:
        others = [o for o in big if o is not c]
        uniq = (sum(1 for o in others if ck.delta_e2000(o["rgb"], c["rgb"]) > 15)
                / float(len(others))) if others else 0.0
        sh = _shape(lab, w, c)
        c["_shape"] = sh
        op, ho = sh["opening"], sh["hole"]
        op_r = op["ratio"] if op else 0.0
        ho_r = ho["ratio"] if ho else 0.0
        hrel = (c["box"][3] - c["box"][1]) / float(hmed or 1)
        c["_uniq"], c["_op"], c["_ho"], c["_hrel"] = uniq, op_r, ho_r, hrel
        # a usable opening or hole is worth a flat bonus: between two equally distinct
        # symbols (a solid badge and a ring beside it) the one the door can fly INTO wins
        usable = (op is not None and op["side"] == "bottom" and op_r >= MARK_MIN_OPENING) \
            or ho_r >= MARK_MIN_HOLE
        c["_score"] = (1.2 * uniq + 1.2 * min(0.6, op_r) + 0.8 * min(0.5, ho_r)
                       + (0.5 if usable else 0.0)
                       + 0.5 * math.sqrt(c["area"] / float(amax)) + 0.3 * min(1.5, hrel - 1))
    cands.sort(key=lambda c: -c["_score"])
    top = cands[0]
    lone = len(big) <= 2
    standout = (top["_uniq"] >= 0.6 or
                (max(top["_op"], top["_ho"]) >= MARK_MIN_OPENING and top["_hrel"] >= 1.2) or
                lone)
    if len(cands) > 1 and not top["_uniq"] >= 0.6 and \
            top["_score"] - cands[1]["_score"] < 0.15 and not lone:
        standout = False                 # two look-alikes (the two o's of "photo")
    how = "colour" if top["_uniq"] >= 0.6 else ("shape" if not lone else "only symbol")
    if not standout:
        # fall back to the existing hole logic: the component around the best round hole
        top = None
        for hh in holes:
            if hh.get("roundness", 0) < 0.6:
                continue
            for c in cands:
                ho = c["_shape"]["hole"]
                if ho and ho["x0"] <= hh["cx"] <= ho["x1"] and ho["y0"] <= hh["cy"] <= ho["y1"]:
                    top = c
                    break
            if top:
                how = "hole"
                break
        if top is None:
            notes.append("no distinct mark found (every part looks like a letter) — the "
                         "gate outro is not available; portal/line/impact still are")
            return None, lab, comps, {}
    # the mark may be several pieces of one colour (an icon drawn in parts)
    mx0, my0, mx1, my1 = top["box"]
    ex, ey = 0.15 * (mx1 - mx0), 0.15 * (my1 - my0)
    group = {top["id"]: "mark"}
    for c in comps:
        if c is top:
            continue
        cx, cy = (c["box"][0] + c["box"][2]) / 2.0, (c["box"][1] + c["box"][3]) / 2.0
        if mx0 - ex <= cx <= mx1 + ex and my0 - ey <= cy <= my1 + ey and \
                ck.delta_e2000(c["rgb"], top["rgb"]) < 12:
            group[c["id"]] = "mark"
            mx0, my0 = min(mx0, c["box"][0]), min(my0, c["box"][1])
            mx1, my1 = max(mx1, c["box"][2]), max(my1, c["box"][3])
    # the words around it: beside (vertical overlap with the mark) or stacked
    mh = my1 - my0
    mcx = (mx0 + mx1) / 2.0
    for c in comps:
        if c["id"] in group:
            continue
        cx, cy = (c["box"][0] + c["box"][2]) / 2.0, (c["box"][1] + c["box"][3]) / 2.0
        if my0 - 0.25 * mh <= cy <= my1 + 0.25 * mh:
            group[c["id"]] = "left" if cx < mcx else "right"
        else:
            group[c["id"]] = "below" if cy > my1 else "above"
    sh = top["_shape"]
    op, ho = sh["opening"], sh["hole"]
    if op and op["side"] == "bottom" and op["ratio"] >= MARK_MIN_OPENING and \
            (not ho or op["ratio"] >= ho["ratio"]):
        # the mouth of the arch is the mark's bottom edge
        opening = {"shape": "arch", "cx": round((op["x0"] + op["x1"]) / 2.0, 1),
                   "top": op["y0"], "w": op["x1"] - op["x0"], "h": my1 - op["y0"],
                   "side": "bottom"}
    elif ho and ho["ratio"] >= MARK_MIN_HOLE:
        opening = {"shape": "hole", "cx": round((ho["x0"] + ho["x1"]) / 2.0, 1),
                   "cy": round((ho["y0"] + ho["y1"]) / 2.0, 1),
                   "top": ho["y0"], "w": ho["x1"] - ho["x0"], "h": ho["y1"] - ho["y0"]}
    else:
        opening = {"shape": "none", "cx": round(mcx, 1), "top": my0, "w": mx1 - mx0,
                   "h": mh}
        if op:
            opening["side"] = op["side"]
    mark = {"x": mx0, "y": my0, "w": mx1 - mx0, "h": mh,
            "colour": ck.rgb_to_hex(top["rgb"]), "how": how,
            "score": round(top["_score"], 3), "opening": opening}
    return mark, lab, comps, group


def export_mark_parts(img: Img, lab, comps, group, outdir: str) -> Tuple[dict, dict, bool]:
    """Write mark.png and word_<side>.png, each holding ONLY its own pixels (anti-aliased
    edges included) at its own bounding box. Returns (part rects, file paths, fill) where
    fill = the logo paints a translucent light inside the mark's opening (dropped from
    mark.png so the door stays clear; the outro redraws it as the 'steps through' light)."""
    from array import array
    w, h, px = img.w, img.h, img.px
    n = w * h
    gid = {"mark": 1, "left": 2, "right": 3, "below": 4, "above": 5}
    cg = {c["id"]: gid[group.get(c["id"], "right")] for c in comps}
    own = array("b", bytes(n))
    for p in range(n):
        k = lab[p]
        if k:
            own[p] = cg.get(k, 0)
    # anti-aliased fringes (alpha < MARK_ALPHA) join the nearest labelled glyph: 3 passes
    al = px[3::4]
    for _ in range(3):
        upd = []
        for p in range(n):
            if own[p] or not al[p]:
                continue
            x, y = p % w, p // w
            for q in ((p - 1) if x > 0 else -1, (p + 1) if x < w - 1 else -1,
                      p - w if y > 0 else -1, p + w if y < h - 1 else -1):
                if q >= 0 and own[q]:
                    upd.append((p, own[q]))
                    break
        if not upd:
            break
        for p, g in upd:
            own[p] = g
    fill = sum(1 for p in range(n) if al[p] > 16 and not own[p]) > 0.01 * n
    rects, files = {}, {}
    for name, g in gid.items():
        xs0, ys0, xs1, ys1 = w, h, -1, -1
        for y in range(h):
            row = own[y * w:(y + 1) * w]
            if g not in row:
                continue
            ys0 = min(ys0, y)
            ys1 = y
            xs0 = min(xs0, row.index(g))
            xs1 = max(xs1, w - 1 - row[::-1].index(g))
        if xs1 < 0:
            rects[name] = None
            continue
        bw, bh = xs1 - xs0 + 1, ys1 - ys0 + 1
        out = bytearray(bw * bh * 4)
        for y in range(bh):
            for x in range(bw):
                p = (ys0 + y) * w + xs0 + x
                if own[p] == g:
                    i, o = p * 4, (y * bw + x) * 4
                    out[o:o + 4] = px[i:i + 4]
        fn = os.path.join(outdir, "mark.png" if name == "mark" else f"word_{name}.png")
        write_png(fn, Img(bw, bh, out))
        rects[name] = {"x": xs0, "y": ys0, "w": bw, "h": bh}
        files[name] = _rel(fn)
    return rects, files, fill


# ================================================================ role logic

def assign_roles(palette: List[dict], primary_ovr: Optional[str], accent_ovr: Optional[str],
                 bg_removed: Optional[Tuple[int, int, int]], notes: List[str]) -> dict:
    chromatic = [c for c in palette if ck.is_chromatic(c["rgb"]) and c["share"] >= 0.01]
    neutral = [c for c in palette if c not in chromatic]
    mono = not chromatic
    roles: Dict[str, str] = {}

    if mono and bg_removed is not None and ck.is_chromatic(bg_removed):
        # a white mark on a brand-coloured square: the square IS the brand colour
        chromatic = [{"rgb": tuple(bg_removed), "hex": ck.rgb_to_hex(bg_removed), "share": 0.0}]
        mono = False
        notes.append(f"the logo's box colour {ck.rgb_to_hex(bg_removed)} was removed as "
                     f"background but is the only brand colour — used as primary")

    # primary
    if primary_ovr:
        primary = ck.rgb_to_hex(ck.hex_to_rgb(primary_ovr))
        notes.append(f"primary set by --primary {primary}")
    elif chromatic:
        primary = chromatic[0]["hex"]
    else:
        nonwhite = [c for c in neutral if not _is_whiteish(c["rgb"])]
        primary = (nonwhite or neutral or [{"hex": DEFAULT_INK}])[0]["hex"]
    p_rgb = ck.hex_to_rgb(primary)

    # secondary: next DISTINCT chromatic colour
    sec = None
    for c in chromatic:
        if c["hex"] != primary and ck.delta_e2000(c["rgb"], p_rgb) > 20:
            sec = c["hex"]
            break
    if sec is None:
        L = ck.lightness(p_rgb)
        sec = ck.rgb_to_hex(ck.deepen(p_rgb, L - 22) if L > 50 else
                            ck.set_lightness(p_rgb, L + 22))
        if not mono:
            notes.append(f"logo has one brand colour; secondary {sec} is a tonal shade of "
                         f"the primary")

    # accent: the most vivid colour
    needs_accent = False
    if accent_ovr:
        accent = ck.rgb_to_hex(ck.hex_to_rgb(accent_ovr))
        notes.append(f"accent set by --accent {accent}")
    elif chromatic:
        accent = max(chromatic, key=lambda c: (ck.chroma(c["rgb"]), c["share"]))["hex"]
    else:
        accent = MONO_SUGGESTIONS[0][0]
        needs_accent = True
        notes.append("MONOCHROME logo: accent is a PROVISIONAL placeholder "
                     f"({accent}) — ask the user for one accent colour and rerun with --accent")

    # highlight base: the brand colour that can carry a keyword on footage
    if ck.chroma(p_rgb) >= 35:
        hl_base = primary
    elif accent_ovr or chromatic or needs_accent:
        hl_base = accent
    else:
        hl_base = primary
    if hl_base != primary:
        notes.append(f"primary {primary} is too dull/dark to carry a highlight "
                     f"(chroma {ck.chroma(p_rgb):.0f}) — highlights derive from accent {hl_base}")

    # ink / paper
    darks = sorted(palette, key=lambda c: ck.lightness(c["rgb"]))
    lights = sorted(palette, key=lambda c: -ck.lightness(c["rgb"]))
    paper = DEFAULT_PAPER
    for c in lights:
        L, C, _ = ck.lab_to_lch(ck.rgb_to_lab(c["rgb"]))
        if L >= 90 and C <= 15:
            paper = c["hex"]
        break
    ink = DEFAULT_INK
    for c in darks:
        if ck.lightness(c["rgb"]) <= 25 and ck.contrast(c["rgb"], paper) >= 7:
            ink = c["hex"]
        break

    on_primary = ck.best_text_on(p_rgb, (ink, WHITE))
    # On footage, prefer a colour the logo ALREADY has that reads strongly (≥ 6:1 on
    # black — footage is rarely black, so 4.5 is a floor, not a target). A two-blue logo
    # should highlight in its light blue, not in a lightened copy of its dark one.
    vivid = [c for c in palette
             if ck.chroma(c["rgb"]) >= 35 and ck.contrast(c["rgb"], DARK_BG) >= 6.0]
    if vivid and not accent_ovr:
        hl_dark_base = max(vivid, key=lambda c: (ck.chroma(c["rgb"]), c.get("share", 0)))["hex"]
        if hl_dark_base != hl_base:
            notes.append(f"hl_on_dark uses the logo's own {hl_dark_base} (reads "
                         f"{ck.contrast(hl_dark_base, DARK_BG):.1f}:1 on black)")
    else:
        hl_dark_base = hl_base
    hl_on_dark = ck.rgb_to_hex(ck.adjust_to_contrast(hl_dark_base, DARK_BG, 6.0, "lighter"))
    hl_on_light = ck.rgb_to_hex(ck.adjust_to_contrast(hl_base, paper, 4.5, "darker"))
    if hl_on_dark != hl_dark_base:
        notes.append(f"hl_on_dark lightened {hl_dark_base} -> {hl_on_dark} to reach 6:1 on black")
    if hl_on_light != hl_base:
        notes.append(f"hl_on_light darkened {hl_base} -> {hl_on_light} to reach 4.5:1 on "
                     f"paper {paper} (the brand colour as-is is "
                     f"{ck.contrast(hl_base, paper):.2f}:1 there — unreadable as text)")

    # card gradient: deep versions of the primary that keep white text >= 7:1
    # (deepen() eases chroma and warms dark yellows, so a gold brand gets a deep bronze
    # card, not an olive one)
    L0, C0, H0 = ck.lab_to_lch(ck.rgb_to_lab(p_rgb))
    if L0 >= 8:
        grad_a = ck.deepen(p_rgb, min(L0, 26.0))
    else:                                             # near-black primary: lift for depth
        grad_a = ck.lch_to_rgb_mapped(16.0, C0, H0)
    grad_a = ck.adjust_to_contrast(grad_a, WHITE, 7.5, "darker")
    grad_b = ck.adjust_to_contrast(ck.deepen(grad_a, max(4.0, ck.lightness(grad_a) * 0.42)),
                                   WHITE, 14.0, "darker")

    roles.update({"primary": primary, "secondary": sec, "accent": accent, "ink": ink,
                  "paper": paper, "on_primary": on_primary, "hl_on_dark": hl_on_dark,
                  "hl_on_light": hl_on_light, "grad_a": ck.rgb_to_hex(grad_a),
                  "grad_b": ck.rgb_to_hex(grad_b)})
    return {"colors": roles, "monochrome": mono, "needs_accent": needs_accent,
            "hl_base": hl_base}


def contrast_table(c: dict) -> Dict[str, float]:
    pairs = {
        "hl_on_dark/#000000": (c["hl_on_dark"], DARK_BG),
        "hl_on_light/paper": (c["hl_on_light"], c["paper"]),
        "ink/paper": (c["ink"], c["paper"]),
        "on_primary/primary": (c["on_primary"], c["primary"]),
        "#FFFFFF/grad_a": (WHITE, c["grad_a"]),
        "#FFFFFF/grad_b": (WHITE, c["grad_b"]),
        "primary/#000000": (c["primary"], DARK_BG),
        "primary/paper": (c["primary"], c["paper"]),
        "accent/#000000": (c["accent"], DARK_BG),
        "accent/paper": (c["accent"], c["paper"]),
    }
    return {k: round(ck.contrast(a, b), 2) for k, (a, b) in pairs.items()}


# ===================================================================== outputs

def write_css(path: str, c: dict, src: str) -> None:
    lines = [f"/* GENERATED by scripts/brand_from_logo.py from {os.path.basename(src)} — do",
             "   not hand-edit; rerun it (with --primary / --accent to override a role).",
             "   Text on footage/dark cards: --hl-on-dark. Text on paper/light: --hl-on-light.",
             "   Cards: --brand-grad-a -> --brand-grad-b (white text >= 7:1). */",
             ":root {"]
    for var, key, rgb in (("--brand-primary", "primary", True),
                          ("--brand-secondary", "secondary", True),
                          ("--brand-accent", "accent", True),
                          ("--brand-ink", "ink", False), ("--brand-paper", "paper", False),
                          ("--brand-on-primary", "on_primary", False),
                          ("--hl-on-dark", "hl_on_dark", False),
                          ("--hl-on-light", "hl_on_light", False),
                          ("--brand-grad-a", "grad_a", False),
                          ("--brand-grad-b", "grad_b", False)):
        lines.append(f"  {var}: {c[key]};")
        if rgb:
            lines.append(f"  {var}-rgb: {ck.rgb_triplet(c[key])};")
    lines.append("}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def silhouette(img: Img, rgb, knock: Optional[bytearray]) -> Img:
    """One-colour version with the logo's alpha; enclosed white/bg fills knocked out."""
    n = img.w * img.h
    px = bytearray(n * 4)
    px[0::4] = bytes([rgb[0]]) * n
    px[1::4] = bytes([rgb[1]]) * n
    px[2::4] = bytes([rgb[2]]) * n
    a = bytearray(img.px[3::4])
    if knock is not None:
        for p in range(n):
            if knock[p]:
                a[p] = 0
    px[3::4] = a
    return Img(img.w, img.h, px)


def knocked_out(img: Img, knock: Optional[bytearray]) -> Img:
    """Full colour, with enclosed white/background fills made transparent. Many logo files
    paint the counter of an "O" white: on a cream outro background it shows as a white
    dot, and the outro cannot land the speaker inside an opaque hole."""
    px = bytearray(img.px)
    if knock is not None:
        for p in range(img.w * img.h):
            if knock[p]:
                px[p * 4 + 3] = 0
    return Img(img.w, img.h, px)


def _font_for_pil(size: int):
    """A FREE font for the swatch labels (never a system face): the skill's fetched Inter
    or Heebo if present, else PIL's bundled default."""
    from PIL import ImageFont
    for d in ("assets/fonts", os.path.join(hfcfg.SKILL_DIR, "assets", "fonts")):
        for fn in ("Inter.ttf", "Heebo.ttf"):
            p = os.path.join(d, fn)
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except OSError:
                    pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


ROLE_ORDER = ["primary", "secondary", "accent", "ink", "paper", "on_primary",
              "hl_on_dark", "hl_on_light", "grad_a", "grad_b"]


def palette_png(path: str, c: dict, outdir: str, trimmed: Img) -> bool:
    """Swatch sheet + the text samples that matter. PIL when present, else ffmpeg colour
    sources (unlabelled; the legend is printed). Returns True if labelled."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return _palette_ffmpeg(path, c)
    W, sw = 1200, 120
    rows = 2
    H = 40 + rows * (sw + 70) + 5 * 96 + 300
    im = Image.new("RGB", (W, H), "#202024")
    d = ImageDraw.Draw(im)
    f_lab, f_hex, f_txt = _font_for_pil(22), _font_for_pil(20), _font_for_pil(44)
    for k, role in enumerate(ROLE_ORDER):
        col, row = k % 5, k // 5
        x = 40 + col * 228
        y = 30 + row * (sw + 70)
        d.rectangle([x, y, x + 200, y + sw], fill=c[role], outline="#555")
        d.text((x, y + sw + 8), role, fill="#EEE", font=f_lab)
        d.text((x, y + sw + 34), c[role], fill="#AAA", font=f_hex)
    y = 40 + rows * (sw + 70)
    samples = [(DARK_BG, c["hl_on_dark"], "hl_on_dark on black / footage"),
               (c["paper"], c["hl_on_light"], "hl_on_light on paper"),
               (c["paper"], c["primary"], "primary as-is on paper (reference)"),
               (c["grad_a"], WHITE, "white on grad_a (card)"),
               (c["primary"], c["on_primary"], "on_primary on primary")]
    for bg, fg, label in samples:
        d.rectangle([40, y, W - 40, y + 84], fill=bg)
        r = ck.contrast(fg, bg)
        verdict = "PASS" if r >= 4.5 else ("large-only" if r >= 3 else "FAIL")
        d.text((64, y + 16), f"Brand keyword  {r:.2f}:1 {verdict}", fill=fg, font=f_txt)
        d.text((W - 420, y + 50), label, fill=ck.best_text_on(bg, ("#111111", "#EEEEEE")),
               font=f_hex)
        y += 96
    # logo variants on their intended grounds
    lw = (W - 80 - 40) // 3
    for k, (bg, name) in enumerate(((c["paper"], "logo_trim.png"), ("#000000", "logo_on_dark.png"),
                                    (c["paper"], "logo_on_light.png"))):
        x = 40 + k * (lw + 20)
        d.rectangle([x, y + 10, x + lw, y + 270], fill=bg)
        try:
            lg = Image.open(os.path.join(outdir, name)).convert("RGBA")
            lg.thumbnail((lw - 30, 230))
            im.paste(lg, (x + (lw - lg.width) // 2, y + 10 + (260 - lg.height) // 2), lg)
        except OSError:
            pass
    im.save(path)
    return True


def _palette_ffmpeg(path: str, c: dict) -> bool:
    inputs, labels = [], []
    for role in ROLE_ORDER:
        inputs += ["-f", "lavfi", "-i", f"color=c=0x{c[role][1:]}:s=200x160:d=1"]
        labels.append(role)
    n = len(ROLE_ORDER)
    fc = (f"{''.join(f'[{i}:v]' for i in range(5))}hstack=inputs=5[top];"
          f"{''.join(f'[{i}:v]' for i in range(5, n))}hstack=inputs={n - 5}[bot];"
          f"[top][bot]vstack=inputs=2[out]")
    r = hfcfg.run(["ffmpeg", "-v", "error", "-y"] + inputs +
                  ["-filter_complex", fc, "-map", "[out]", "-frames:v", "1", path])
    if r.returncode:
        say(f"  ! palette.png failed: {r.stderr.strip()[-300:]}")
    say("  palette.png (unlabelled, no PIL) — left to right, top row then bottom row:")
    for k, role in enumerate(ROLE_ORDER):
        say(f"    {k + 1:2d}. {role:12} {c[role]}")
    return False


def _rel(p: str) -> str:
    ap = os.path.abspath(p)
    cwd = os.getcwd()
    return os.path.relpath(ap, cwd) if ap.startswith(cwd + os.sep) else ap


# ======================================================================== main

def main(argv=None) -> int:
    ap = hfcfg.arg_parser(__doc__.split("\n\n")[0])
    ap.add_argument("logo", help="logo file: png / jpg / webp / svg")
    ap.add_argument("--out", default="brand", help="output folder (default brand/)")
    ap.add_argument("--primary", help="override the primary colour (#hex)")
    ap.add_argument("--accent", help="override / supply the accent colour (#hex) — "
                                     "required for a monochrome logo")
    a = ap.parse_args(argv)
    hfcfg.require("ffmpeg", "ffprobe")
    for v in (a.primary, a.accent):
        if v:
            try:
                ck.hex_to_rgb(v)
            except ValueError:
                sys.exit(f"not a hex colour: {v}")
    src = a.logo
    if not os.path.exists(src):
        sys.exit(f"logo not found: {src}")
    os.makedirs(a.out, exist_ok=True)
    notes: List[str] = []
    declared: List[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        raster = src
        if src.lower().endswith(".svg"):
            with open(src, encoding="utf-8", errors="replace") as f:
                declared = svg_colours(f.read())
            raster = rasterise_svg(src, tmp)
            notes.append(f"SVG: colours declared in the source: {', '.join(declared) or 'none'}")
        img = decode(raster, WORK_MAX)

    # ---- alpha: real transparency, or derive it from a solid background
    alpha = img.alpha()
    transparent = sum(1 for v in alpha[::7] if v < 250) / max(1, len(alpha[::7]))
    bg_removed = None
    if transparent < 0.005:
        bg, cover = detect_background(img)
        if cover >= 0.6:
            img = remove_background(img, bg)
            bg_removed = bg
            notes.append(f"opaque logo: background {ck.rgb_to_hex(bg)} removed by flood fill "
                         f"from the border ({cover * 100:.0f}% of the border matched)")
        else:
            notes.append("opaque logo with a non-uniform border — kept as a full rectangle; "
                         "supply a transparent PNG/SVG for cut-out variants and holes")

    box = bbox(img)
    if box is None:
        sys.exit("the logo is completely transparent")
    trimmed = crop(img, box)

    # ---- colours
    colours = sample_colours(trimmed)
    palette = cluster(colours)
    if declared:
        for c in palette:                       # snap to the designer's exact hex
            best = min(declared, key=lambda d: ck.delta_e2000(d, c["rgb"]))
            if ck.delta_e2000(best, c["rgb"]) < 5:
                c["hex"], c["rgb"] = best, ck.hex_to_rgb(best)
    white_share = sum(c["share"] for c in palette if _is_whiteish(c["rgb"]))
    res = assign_roles(palette, a.primary, a.accent, bg_removed, notes)
    colors = res["colors"]

    # ---- holes + variants
    holes, knock = find_holes(trimmed, bg_removed, white_counts=white_share < 0.5)
    if any(h["fill"] == "solid-fill" for h in holes):
        notes.append("some enclosed regions are filled white/background in the source; they "
                     "count as holes; logo_knock.png (used by the outro) and the silhouettes knock "
                     "them out, logo_trim.png keeps them as designed")
    p_trim = os.path.join(a.out, "logo_trim.png")
    p_dark = os.path.join(a.out, "logo_on_dark.png")
    p_light = os.path.join(a.out, "logo_on_light.png")
    write_png(p_trim, trimmed)
    p_knock = os.path.join(a.out, "logo_knock.png")
    has_fill = any(h.get("fill") == "solid-fill" for h in holes)
    if has_fill:
        write_png(p_knock, knocked_out(trimmed, knock))
    write_png(p_dark, silhouette(trimmed, (255, 255, 255), knock))
    write_png(p_light, silhouette(trimmed, ck.hex_to_rgb(colors["ink"]), knock))

    # ---- the mark (the symbol) and the words around it — for the gate outro
    mark, lab, comps, group = find_mark(trimmed, knock if has_fill else None, holes, notes)
    if mark:
        # from the knocked-out image: a counter painted white is a hole for the door, and
        # its white must not ride along as a fringe on mark.png
        src_img = knocked_out(trimmed, knock) if has_fill else trimmed
        rects, files, fill = export_mark_parts(src_img, lab, comps, group, a.out)
        mr = rects.pop("mark")
        # mark.png's own box (fringes included) is the placement rect
        mark.update({"x": mr["x"], "y": mr["y"], "w": mr["w"], "h": mr["h"]})
        mark["parts"] = {k: rects.get(k) for k in ("left", "right", "below", "above")}
        mark["files"] = files
        mark["fill"] = fill
        boxes = [mr] + [r for r in rects.values() if r]
        mark["content"] = [min(r["x"] for r in boxes), min(r["y"] for r in boxes),
                           max(r["x"] + r["w"] for r in boxes),
                           max(r["y"] + r["h"] for r in boxes)]

    out = {
        "logo": {"src": _rel(src), "trimmed": _rel(p_trim), "on_dark": _rel(p_dark),
                 "on_light": _rel(p_light),
                 "knocked": _rel(p_knock) if has_fill else "",
                 "w": trimmed.w, "h": trimmed.h,
                 "aspect": round(trimmed.w / float(trimmed.h), 4), "holes": holes,
                 "mark": mark,
                 "monochrome": res["monochrome"]},
        "colors": colors,
        "palette": [{"hex": c["hex"], "share": round(c["share"], 4)} for c in palette],
        "contrast": contrast_table(colors),
        "notes": notes,
        "hl_base": res["hl_base"],
        "needs_accent": res["needs_accent"],
    }
    with open(os.path.join(a.out, "brand.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")
    write_css(os.path.join(a.out, "brand.css"), colors, src)
    labelled = palette_png(os.path.join(a.out, "palette.png"), colors, a.out, trimmed)

    # ---- report
    say(f"brand from {src}")
    say(f"  palette : " + "  ".join(f"{c['hex']} {c['share'] * 100:.0f}%" for c in palette))
    for role in ROLE_ORDER:
        say(f"  {role:12}{colors[role]}")
    say("  contrast: " + ", ".join(f"{k} {v}" for k, v in out["contrast"].items()))
    say(f"  logo    : {trimmed.w}x{trimmed.h} (aspect {out['logo']['aspect']}), "
        f"{len(holes)} hole(s)")
    for hh in holes[:3]:
        say(f"    hole  : centre ({hh['cx']}, {hh['cy']}) r {hh['r']} "
            f"(inscribed {hh['r_inscribed']}) roundness {hh['roundness']} [{hh['fill']}]")
    if mark:
        o = mark["opening"]
        parts = ", ".join(f"{k} {v['w']}x{v['h']}" for k, v in mark["parts"].items() if v)
        say(f"  mark    : {mark['w']}x{mark['h']} at ({mark['x']}, {mark['y']}) "
            f"{mark['colour']} [{mark['how']}], opening {o['shape']} "
            f"w {o['w']} h {o['h']} top {o['top']}; words: {parts or 'none'}")
    for n in notes:
        say(f"  note    : {n}")
    say(f"  wrote   : {a.out}/brand.json, brand.css, logo_trim/on_dark/on_light.png, "
        f"palette.png{'' if labelled else ' (unlabelled)'}")
    if res["needs_accent"]:
        say("\n  >>> MONOCHROME LOGO. Claude: ask the user for ONE accent colour for highlights "
            "and motion graphics. Offer:")
        for hx, why in MONO_SUGGESTIONS:
            say(f"        {hx}  {why}")
        say(f"      then rerun:  python3 scripts/brand_from_logo.py {src} --out {a.out} "
            f"--accent #RRGGBB")
    say("\n  LOOK at palette.png before using the brand: every text sample must read.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
