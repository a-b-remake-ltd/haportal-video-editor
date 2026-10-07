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
                             "caption_band_reason", "samples": [...per frame...]}

How each number is measured (median over the frames, so one gesture cannot move it):
  face      skin mask r>95 & r>g+16 & g>b+6 & 40<r-b<130 (references/layout.md, the same mask
            outro.py uses); the widest column run of skin above y 1500 is the face. face_cx is
            its skin-weighted centre, face_w its width.
  head_top  hair by LUMA: the patch just above the first skin row gives the hair's luma; rows
            above it that still match (within the face columns) are hair; the topmost is the
            head top. Bald → the skin top. Capped at 0.75 face widths above the skin.
  chest     the shirt: its colour is the median of a patch well below the face; the chest is the
            run of rows where that colour fills the band under the chin.
  chin      the last face-wide skin row, pushed down to just above the shirt when a beard or a
            shadow hides the jaw (collar − 0.25 face widths), never below the collar.
  hands_y   the highest skin seen below the collar outside the neck column (None = no hands).
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


def measure_frame(np, img):
    """One frame (SH x SW x 3 uint8) → dict in SAMPLE px, or None when no face is found."""
    f = img.astype(np.int16)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    skin = (r > 95) & (r > g + 16) & (g > b + 6) & ((r - b) > 40) & ((r - b) < 130)
    luma = (0.299 * r + 0.587 * g + 0.114 * b)
    r0, r1 = int(SH * 100 / 1920), int(SH * 1500 / 1920)
    cols = skin[r0:r1].sum(axis=0)
    peak = int(cols.max())
    if peak < 4:
        return None
    on = cols >= max(3, 0.25 * peak)
    best, cur = (0, 0), None
    for x in range(SW + 1):
        o = x < SW and on[x]
        if o and cur is None:
            cur = x
        elif not o and cur is not None:
            if x - cur > best[1] - best[0]:
                best = (cur, x)
            cur = None
    a, bb = best
    w = bb - a
    if not (8 <= w <= 0.62 * SW):
        return None
    cx = float((np.arange(a, bb) + 0.5) @ cols[a:bb]) / max(1, int(cols[a:bb].sum()))
    rows = skin[:, a:bb].sum(axis=1)
    top = next((y for y in range(r0, r1) if rows[y] >= 0.3 * w), None)
    if top is None:
        return None
    # last face-wide skin row (stops at the first long run without skin: beard / collar)
    lo, hi = max(0, int(a - 0.15 * w)), min(SW, int(bb + 0.15 * w))
    wide = skin[:, lo:hi].sum(axis=1)
    face_bot, gap = top, 0
    for y in range(top, min(SH, int(top + 2.2 * w))):
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
        hp = skin[hy0:hy1, hx0:hx1].mean()
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
    # hands: skin under the collar, outside the neck column
    # A hand is a BLOB: 6 consecutive rows, each with skin, 60+ skin px together. Warm
    # bokeh lights and a lamp pass the skin mask too, but as specks — this skips them.
    hands = None
    if collar is not None:
        side = skin[collar:, :].copy()
        side[:, max(0, int(cx - 0.4 * w)):min(SW, int(cx + 0.4 * w))] = False
        per = side.sum(axis=1)
        for y in range(0, len(per) - 6):
            win = per[y:y + 6]
            if win.min() >= 3 and win.sum() >= 60:
                hands = collar + y
                break
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


def main():
    ap = hfcfg.arg_parser(__doc__.split("\n\n")[0])
    ap.add_argument("aroll", nargs="?", default="assets/aroll.mp4")
    ap.add_argument("--frames", type=int, default=10)
    ap.add_argument("--out", default="build/framing.json")
    ap.add_argument("--sheet", default="build/framing.png")
    ap.add_argument("--apply", action="store_true",
                    help="write the caption band into config.json captions.center_y")
    a = ap.parse_args()
    hfcfg.ensure_deps(["numpy"])
    import numpy as np
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
    if len(samples) < 2:
        sys.exit("no stable face found in the samples — place by eye from the sheet "
                 "(python3 scripts/grid.py overlay) and set captions.center_y by hand")

    # Drop implausible per-frame readings BEFORE aggregating: a hand raised to the chest or
    # a microphone read as the collar gave a "chin" 700 px below the face on a real phone
    # take, and the 80th percentile then pushed the caption band onto the hands. A chin sits
    # 0.35-1.4 face widths below the face centre; a collar sits within ~1.2 face widths of it.
    dropped = 0
    for sm in samples:
        fw_s, fcy = sm.get("face_w"), sm.get("face_cy")
        if not fw_s or fcy is None:
            continue
        if sm.get("chin") is not None and not (0.35 * fw_s <= sm["chin"] - fcy <= 1.4 * fw_s):
            sm["chin"], dropped = None, dropped + 1
        ch = sm.get("chin")
        if sm.get("collar") is not None and ch is not None and \
                not (-0.15 * fw_s <= sm["collar"] - ch <= 1.2 * fw_s):
            sm["collar"], sm["chest_bot"], dropped = None, None, dropped + 1
    if dropped:
        print(f"  ignored {dropped} implausible chin/collar reading(s) (a hand or a mic in "
              f"front of the chest)")
    med = lambda k: float(np.median([s[k] for s in samples if s.get(k) is not None])) \
        if any(s.get(k) is not None for s in samples) else None
    fw = med("face_w") * K
    fm = {"head_top": round(med("head_top") * K), "face_cx": round(med("face_cx") * K),
          "face_cy": round(med("face_cy") * K), "face_w": round(fw), "chin": round(med("chin") * K)}
    col, cb = med("collar"), med("chest_bot")
    fm["chest"] = None
    if col is not None:
        y0 = round(col * K)
        y1 = round(min((cb if cb is not None else H / K) * K, g["safe"][3]))
        fm["chest"] = [y0, y1] if y1 > y0 + 40 else [y0, g["safe"][3]]   # never upside down
    hs = sorted(s["hands"] for s in samples if s.get("hands") is not None)
    fm["hands_y"] = round(hs[len(hs) // 5] * K) if hs else None
    fm["hands_frames"] = f"{len(hs)}/{len(samples)}"
    shirts = [s["shirt"] for s in samples if s.get("shirt")]
    if shirts:
        sc = np.median(np.array(shirts), axis=0)
        fm["chest_luma"] = round(float(0.299 * sc[0] + 0.587 * sc[1] + 0.114 * sc[2]) / 255, 3)

    # the person, so free zones never land on them: head box + the shirt below the collar
    fx0 = min(s["face_x"][0] for s in samples) * K
    fx1 = max(s["face_x"][1] for s in samples) * K
    person = [[fx0 - 0.35 * fw, fm["head_top"] - 30, fx1 + 0.35 * fw, fm["chin"] + 40]]
    if fm["chest"]:
        person.append([fm["face_cx"] - 2.0 * fw, fm["chest"][0] - 20,
                       fm["face_cx"] + 2.0 * fw, H])
    fm["free_zones"] = free_zones(np, frames, person, g["safe"])

    # ---- caption band: the configured one, moved onto the chest if it hits the face.
    # The head MOVES, so the test uses the low end of the chin and collar (80th percentile
    # over the frames), not the average frame: a band that clears the median chin still
    # sits on the beard every time the speaker nods. It must also start BELOW the collar —
    # a line straddling neck and shirt reads as sitting on the throat.
    p80 = lambda k: float(np.percentile([s[k] for s in samples if s.get(k) is not None], 80)) * K \
        if any(s.get(k) is not None for s in samples) else None
    chin80 = round(p80("chin"))
    collar80 = round(p80("collar")) if fm["chest"] and p80("collar") is not None else None
    fm["chin_p80"], fm["collar_p80"] = chin80, collar80
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
    lu = band_luma(np, frames, band)
    fm["caption_band_luma"] = lu
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
