#!/usr/bin/env python3
"""Stills from the MASTER at every designed time, on contact sheets with the Reels grid.

    python3 scripts/qa_frames.py renders/final.mp4           # → build/qa/sheet_01.png ...
    python3 scripts/qa_frames.py renders/final.mp4 --at 1.0,6.05 --per-sheet 8

WHY. A snapshot of the composition is not the video: the encode, the caption layer, the
punch-ins and the mix only exist in the master. So the review frames come from the master,
at the moments where things go wrong — just after an element enters (mid-animation), when it
has settled, just before it leaves, on every punch-in step (a punch shrinks the margins and
clips text), and through the outro. Each frame carries the grid (red = hidden by the app,
green = safe zone, yellow = caption band) so "is it under the UI" is answered by looking.

Times come from index.html: every timed element that is not media or a caption
(headlines, moments, scenes, widgets, outro parts), every tl.set on the footage's scale, and
build/outro.json's span sampled every 0.35 s. Labels (time + what) are drawn with Pillow
when it is installed; the same list is always printed and written to build/qa/index.json.

Then LOOK at every sheet (references/qa.md §snapshots): text clipped by the frame edge,
headlines behind the head, words visible before they are spoken, missing spaces between
word spans, widgets overlapping each other or the captions, anything in the red zones,
black corners on a rotation, grey-looking glass, stacked tagline words in the outro.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402
import grid  # noqa: E402

TW, TH = 270, 480


def timed_elements(html):
    """[(start, dur, id, cls)] of every visual timed element that is not media / a caption."""
    out = []
    for m in re.finditer(r"<(div|img|svg|section|span)\b([^>]*)>", html):
        at = dict(re.findall(r'([\w-]+)="([^"]*)"', m.group(2)))
        if "data-start" not in at or "data-duration" not in at:
            continue
        cls = at.get("class", "")
        if re.search(r"\bcap\b", cls) or at.get("data-composition-id"):
            continue
        out.append((float(at["data-start"]), float(at["data-duration"]), at.get("id", ""), cls))
    return out


def punch_times(html):
    """Every scale step on the footage (#aroll / #cam)."""
    return sorted({float(t) for t in re.findall(
        r"tl\.set\(\s*['\"]#(?:aroll|cam)['\"]\s*,\s*{[^}]*scale[^}]*}\s*,\s*([\d.]+)\s*\)", html)})


def plan_times(html, end, outro=None, extra=()):
    rows = []
    for s, d, eid, cls in timed_elements(html):
        who = eid or (cls.split()[0] if cls.split() else "element")
        rows.append((s + min(0.12, d / 3), f"{who} in"))
        rows.append((s + min(0.6, d / 2), f"{who} settled"))
        if d > 1.2:
            rows.append((s + d - 0.08, f"{who} out"))
    for t in punch_times(html):
        rows.append((t + 0.04, "punch step"))
    if outro and outro.get("start") is not None:
        t, e = float(outro["start"]), float(outro.get("end") or end)
        while t < e - 0.05:
            rows.append((t, "outro"))
            t += 0.35
    for t in extra:
        rows.append((t, "requested"))
    rows = sorted((round(min(max(t, 0.0), end - 0.05), 2), w) for t, w in rows)
    out = []
    for t, w in rows:
        if out and t - out[-1][0] < 0.08:
            if w not in out[-1][1]:
                out[-1] = (out[-1][0], out[-1][1] + " + " + w)
            continue
        out.append((t, w))
    return out


def grab(src, t, path, g):
    vf = (f"scale={g['width']}:{g['height']}:force_original_aspect_ratio=increase,"
          f"crop={g['width']}:{g['height']},{grid._filters(g)},scale={TW}:{TH}")
    r = hfcfg.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", src,
                   "-frames:v", "1", "-vf", vf, path])
    return r.returncode == 0 and os.path.exists(path)


def tile(paths, labels, out, cols):
    """Pillow when available (with labels), ffmpeg tile otherwise."""
    if hfcfg.has_module("PIL"):
        from PIL import Image, ImageDraw
        rows = (len(paths) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * (TW + 4), rows * (TH + 30)), (12, 12, 12))
        d = ImageDraw.Draw(sheet)
        for k, (p, lab) in enumerate(zip(paths, labels)):
            x, y = (k % cols) * (TW + 4), (k // cols) * (TH + 30)
            sheet.paste(Image.open(p).convert("RGB"), (x, y + 30))
            d.text((x + 6, y + 8), lab[:44], fill=(255, 255, 255))
        sheet.save(out)
        return
    tmp = tempfile.mkdtemp(prefix="qasheet_")
    for k, p in enumerate(paths):
        os.link(p, os.path.join(tmp, f"f{k:03d}.png"))
    rows = (len(paths) + cols - 1) // cols
    hfcfg.run(["ffmpeg", "-v", "error", "-y", "-i", os.path.join(tmp, "f%03d.png"),
               "-vf", f"tile={cols}x{rows}:padding=4:color=black", "-frames:v", "1", out])


def main():
    ap = hfcfg.arg_parser(__doc__.split("\n\n")[0])
    ap.add_argument("master", nargs="?", help="the master mp4 (default: newest in renders/)")
    ap.add_argument("--html", default="index.html")
    ap.add_argument("--at", default="", help="extra comma-separated times")
    ap.add_argument("--per-sheet", type=int, default=12)
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--out", default="build/qa")
    ap.add_argument("--max", type=int, default=144, help="cap on the number of stills")
    a = ap.parse_args()
    cfg = hfcfg.load(a.config)
    hfcfg.require("ffmpeg", "ffprobe")
    src = a.master or max(glob.glob("renders/*.mp4"), key=os.path.getmtime, default=None)
    if not src or not os.path.exists(src):
        sys.exit("give the master mp4 (nothing in renders/)")
    if not os.path.exists(a.html):
        sys.exit(f"{a.html} not found")
    html = open(a.html, encoding="utf-8").read()
    end = float(hfcfg.probe(src, "format=duration") or 0)
    outro = json.load(open("build/outro.json")) if os.path.exists("build/outro.json") else None
    extra = [float(x) for x in a.at.split(",") if x.strip()]
    times = plan_times(html, end, outro, extra)
    if len(times) > a.max:
        step = len(times) / a.max
        times = [times[int(k * step)] for k in range(a.max)]
    g = grid.from_config(cfg)
    os.makedirs(a.out, exist_ok=True)
    for old in glob.glob(os.path.join(a.out, "sheet_*.png")):
        os.remove(old)
    tmp = tempfile.mkdtemp(prefix="qaframes_")
    stills = []
    for k, (t, why) in enumerate(times):
        p = os.path.join(tmp, f"s{k:03d}.png")
        if grab(src, t, p, g):
            stills.append((t, why, p))
    index = []
    for n in range(0, len(stills), a.per_sheet):
        chunk = stills[n:n + a.per_sheet]
        out = os.path.join(a.out, f"sheet_{n // a.per_sheet + 1:02d}.png")
        tile([p for _, _, p in chunk], [f"{t:.2f}s {why}" for t, why, _ in chunk], out,
             min(a.cols, len(chunk)))
        for k, (t, why, _) in enumerate(chunk):
            index.append({"sheet": os.path.basename(out), "cell": k + 1, "t": t, "what": why})
    json.dump(index, open(os.path.join(a.out, "index.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"  {len(stills)} stills from {src} on {(len(stills) + a.per_sheet - 1) // a.per_sheet} "
          f"sheet(s) in {a.out}/ (red = hidden by the app, green = safe, yellow = caption band)")
    cur = None
    for r in index:
        if r["sheet"] != cur:
            cur = r["sheet"]
            print(f"  {cur}:")
        print(f"    {r['cell']:2d}  {r['t']:6.2f}s  {r['what']}")
    print("  LOOK at every sheet — the checklist is in references/qa.md §snapshots")
    return 0


if __name__ == "__main__":
    sys.exit(main())
