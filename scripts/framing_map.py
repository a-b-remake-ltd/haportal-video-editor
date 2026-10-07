#!/usr/bin/env python3
"""Framing map — where the speaker's head, chin, chest and hands are, and where the free space
is, measured on the A-roll BEFORE anything is designed.

    python3 scripts/framing_map.py assets/aroll.mp4            # → build/framing.png + framing.json
    python3 scripts/framing_map.py assets/aroll.mp4 --apply    # also move the caption band in config

WHY. Every placement decision in a reel depends on the framing: headlines go on the chest,
captions on a high-contrast band, sky widgets in the free zone above the head, nothing on the
face. Guessing those from one screenshot is how a caption ends up on the speaker's chin and a
widget on their forehead. So: ~10 frames across the take, measured, with a gridded sheet to
LOOK at and the numbers in a file every later step can read.

What it writes (all in real composition px, 1080x1920, object-fit: cover applied):
  build/framing.png         10 frames at 270x480 with drawgrid=w=27:h=48 (one cell = 108x192
                            real px), tiled 5x2. Look at it before designing anything.
  build/framing_marked.png  the same frames with the measurements drawn on: head top (cyan),
                            chin (magenta), chest (orange), caption band (yellow), free zones
                            (green), face centre (white cross). Verify the numbers by eye.
  build/framing.json        {"head_top", "face_cx", "face_cy", "face_w", "chin", "chest": [y0, y1],
                             "chest_luma", "hands_y", "free_zones": [[x0, y0, x1, y1], ...],
                             "caption_band": [y0, y1], "caption_band_luma",
                             "caption_band_reason", "fallback": [keys not measured],
                             "samples": [...per frame...]}

    python3 scripts/framing_map.py --selftest   # the gates' negative tests, synthetic frames

How each number is measured (median over the frames, so one gesture cannot move it):
  face      skin = the YCbCr chroma box (or the warm-RGB rule) AND ≥ 10 chroma units away
            from THIS frame's background (median chroma of the top band and the outer
            columns). The face is the largest skin BLOB with its top in the upper 60 % of
            the frame; face_w is the median row width over its cheek rows, face_cx their
            midpoint. WHY: the old fixed RGB rule (r>g>b) kept a 128 px strip of a 290 px
            face lit cool against a pale-blue sky, put the "chin" above the face centre in
            every frame, every reading was rejected and the script crashed.
  checks    per frame: hairline→chin 0.8-2.0 face widths, chin 0.35-1.4 widths under the
            face centre, collar near the chin (or 0.35-2.6 widths under the face when the
            chin was dropped); across frames: a "face" more than a face width away from the
            others is a hand. Readings that fail are dropped, not averaged.
  fallback  a number NO frame could give takes the house default (a centred medium
            close-up: head 620, face 540,860 w 280, chin 1120, chest 1200-1520), is listed in
            "fallback" and printed loudly. The map and both sheets are ALWAYS written, so
            the pipeline continues; correct the listed numbers by eye from the sheet.
  head_top  hair by LUMA: the patch just above the first skin row gives the hair's luma; rows
            above it that still match (within the face columns) are hair; the topmost is the
            head top. Bald → the skin top. Capped at 0.75 face widths above the skin.
  chest     the shirt: its colour is the median of a patch well below the face; the chest is the
            run of rows where that colour fills the band under the chin.
  chin      the last face-wide skin row, pushed down to just above the shirt when a beard or a
            shadow hides the jaw (collar − 0.25 face widths), never below the collar.
  hands_y   the highest hand-sized skin blob below the collar outside the neck column
            (None = no hands); p20 over the frames.
  free      low-detail, steady blocks inside the safe zone, outside the head and the shirt:
            spatial luma std AND frame-to-frame change both low. The largest rectangles
            (≥ 300x150 px) are reported, biggest first.

Caption band (references/grid.md, spec §2): the band is the configured one (captions.center_y,
else the grid's 1110-1190). If it lands on the chin or face, it moves DOWN onto the chest
(top = max(chin + 80, chest top + 40)) and the reason is recorded. --apply writes
captions.center_y (+ center_y_reason, center_y_source) into config.json and reports the change;
a band that clears the chin is left alone (and a stale framing_map value is removed).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402
import grid  # noqa: E402

SW, SH = 270, 480          # sample size; ×4 = composition px at 1080x1920
K = 4.0
BAND_H = 76                # one caption line: 62 px type, line height ~1.2


def grab(path, t, W, H):
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"scale={SW}:{SH}:flags=area,format=rgb24")
    r = hfcfg.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
                   "-vf", vf, "-f", "rawvideo", "-"], text=False)
    return r.stdout


def background_chroma(np, f):
    """(Cb, Cr) of the frame's own background: the median over the top band and the outer
    columns of the upper half — sky, wall or window in a talking-head take.

    WHY: skin is "warmer than THIS frame's background", not a fixed RGB box. A pale-blue sky
    lit cool turned a face pink-grey (r 167, g 119, b 125: g < b, so the old r>g>b rule kept
    only a 128 px strip of a 290 px face), while a warm wall can sit inside any fixed skin
    box. Measuring the background and requiring distance from it handles both."""
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    top = int(0.08 * SH)
    side = int(0.12 * SW)
    half = SH // 2
    sel = np.concatenate([cb[:top].ravel(), cb[:half, :side].ravel(), cb[:half, -side:].ravel()])
    sel_r = np.concatenate([cr[:top].ravel(), cr[:half, :side].ravel(), cr[:half, -side:].ravel()])
    return float(np.median(sel)), float(np.median(sel_r))


def skin_mask(np, f, bg=None):
    """Skin = the YCbCr chroma box (Cb 77-127, Cr 135-175, not black, not blown out) OR the
    classic warm-RGB rule — and, when the background is known, at least SKIN_BG_DIST away
    from the background's chroma. Chroma barely moves with the light's level and colour,
    which is why the box finds a face under cool light the RGB rule loses."""
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    box = (cb >= 77) & (cb <= 127) & (cr >= 135) & (cr <= 175) & (y > 40) & (y < 245)
    rgb = (r > 95) & (r > g + 16) & (g > b + 6) & ((r - b) > 40) & ((r - b) < 130)
    skin = box | rgb
    if bg is not None:
        skin &= np.hypot(cb - bg[0], cr - bg[1]) >= SKIN_BG_DIST
    return skin


SKIN_BG_DIST = 10.0        # min (Cb, Cr) distance from the background's chroma


def blobs(np, mask):
    """Connected components (4-connected, by row runs + union-find) of a boolean mask.
    Returns a label image (0 = none) and {label: area}. numpy only: scipy is not a
    dependency of this skill."""
    h, w = mask.shape
    lab = np.zeros((h, w), dtype=np.int32)
    parent = [0]

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    prev = []
    for yy in range(h):
        row = mask[yy]
        if not row.any():
            prev = []
            continue
        d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
        cur = []
        for s, e in zip(starts, ends):
            l_ = len(parent)
            parent.append(l_)
            for ps, pe, pl in prev:
                if ps < e and pe > s:
                    ra, rb = find(l_), find(pl)
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
            lab[yy, s:e] = l_
            cur.append((s, e, l_))
        prev = cur
    if len(parent) == 1:
        return lab, {}
    roots = np.array([find(i) for i in range(len(parent))], dtype=np.int32)
    lab = roots[lab]
    ids, counts = np.unique(lab[lab > 0], return_counts=True)
    return lab, {int(i): int(c) for i, c in zip(ids, counts)}


def face_blob(np, skin):
    """The face = the largest plausible skin blob: its top in the upper 60 % of the frame
    (a hand at the chest is lower), at least 6 % of the frame wide, not a sliver (bbox
    height ≥ 0.6 × width), at least ~0.3 % of the frame. The bbox may be wide — a hand
    touching the cheek merges into the face blob — the face's own width is measured on its
    cheek rows afterwards. Hands and warm specks elsewhere are separate blobs, so they no
    longer widen or narrow the face the way a column histogram over the whole frame did.
    Returns (mask, (x0, y0, x1, y1)) or (None, None)."""
    lab, areas = blobs(np, skin)
    best, best_score = None, 0.0
    for l_, area in areas.items():
        if area < 0.003 * SW * SH:
            continue
        ys, xs = np.nonzero(lab == l_)
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
        bw, bh = x1 - x0, y1 - y0
        if y0 > 0.6 * SH or bw < 0.06 * SW or bh < 0.6 * min(bw, 0.62 * SW):
            continue
        score = area * (1.0 if y0 < 0.5 * SH else 0.5)
        if score > best_score:
            best, best_score = (l_, (x0, y0, x1, y1)), score
    if not best:
        return None, None
    return lab == best[0], best[1]


def measure_frame(np, img, bg=None):
    """One frame (SH x SW x 3 uint8) → dict in SAMPLE px, or None when no face is found.
    `bg` is the background chroma (background_chroma); None = measure it on this frame."""
    f = img.astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    if bg is None:
        bg = background_chroma(np, f)
    skin = skin_mask(np, f, bg)
    luma = (0.299 * r + 0.587 * g + 0.114 * b)
    r0, r1 = int(SH * 100 / 1920), int(SH * 1500 / 1920)
    search = skin.copy()
    search[:r0] = False
    search[r1:] = False
    face, box = face_blob(np, search)
    if face is None:
        return None
    # the face's top: the first row where the blob is a real width (not a stray hair pixel)
    ext = []
    for yy in range(box[1], box[3]):
        xs = np.nonzero(face[yy])[0]
        ext.append((yy, int(xs[0]), int(xs[-1]) + 1) if xs.size else (yy, None, None))
    widths = [e[2] - e[1] for e in ext if e[1] is not None]
    wmax = float(np.percentile(widths, 90)) if widths else 0
    top = next((e[0] for e in ext if e[1] is not None and e[2] - e[1] >= 0.3 * wmax), None)
    if top is None:
        return None
    # face width: the median row width over the CHEEK rows (0.35-0.85 face widths under the
    # top) — the forehead is narrower, the jaw too, and a hand touching the face only widens
    # a few rows, which the median ignores
    w0 = float(np.median([e[2] - e[1] for e in ext if e[1] is not None and
                          top <= e[0] < top + 60])) if widths else 0
    cheek = [e[2] - e[1] for e in ext if e[1] is not None and
             top + 0.35 * w0 <= e[0] <= top + 0.85 * w0]
    w = float(np.median(cheek)) if cheek else w0
    if not (8 <= w <= 0.62 * SW):
        return None
    mids = [(e[1] + e[2]) / 2.0 for e in ext if e[1] is not None and
            top + 0.35 * w0 <= e[0] <= top + 0.85 * w0]
    a = int(round(np.median([e[1] for e in ext if e[1] is not None and
                             top + 0.35 * w0 <= e[0] <= top + 0.85 * w0] or [box[0]])))
    bb = int(round(a + w))
    cx = float(np.median(mids)) if mids else (box[0] + box[2]) / 2.0
    skin_all, skin = skin, face   # the face blob for the chin; all skin for hair and hands
    # last face-wide skin row (stops at the first long run without skin: beard / collar)
    lo, hi = max(0, int(a - 0.15 * w)), min(SW, int(bb + 0.15 * w))
    wide = skin[:, lo:hi].sum(axis=1)
    face_bot, gap = top, 0
    # ≤ 1.65 face widths under the hairline: the neck is skin too and as wide, and the
    # better mask follows it down to the collar (forehead to chin is ~1.3-1.6 widths)
    for y in range(top, min(SH, int(top + 1.65 * w))):
        if wide[y] >= 0.45 * w:
            face_bot, gap = y, 0
        else:
            gap += 1
            if gap > 0.35 * w:
                break
    # the shirt: colour of a patch well under the face, then the run of rows it fills
    py0, py1 = int(face_bot + 0.9 * w), int(face_bot + 1.3 * w)
    px0, px1 = max(0, int(cx - 0.8 * w)), min(SW, int(cx + 0.8 * w))
    collar = chest_bot = None
    shirt = None
    if py1 < SH - 2:
        patch = f[py0:py1, px0:px1].reshape(-1, 3)
        shirt = np.median(patch, axis=0)
        dist = np.sqrt(((f[:, px0:px1] - shirt) ** 2).sum(axis=2))
        frac = (dist < 45).mean(axis=1)
        for y in range(face_bot, SH):
            if frac[y] >= 0.6:
                collar = y
                break
        if collar is not None:
            chest_bot = collar
            for y in range(collar, SH):
                if frac[y] >= 0.5:
                    chest_bot = y
                elif y - chest_bot > 6:
                    break
    chin = face_bot
    if collar is not None:
        chin = min(max(face_bot, collar - 0.25 * w), collar - 2)
    # hair top by luma
    hy0, hy1 = int(top - 0.12 * w), int(top - 0.04 * w)
    hx0, hx1 = max(0, int(cx - 0.25 * w)), min(SW, int(cx + 0.25 * w))
    head_top, how_top = top, "skin top (no hair found)"
    if hy0 > 0 and hy1 > hy0:
        hp = skin_all[hy0:hy1, hx0:hx1].mean()
        hl = float(np.median(luma[hy0:hy1, hx0:hx1]))
        if hp < 0.3:
            band = luma[:, max(0, int(cx - 0.4 * w)):min(SW, int(cx + 0.4 * w))]
            match = (np.abs(band - hl) < 30).mean(axis=1)
            cap = max(0, int(top - 0.75 * w))
            y, miss, last = top - 1, 0, top
            while y >= cap:
                if match[y] >= 0.35:
                    last, miss = y, 0
                else:
                    miss += 1
                    if miss > 2:
                        break
                y -= 1
            head_top = last
            how_top = "hair by luma" + (" (capped)" if last <= cap else "")
    # hands: skin under the collar, outside the neck column, as a real BLOB (≥ 0.4 % of the
    # frame, about a hand). Warm bokeh lights and a lamp pass the skin mask too, but as
    # specks; the chroma box also passes a warm wall patch by the collar — blobs skip both.
    hands = None
    if collar is not None:
        side = skin_all & ~skin
        side[:collar] = False
        side[:, max(0, int(cx - 0.4 * w)):min(SW, int(cx + 0.4 * w))] = False
        hl_, hareas = blobs(np, side)
        tops = [int(np.nonzero((hl_ == l_).any(axis=1))[0][0]) for l_, ar in hareas.items()
                if ar >= 0.004 * SW * SH]
        hands = min(tops) if tops else None
    return {"face_cx": cx, "face_cy": top + 0.62 * w, "face_w": w, "skin_top": top,
            "head_top": head_top, "how_top": how_top, "face_bot": face_bot, "chin": chin,
            "collar": collar, "chest_bot": chest_bot, "hands": hands,
            "face_x": [a, bb], "shirt": shirt.tolist() if shirt is not None else None}


def free_zones(np, frames, person, safe):
    """Low-detail, steady rectangles (composition px) inside the safe zone, outside the
    person. Blocks of 6x6 sample px (24x24 real)."""
    B = 6
    nx, ny = SW // B, SH // B
    lum = np.stack([(0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2])
                    for f in frames]).astype(np.float32)
    blocks = lum[:, :ny * B, :nx * B].reshape(len(frames), ny, B, nx, B)
    spatial = blocks.std(axis=(2, 4)).mean(axis=0)
    means = blocks.mean(axis=(2, 4))
    temporal = means.std(axis=0)
    free = (spatial < 9.0) & (temporal < 8.0)
    sx0, sy0, sx1, sy1 = [v / K for v in safe]
    for j in range(ny):
        for i in range(nx):
            x0, y0, x1, y1 = i * B, j * B, (i + 1) * B, (j + 1) * B
            if x0 < sx0 or y0 < sy0 or x1 > sx1 or y1 > sy1:
                free[j, i] = False
            for p in person:
                if x0 < p[2] / K and x1 > p[0] / K and y0 < p[3] / K and y1 > p[1] / K:
                    free[j, i] = False
    out = []
    m = free.copy()
    for _ in range(3):
        best = _max_rect(m)
        if not best:
            break
        area, (i0, j0, i1, j1) = best
        rect = [int(i0 * B * K), int(j0 * B * K), int(i1 * B * K), int(j1 * B * K)]
        if rect[2] - rect[0] < 300 or rect[3] - rect[1] < 150:
            break
        out.append(rect)
        m[j0:j1, i0:i1] = False
    return out


def _max_rect(m):
    """Largest all-True rectangle in a boolean matrix (histogram + stack). (area, (i0,j0,i1,j1))"""
    ny, nx = m.shape
    h = [0] * nx
    best = None
    for j in range(ny):
        for i in range(nx):
            h[i] = h[i] + 1 if m[j, i] else 0
        st = []
        for i in range(nx + 1):
            cur = h[i] if i < nx else 0
            start = i
            while st and st[-1][1] >= cur:
                s, hh = st.pop()
                area = hh * (i - s)
                if hh and (best is None or area > best[0]):
                    best = (area, (s, j - hh + 1, i, j + 1))
                start = s
            st.append((start, cur))
    return best


def sheet(path, times, W, H, out, extra=None):
    """Tile frames (270x480, gridded) 5 per row."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="framing_")
    for k, t in enumerate(times):
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              + (extra + "," if extra else "")
              + f"scale={SW}:{SH},drawgrid=w=27:h=48:t=1:color=white@0.45")
        r = hfcfg.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", path,
                       "-frames:v", "1", "-vf", vf, os.path.join(tmp, f"f{k:02d}.png")])
        if r.returncode:
            sys.exit(f"could not grab a frame at {t:.2f}s:\n{r.stderr[-400:]}")
    cols = 5
    rows = (len(times) + cols - 1) // cols
    r = hfcfg.run(["ffmpeg", "-v", "error", "-y", "-i", os.path.join(tmp, "f%02d.png"),
                   "-vf", f"tile={cols}x{rows}:padding=4:color=black", "-frames:v", "1", out])
    if r.returncode:
        sys.exit(f"could not tile {out}:\n{r.stderr[-400:]}")


def marks_filter(fm, W, H):
    k = W / 1080.0
    L = []

    def hline(y, color, t=6):
        if y is not None:
            L.append(f"drawbox=x=0:y={int(y * k)}:w={W}:h={t}:color={color}:t=fill")
    hline(fm["head_top"], "cyan@0.9")
    hline(fm["chin"], "magenta@0.9")
    if fm.get("chest"):
        y0, y1 = fm["chest"]
        L.append(f"drawbox=x=0:y={int(y0 * k)}:w={W}:h={int((y1 - y0) * k)}:color=orange@0.9:t=10")
    a, b = fm["caption_band"]
    L.append(f"drawbox=x={int(60 * k)}:y={int(a * k)}:w={int(880 * k)}:h={int((b - a) * k)}:color=yellow@0.5:t=fill")
    for z in fm.get("free_zones", []):
        L.append(f"drawbox=x={int(z[0] * k)}:y={int(z[1] * k)}:w={int((z[2] - z[0]) * k)}:"
                 f"h={int((z[3] - z[1]) * k)}:color=lime@0.9:t=10")
    if fm.get("hands_y"):
        hline(fm["hands_y"], "red@0.9", 4)
    cx, cy = fm["face_cx"], fm["face_cy"]
    L.append(f"drawbox=x={int((cx - 30) * k)}:y={int(cy * k) - 3}:w={int(60 * k)}:h=6:color=white:t=fill")
    L.append(f"drawbox=x={int(cx * k) - 3}:y={int((cy - 30) * k)}:w=6:h={int(60 * k)}:color=white:t=fill")
    return ",".join(L)


def band_luma(np, frames, band):
    a, b = int(band[0] / K), int(band[1] / K)
    x0, x1 = int(60 / K), int(940 / K)
    vals = np.concatenate([(0.299 * f[a:b, x0:x1, 0] + 0.587 * f[a:b, x0:x1, 1]
                            + 0.114 * f[a:b, x0:x1, 2]).ravel() for f in frames])
    return round(float(np.percentile(vals, 75)) / 255.0, 3) if vals.size else None


# The house's typical centred medium close-up (spec §1.3; kit.py FRAMING_DEFAULT). Used
# ONLY for a number the frames could not give, and always reported as a fallback.
FALLBACK = {"head_top": 620, "face_cx": 540, "face_cy": 860, "face_w": 280, "chin": 1120,
            "chest": [1200, 1520]}


def plausible(sm):
    """Per-frame sanity checks, in SAMPLE px, BEFORE aggregating. Returns the problems found
    and clears the readings they make untrustworthy.

    WHY: one bad frame used to be enough to break the whole map — a hand or a microphone
    read as the collar gave a "chin" 700 px below the face on a real phone take, and a face
    measured as a 128 px strip (a cool-lit face, real width ~290) put the chin ABOVE the
    face centre in every frame, every reading was dropped and the script crashed.
      * the face: hairline (skin top) → chin spans 0.8-2.0 face widths (forehead to chin is
        ~1.3-1.6, a beard adds a little; the 128 px strip measured 2.15). Outside that, the
        face width itself is wrong → the frame's face readings are dropped.
      * the chin sits 0.35-1.4 face widths below the face centre;
      * the collar sits within -0.15..1.2 face widths of the chin — or, when the chin was
        dropped, 0.35-2.6 face widths under the face centre (this check used to be skipped
        then, and a mic 1800 px down became the "chest").
    """
    out = []
    fw, fcy = sm.get("face_w"), sm.get("face_cy")
    if not fw or fcy is None:
        return out
    top, fb = sm.get("skin_top"), sm.get("chin")
    if top is not None and fb is not None and fb > top and not (0.8 * fw <= fb - top <= 2.0 * fw):
        out.append(f"face {fw * K:.0f} px wide but {(fb - top) * K:.0f} px tall")
        for k in ("face_w", "face_cx", "face_cy", "chin", "collar", "chest_bot"):
            sm[k] = None
        return out
    if sm.get("chin") is not None and not (0.35 * fw <= sm["chin"] - fcy <= 1.4 * fw):
        out.append(f"chin {sm['chin'] * K:.0f} vs face centre {fcy * K:.0f}")
        sm["chin"] = None
    ch = sm.get("chin")
    col = sm.get("collar")
    if col is not None and ((ch is not None and not (-0.15 * fw <= col - ch <= 1.2 * fw)) or
                            (ch is None and not (0.35 * fw <= col - fcy <= 2.6 * fw))):
        out.append(f"collar {col * K:.0f}")
        sm["collar"], sm["chest_bot"] = None, None
    return out


def aggregate(np, samples, H, g):
    """Per-frame readings (sample px) → the framing map (composition px) + notes.

    Medians over the frames that passed plausible(); any number no frame could give falls
    back to FALLBACK and is LISTED in fm["fallback"] — never a crash, never a silent
    default: the pipeline continues and the report says what to check by eye."""
    notes = []
    bad = 0
    for sm in samples:
        if plausible(sm):
            bad += 1
    # Consistency across the take: a talking head moves less than a face width. A frame
    # whose "face" sits far from where the other frames put it is a hand or a lamp.
    good = [s for s in samples if s.get("face_w")]
    if len(good) >= 3:
        mx = float(np.median([s["face_cx"] for s in good]))
        my = float(np.median([s["face_cy"] for s in good]))
        mw = float(np.median([s["face_w"] for s in good]))
        for s in good:
            if abs(s["face_cx"] - mx) > 1.0 * mw or abs(s["face_cy"] - my) > 0.8 * mw or \
                    not (0.6 * mw <= s["face_w"] <= 1.6 * mw):
                for k in ("face_w", "face_cx", "face_cy", "chin", "collar", "chest_bot",
                          "head_top", "hands"):
                    s[k] = None
                bad += 1
    if bad:
        notes.append(f"ignored implausible readings in {bad}/{len(samples)} frame(s) (a hand or "
                     f"a mic in front of the chest, or a face the skin mask cut short)")

    def vals(k):
        return [s[k] for s in samples if s.get(k) is not None]

    def med(k):
        v = vals(k)
        return float(np.median(v)) * K if v else None

    def p80(k):
        v = vals(k)
        return float(np.percentile(v, 80)) * K if v else None

    fallback = []

    def pick(k, v):
        if v is None:
            fallback.append(k)
            return FALLBACK[k]
        return round(v)
    fm = {"head_top": pick("head_top", med("head_top")), "face_cx": pick("face_cx", med("face_cx")),
          "face_cy": pick("face_cy", med("face_cy")), "face_w": pick("face_w", med("face_w")),
          "chin": pick("chin", med("chin"))}
    fw = fm["face_w"]
    col, cb = med("collar"), med("chest_bot")
    fm["chest"] = None
    if col is not None and col < g["safe"][3] - 40:
        y0 = round(col)
        y1 = round(min(cb if cb is not None else H, g["safe"][3]))
        fm["chest"] = [y0, y1] if y1 > y0 + 40 else [y0, g["safe"][3]]   # never upside down
    elif col is not None:
        notes.append(f"collar measured at {col:.0f}, below the safe zone — no chest band")
    if fm["chest"] is None and "chin" in fallback:
        fm["chest"] = list(FALLBACK["chest"])
        fallback.append("chest")
    hs = sorted(s["hands"] for s in samples if s.get("hands") is not None)
    fm["hands_y"] = round(hs[len(hs) // 5] * K) if hs else None
    fm["hands_frames"] = f"{len(hs)}/{len(samples)}"
    shirts = [s["shirt"] for s in samples if s.get("shirt")]
    if shirts:
        sc = np.median(np.array(shirts), axis=0)
        fm["chest_luma"] = round(float(0.299 * sc[0] + 0.587 * sc[1] + 0.114 * sc[2]) / 255, 3)
    xs = [s["face_x"] for s in samples if s.get("face_w") is not None and s.get("face_x")]
    fm["_face_x"] = ([min(x[0] for x in xs) * K, max(x[1] for x in xs) * K] if xs else
                     [fm["face_cx"] - fw / 2, fm["face_cx"] + fw / 2])
    c80 = p80("chin")
    fm["chin_p80"] = round(c80) if c80 is not None else fm["chin"]
    k80 = p80("collar")
    fm["collar_p80"] = round(k80) if (fm["chest"] and k80 is not None) else None
    if fallback:
        fm["fallback"] = fallback
        notes.append("FALLBACK for " + ", ".join(fallback) + (
            f" — only {len(samples)} frame(s) had a face" if len(samples) < 2 else ""))
    return fm, notes


def _synthetic(np, bg=(187, 214, 237), skin=(190, 135, 140), face=(145, 215, 36, 48),
               shirt=(8, 8, 12)):
    """A 270x480 talking-head frame: a background, a face ellipse (cx, cy, rx, ry), dark
    hair on top, a neck and a shirt. Default: the failing case — a cool-lit face (g < b)
    on a pale-blue sky."""
    img = np.zeros((SH, SW, 3), dtype=np.uint8)
    img[:] = bg
    yy, xx = np.mgrid[0:SH, 0:SW]
    cx, cy, rx, ry = face
    img[((xx - cx) / (rx + 3)) ** 2 + ((yy - cy + 14) / (ry + 4)) ** 2 < 1] = (40, 30, 28)
    img[((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 < 1] = skin
    img[(abs(xx - cx) < 0.55 * rx) & (yy > cy) & (yy < cy + ry + 30)] = skin
    img[(yy >= cy + ry + 26) & (abs(xx - cx) < 3.2 * rx)] = shirt
    return img


def selftest(np):
    """The framing gates' own tests. Exit 1 on any failure."""
    global K
    K = 4.0
    fails = []

    def expect(cond, what):
        print(f"  {'✓' if cond else '✗'} {what}")
        if not cond:
            fails.append(what)
    g = {"safe": [60, 220, 940, 1520]}
    img = _synthetic(np)
    f = img.astype(np.int16)
    r, gg, b = f[..., 0], f[..., 1], f[..., 2]
    old = ((r > 95) & (r > gg + 16) & (gg > b + 6) & ((r - b) > 40) & ((r - b) < 130)).sum()
    expect(old == 0, "the old warm-RGB rule finds NO skin on a cool-lit face (the bug's cause)")
    m = measure_frame(np, img)
    expect(m is not None and abs(m["face_w"] - 72) <= 0.15 * 72,
           f"cool-lit face on a pale-blue sky: width {m and round(m['face_w'] * K)} px "
           f"(truth 288 ±15 %)")
    expect(m is not None and abs(m["face_cx"] - 145) <= 4, "… and centred on the face")
    expect(m is not None and m["chin"] is not None and m["chin"] > m["face_cy"],
           "… with the chin BELOW the face centre")
    warm = _synthetic(np, bg=(225, 205, 180), skin=(215, 160, 135))
    m2 = measure_frame(np, warm)
    expect(m2 is not None and abs(m2["face_w"] - 72) <= 0.2 * 72,
           f"warm face on a beige wall: width {m2 and round(m2['face_w'] * K)} px (truth 288)")
    # a hand as a second, larger blob low in the frame must not become the face
    hand = _synthetic(np)
    yy, xx = np.mgrid[0:SH, 0:SW]
    hand[((xx - 60) / 50) ** 2 + ((yy - 400) / 45) ** 2 < 1] = (190, 135, 140)
    m3 = measure_frame(np, hand)
    expect(m3 is not None and abs(m3["face_cx"] - 145) <= 4, "a big hand at the chest is not the face")
    # no face at all, and no frame at all: never a crash, the fallback is LISTED
    blank = np.zeros((SH, SW, 3), dtype=np.uint8) + np.array([187, 214, 237], dtype=np.uint8)
    expect(measure_frame(np, blank) is None, "an empty sky has no face")
    fm, notes = aggregate(np, [], 1920, g)
    expect(fm["face_w"] == FALLBACK["face_w"] and "face_w" in fm.get("fallback", []) and notes,
           "no samples → defaults, LISTED as fallback (no crash)")
    # every chin implausible (chin above the face centre) → fallback chin, not a TypeError
    bad = [{"face_cx": 140, "face_cy": 230, "face_w": 32, "skin_top": 190, "head_top": 180,
            "chin": 200, "collar": None, "chest_bot": None, "hands": None, "face_x": [124, 156],
            "t": i} for i in range(4)]
    fm2, _ = aggregate(np, bad, 1920, g)
    expect("chin" in fm2.get("fallback", []) and fm2["chin"] == FALLBACK["chin"],
           "every chin implausible → fallback chin (was: TypeError NoneType * float)")
    # a collar far below the face when the chin was dropped is NOT a chest
    s4 = [{"face_cx": 140, "face_cy": 200, "face_w": 90, "skin_top": 150, "head_top": 120,
           "chin": 100, "collar": 451, "chest_bot": 470, "hands": None, "face_x": [95, 185],
           "t": i} for i in range(3)]
    fm3, _ = aggregate(np, s4, 1920, g)
    expect(fm3["chest"] is None or fm3["chest"][0] < fm3["chest"][1],
           f"a collar under a dropped chin is checked too — chest {fm3['chest']} (never upside down)")
    print(f"  {'all passed' if not fails else str(len(fails)) + ' FAILED'}")
    return 1 if fails else 0


def main():
    ap = hfcfg.arg_parser(__doc__.split("\n\n")[0])
    ap.add_argument("aroll", nargs="?", default="assets/aroll.mp4")
    ap.add_argument("--frames", type=int, default=10)
    ap.add_argument("--out", default="build/framing.json")
    ap.add_argument("--sheet", default="build/framing.png")
    ap.add_argument("--apply", action="store_true",
                    help="write the caption band into config.json captions.center_y")
    ap.add_argument("--selftest", action="store_true",
                    help="negative tests on synthetic frames (no video needed)")
    a = ap.parse_args()
    hfcfg.ensure_deps(["numpy"])
    import numpy as np
    if a.selftest:
        return selftest(np)
    hfcfg.require("ffmpeg", "ffprobe")
    cfg = hfcfg.load(a.config)
    W, H = cfg["project"]["width"], cfg["project"]["height"]
    g = grid.from_config(cfg)
    global K
    K = W / SW
    dur = float(hfcfg.probe(a.aroll, "format=duration") or 0)
    if dur <= 0:
        sys.exit(f"cannot read {a.aroll}")
    n = max(2, a.frames)
    times = [round(0.5 + (dur - 1.0) * i / (n - 1), 2) for i in range(n)]

    frames, samples = [], []
    for t in times:
        buf = grab(a.aroll, t, W, H)
        if len(buf) < SW * SH * 3:
            continue
        img = np.frombuffer(buf[:SW * SH * 3], dtype=np.uint8).reshape(SH, SW, 3)
        frames.append(img)
        m = measure_frame(np, img)
        if m:
            m["t"] = t
            samples.append(m)
    fm, notes = aggregate(np, samples, H, g)
    for n_ in notes:
        print(f"  {n_}")
    fw = fm["face_w"]
    # the person, so free zones never land on them: head box + the shirt below the collar
    fx0, fx1 = fm.pop("_face_x")
    person = [[fx0 - 0.35 * fw, fm["head_top"] - 30, fx1 + 0.35 * fw, fm["chin"] + 40]]
    if fm["chest"]:
        person.append([fm["face_cx"] - 2.0 * fw, fm["chest"][0] - 20,
                       fm["face_cx"] + 2.0 * fw, H])
    fm["free_zones"] = free_zones(np, frames, person, g["safe"]) if frames else []

    # ---- caption band: the configured one, moved onto the chest if it hits the face.
    # The head MOVES, so the test uses the low end of the chin and collar (80th percentile
    # over the frames, from aggregate()), not the average frame: a band that clears the
    # median chin still sits on the beard every time the speaker nods. It must also start
    # BELOW the collar — a line straddling neck and shirt reads as sitting on the throat.
    chin80, collar80 = fm["chin_p80"], fm["collar_p80"]
    need = round(0.15 * fw)
    cy_cfg = cfg.get("captions", {}).get("center_y")
    src = cfg.get("captions", {}).get("center_y_source", "")
    base_cy = float(cy_cfg) if (cy_cfg and src != "framing_map") else sum(g["caption_band"]) / 2
    band = [round(base_cy - BAND_H / 2), round(base_cy + BAND_H / 2)]
    clear = band[0] - chin80
    on_collar = collar80 is not None and band[0] < collar80 and band[1] > chin80
    if clear >= need and not on_collar:
        reason = (f"band {band[0]}-{band[1]} clears the chin (p80 {chin80}) by {clear} px — kept")
        moved = False
    else:
        top = max(chin80 + need, (collar80 + 24) if collar80 else 0)
        top = min(top, g["safe"][3] - BAND_H - 8)
        nb = [int(top), int(top + BAND_H)]
        why = (f"{clear} px under the chin (p80 {chin80}), needs {need}" if clear < need
               else f"straddles the collar (p80 {collar80})")
        reason = (f"default band {band[0]}-{band[1]} sits on the chin/neck ({why}) — moved "
                  f"onto the chest, {nb[0]}-{nb[1]}")
        if nb[0] - chin80 < need:
            reason += " (still tight: no room on the chest inside the safe zone — check by eye)"
        band, moved = nb, True
    fm["caption_band"] = band
    fm["caption_band_reason"] = reason
    lu = band_luma(np, frames, band) if frames else None
    fm["caption_band_luma"] = lu
    if fm.get("fallback"):
        fm["caption_band_reason"] += (" — FROM FALLBACK framing (" + ", ".join(fm["fallback"]) +
                                      " not measured): LOOK at the sheet")
    if lu is not None and lu > 0.6:
        fm["caption_band_reason"] += (f"; band is BRIGHT (p75 luma {lu:.2f}): white shadow "
                                      f"captions will not read — use captions.style \"plate\"")
    fm["samples"] = []
    for s in samples:
        row = {"t": s["t"], "how_top": s["how_top"]}
        for k, v in s.items():
            if k not in row and k not in ("shirt", "face_x") and isinstance(v, (int, float)):
                row[k] = round(v * K)
        fm["samples"].append(row)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    json.dump(fm, open(a.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    sheet(a.aroll, times, W, H, a.sheet)
    marked = os.path.splitext(a.sheet)[0] + "_marked.png"
    sheet(a.aroll, times, W, H, marked, extra=marks_filter(fm, W, H))

    print(f"  {len(samples)}/{len(times)} frames measured ({a.aroll}, {dur:.1f}s)")
    if fm.get("fallback"):
        print(f"  !!! FALLBACK: {', '.join(fm['fallback'])} could not be measured on this take "
              f"and use the house defaults (a centred medium close-up). framing.json and the "
              f"sheets are written so the pipeline continues — LOOK at the sheets and correct "
              f"those numbers in {a.out} by hand before designing.")
    print(f"  head top {fm['head_top']}   face centre ({fm['face_cx']}, {fm['face_cy']})  "
          f"width {fm['face_w']}   chin {fm['chin']}")
    print(f"  chest {fm['chest']}  (luma {fm.get('chest_luma')})   hands from y "
          f"{fm['hands_y']} in {fm['hands_frames']} frames")
    print(f"  free zones: {fm['free_zones'] or 'none ≥ 300x150'}")
    print(f"  caption band {band[0]}-{band[1]} (p75 luma {lu}): {fm['caption_band_reason']}")
    print(f"  → {a.out}\n  → {a.sheet}  (gridded: one cell = 108x192 px)\n  → {marked}  "
          f"(cyan head top, magenta chin, orange chest, yellow captions, green free zones)")
    print("  LOOK at both sheets and check these numbers against the frames before designing.")

    if a.apply:
        path = cfg["_source"] if os.path.exists(str(cfg.get("_source"))) else "config.json"
        if not os.path.exists(path):
            sys.exit("--apply: no config.json in this project")
        raw = json.load(open(path, encoding="utf-8"))
        caps = raw.setdefault("captions", {})
        before = caps.get("center_y")
        if moved:
            caps["center_y"] = int(round(sum(band) / 2))
            caps["center_y_reason"] = reason
            caps["center_y_source"] = "framing_map"
        elif caps.get("center_y_source") == "framing_map":
            for k in ("center_y", "center_y_reason", "center_y_source"):
                caps.pop(k, None)
        after = caps.get("center_y")
        # Readability is not optional for a user who never reads a warning: white shadow
        # captions on a bright band (a white shirt, a bright wall) switch to the plate style.
        lu = fm.get("caption_band_luma")
        style_now = caps.get("style", cfg.get("captions", {}).get("style", "shadow"))
        if lu is not None and lu > 0.55 and style_now == "shadow":
            caps["style"] = "plate"
            caps["style_reason"] = f"caption band is bright (p75 luma {lu:.2f}) — framing_map"
            print(f"  --apply: captions.style shadow → plate (the band behind the captions is "
                  f"bright, luma {lu:.2f}: white text would not read)")
        elif caps.get("style_reason", "").endswith("framing_map") and (lu or 0) <= 0.55:
            caps.pop("style", None)
            caps.pop("style_reason", None)
        json.dump(raw, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        if before == after:
            print(f"  --apply: captions.center_y unchanged ({after or 'grid default'})")
        else:
            print(f"  --apply: captions.center_y {before or 'grid default'} → "
                  f"{after or 'grid default'}  in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
