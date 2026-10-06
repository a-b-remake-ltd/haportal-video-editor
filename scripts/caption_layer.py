#!/usr/bin/env python3
"""Render the caption cards to a transparent video layer, composited OUTSIDE the renderer.

Why: HyperFrames can leave the LAST clip of each CSS slot painted for the whole render
once a composition carries ~48 text clips — and snapshots do NOT show it, only the
encoded file does. Adding class="clip", alternating tracks and giving every caption its
own track all fail to fix it. See references/hyperframes.md.

The bug is not deterministic above 48 clips, so run `check_ghosting.py` on a draft
render before paying this cost blindly — a 53-caption composition has rendered clean.

Each card is drawn by headless Chrome (same CSS, same brand font, correct complex-script
shaping) into a 1080x1920 RGBA PNG; the stills are concatenated into a VP9 alpha webm on
the exact caption timings. No node, no puppeteer.

Bonus: re-timing a caption then costs an overlay pass instead of a full re-render.

Caption hiding: build_index.py writes build/caption_hide.json — the windows in which a
kinetic headline (or any element marked "hide_captions") owns the frame. Inside a window the
layer is TRANSPARENT; a card that straddles a window edge is split into a visible piece and a
blank piece. captions.json is never touched and every card keeps its exact slot in the
concat list, so the layer's total length — and with it the caption timeline, preflight's
continuity check and finish.py's overlay — stay identical. Only pixels go missing, never time.
"""
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402
import grid  # noqa: E402

beatmap, BEATS_PATH = hfcfg.load_beats()   # project copy wins over the skill's stub

PAGE = """<!doctype html><meta charset="utf-8">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html, body {{ width:{W}px; height:{H}px; background:transparent; overflow:hidden; }}
{faces}
  .cap {{ position:absolute; left:{left}px; width:{width}px; top:{top}px; text-align:center;
          direction:{dir}; font-family:"{family}", "Inter", sans-serif;
          line-height:1.0; }}
  .cap .p {{ display:inline-block; font-size:{size}px; padding:20px 34px 26px;
             white-space:nowrap; {paint} }}
  .ltr {{ unicode-bidi:isolate; direction:ltr; }}
  .ai {{ font-family:"Roboto Slab", serif; font-weight:800; letter-spacing:.02em; }}
</style>
<div class="cap"><span class="p">{text}</span></div>"""


def font_faces(font_dir, family):
    """@font-face blocks for whatever is in the font dir.

    A single file named after the family is treated as a VARIABLE font and gets the
    full 100-1000 range; without that Chrome clamps it to one instance and every
    weight in the composition renders identically.
    """
    faces, files = [], sorted(glob.glob(os.path.join(font_dir, "*.ttf")) +
                              glob.glob(os.path.join(font_dir, "*.otf")))
    if not files:
        return ""
    variable = [f for f in files
                if os.path.splitext(os.path.basename(f))[0].lower() == family.lower()]
    if variable:
        url = "file://" + os.path.abspath(variable[0])
        faces.append(f'  @font-face {{ font-family:"{family}"; src:url("{url}");'
                     f' font-weight:100 1000; }}')
        return "\n".join(faces)
    weights = {"thin": 100, "extralight": 200, "light": 300, "regular": 400, "medium": 500,
               "semibold": 600, "bold": 700, "extrabold": 800, "black": 900}
    for f in files:
        stem = os.path.splitext(os.path.basename(f))[0]
        suffix = stem.split("-")[-1].replace(" ", "").lower()
        w = weights.get(suffix, 400)
        url = "file://" + os.path.abspath(f)
        faces.append(f'  @font-face {{ font-family:"{family}"; src:url("{url}");'
                     f' font-weight:{w}; }}')
    return "\n".join(faces)


def shoot(chrome, html_path, png_path, w, h):
    r = subprocess.run(
        [chrome, "--headless", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
         "--allow-file-access-from-files", "--font-render-hinting=none",
         "--default-background-color=00000000", "--virtual-time-budget=1200",
         f"--screenshot={png_path}", f"--window-size={w},{h}",
         "file://" + os.path.abspath(html_path)],
        capture_output=True, text=True)
    if not os.path.exists(png_path):
        sys.exit(f"Chrome produced no screenshot for {html_path}\n{r.stderr[-800:]}")


def load_hide(path):
    """The merged hide windows [[a, b], ...] from build_index.py; [] when there are none."""
    if not path or not os.path.exists(path):
        return []
    d = json.load(open(path, encoding="utf-8"))
    return sorted([float(a), float(b)] for a, b in (d.get("windows") or []) if float(b) > float(a))


def pieces(start, dur, windows, min_piece=0.02):
    """Split one card's [start, start+dur) into (duration, visible) pieces around the hide
    windows. The durations always sum to `dur` exactly — that is the no-drift guarantee.
    A sliver shorter than `min_piece` (the 0.005 s caption gap, a rounding crumb) is folded
    into its neighbour instead of becoming a one-frame flash."""
    end = start + dur
    out, t = [], start
    for a, b in windows:
        if b <= t or a >= end:
            continue
        if a > t:
            out.append([a - t, True])
        out.append([min(b, end) - max(a, t), False])
        t = min(b, end)
    if end > t:
        out.append([end - t, True])
    merged = []
    for d, vis in out:                    # a sliver (or an equal neighbour) joins the previous
        if merged and (merged[-1][1] == vis or d < min_piece):
            merged[-1][0] += d
        else:
            merged.append([d, vis])
    if len(merged) > 1 and merged[0][0] < min_piece:    # a LEADING sliver joins the next
        merged[1][0] += merged.pop(0)[0]
    final = []
    for d, vis in merged:
        if final and final[-1][1] == vis:
            final[-1][0] += d
        else:
            final.append([d, vis])
    return [(round(d, 6), vis) for d, vis in final]


def layer_segments(caps):
    """The layer as ABSOLUTE stretches [t0, t1, card-or-None] from t=0, back to back.

    Each card holds until the NEXT card's start, not for its own `dur`: a caption clip's dur
    is `next − start − 0.005` (the composition's anti-stacking gap), and feeding those to the
    concat demuxer made every card start 0.005 s earlier than the one before it — 3 frames
    early by card 27, 7 by card 57 (measured). A real gap (> 1 frame of nothing) and any time
    before the first card become transparent stretches, so the layer's clock is the
    composition's clock."""
    segs, t = [], 0.0
    for k, c in enumerate(caps):
        s0 = float(c["start"])
        e0 = s0 + float(c["dur"])
        nxt = float(caps[k + 1]["start"]) if k + 1 < len(caps) else e0
        if s0 > t + 1e-6:
            segs.append([t, s0, None])
        end = nxt if nxt - e0 <= 0.0055 + 1e-6 else e0
        segs.append([s0, end, c])
        t = end
        if end < nxt - 1e-6:
            segs.append([end, nxt, None])
            t = nxt
    return segs


def blank_png(path, w, h):
    """One fully transparent frame for the hidden stretches."""
    if not os.path.exists(path):
        r = hfcfg.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                       "-i", f"color=c=black@0.0:s={w}x{h},format=rgba",
                       "-frames:v", "1", "-pix_fmt", "rgba", path])
        if r.returncode or not os.path.exists(path):
            sys.exit(f"could not write the transparent frame {path}\n{r.stderr[-400:]}")
    return path


def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("--captions", default="captions.json")
    ap.add_argument("--out", default="assets/captions.webm")
    ap.add_argument("--workdir", default="build/caps")
    ap.add_argument("--hide", default="build/caption_hide.json",
                    help="hide windows written by build_index.py (headlines, hide_captions)")
    a = ap.parse_args()
    cfg = hfcfg.load(a.config)
    hfcfg.require("ffmpeg", "ffprobe")

    W = cfg["project"]["width"]
    H = cfg["project"]["height"]
    fps = cfg["project"]["fps"]
    b = cfg["brand"]
    g = grid.from_config(cfg)          # captions centre on the Reels safe zone, x 500
    font_dir = b["font_dir"]            # the project's copy (fonts.ensure fills it)
    import fonts
    fams = [b["font_family"], "Roboto Slab", "Inter"]
    if fonts.ensure(fams, font_dir):
        sys.exit(f"fonts missing in {font_dir} — run scripts/setup_assets.py")
    faces = fonts.font_faces_css(fams, font_dir,
                                 url_prefix="file://" + os.path.abspath(font_dir) + "/")

    caps = json.load(open(a.captions, encoding="utf-8"))
    html_dir = os.path.join(a.workdir, "html")
    png_dir = os.path.join(a.workdir, "png")
    os.makedirs(html_dir, exist_ok=True)
    os.makedirs(png_dir, exist_ok=True)
    chrome = hfcfg.chrome_path()

    windows = load_hide(a.hide)
    segs = layer_segments(caps)
    plan = [(c, pieces(t0, t1 - t0, windows)) for t0, t1, c in segs]
    for c in caps:
        if not any(vis for cc, ps in plan if cc is c for _, vis in ps):
            continue                     # fully hidden: no still needed
        # The slot comes from the beat map — never from a second, hand-kept table.
        top = grid.slot_top(cfg, beatmap, c["start"])
        hp = os.path.join(html_dir, f"c{c['i']:03d}.html")
        with open(hp, "w", encoding="utf-8") as f:
            f.write(PAGE.format(W=W, H=H, faces=faces, top=top,
                                left=g["safe"][0], width=g["safe_width"],
                                dir=cfg["language"]["direction"], family=b["font_family"],
                                size=c.get("size", b["caption_size"]),
                                paint=grid.caption_css(cfg), text=c["text"]))
        shoot(chrome, hp, os.path.join(png_dir, f"c{c['i']:03d}.png"), W, H)
    shown = sum(1 for c in caps if any(vis for cc, ps in plan if cc is c for _, vis in ps))
    print(f"  rendered {shown} cards" + (f" ({len(caps) - shown} fully hidden)" if shown < len(caps) else ""))
    # absolute: the concat demuxer resolves a relative path against concat.txt's folder
    blank = os.path.abspath(blank_png(os.path.join(png_dir, "blank.png"), W, H))
    if windows:
        cut = sum(1 for c, ps in plan if c is not None and len(ps) > 1)
        print(f"  hidden over {len(windows)} window(s) from {a.hide}: "
              + ", ".join(f"{x:.2f}-{y:.2f}" for x, y in windows)
              + (f" — {cut} card(s) split at a window edge" if cut else ""))

    lst = os.path.join(a.workdir, "concat.txt")
    last = None
    with open(lst, "w") as f:
        for c, ps in plan:
            for d, vis in ps:
                last = (f"{os.path.abspath(png_dir)}/c{c['i']:03d}.png"
                        if vis and c is not None else blank)
                f.write(f"file '{last}'\n")
                f.write(f"duration {d:.6f}\n")
        f.write(f"file '{last}'\n")

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    # -t: the concat demuxer holds the repeated last still for its full duration again on
    # current ffmpeg, so the layer ran ~one card long and covered whatever came next.
    want = caps[-1]["start"] + caps[-1]["dur"]
    r = hfcfg.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                   "-t", f"{want:.3f}",
                   "-vf", f"fps={fps},format=yuva420p",
                   "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "24",
                   "-auto-alt-ref", "0", a.out])
    if r.returncode:
        sys.exit(f"webm encode failed:\n{r.stderr}")

    dur = hfcfg.probe(a.out, "format=duration")
    print(f"  {a.out}  {float(dur or 0):.2f}s  ({len(caps)} cards, timeline ends {want:.2f}s)")
    if dur and abs(float(dur) - want) > 0.12:
        print(f"  ! layer length {float(dur):.2f}s vs caption timeline {want:.2f}s — "
              f"the overlay will drift; check captions.json durations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
