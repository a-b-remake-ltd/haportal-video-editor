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

Caption hiding: build_index.py writes build/caption_hide.json — the windows in which the hook
world, a kinetic headline, a designed moment marked "hide_captions" or the outro owns the frame.
Inside a window the layer is TRANSPARENT. captions.py already splits cards at window edges, so
normally a card is either fully hidden ("hidden": true) or fully outside. When captions.json is
STALE (a headline moved after captions.py ran), a card can cross a window edge. Then:
  * the piece BEFORE the window shows only the words spoken before the window
  * the piece AFTER the window RE-STARTS the card with only the words not yet spoken, from
    the first of those words — never the half card again with words the headline already
    showed (the old behaviour: the card reappeared with spoken words on it)
  * a piece with no words left is transparent
Every card keeps its exact slot in the concat list and the pieces sum to its duration, so the
layer's length — and with it finish.py's overlay — is unchanged. Only pixels go missing.

Gates (exit 1): a visible piece of the layer that starts inside a hidden window, or shows a
word spoken inside one (captions.word_zone: by where most of the word is spoken), and a
plate whose INK is not centred on the frame (x 540, grid.centered_box) — measured on the
rendered stills' alpha, ±4 px. Stale input is reported (re-run captions.py) but fixed, not fatal;
preflight_qa.py fails a stale captions.json.
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
          direction:rtl; font-family:"{family}", "Inter", sans-serif;
          line-height:1.0; }}
  .cap .p {{ display:inline-block; font-size:{size}px; padding:20px 34px 26px; direction:{dir};
             white-space:nowrap; {paint} }}
  .ltr {{ unicode-bidi:isolate; direction:ltr; }}
  .ai {{ font-family:"Roboto Slab", serif; font-weight:{aiw}; letter-spacing:.02em; }}
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

    Each card holds until the NEXT card's start when the two are back to back (a caption's
    dur is `next − start − 0.005`, the composition's anti-stacking gap; feeding those to the
    concat demuxer made every card start 0.005 s earlier than the one before it — 3 frames
    early by card 27, 7 by card 57, measured). A real gap (a trimmed pause, a hidden window)
    and any time before the first card become transparent stretches, so the layer's clock is
    the composition's clock. Hidden cards are transparent stretches too."""
    segs, t = [], 0.0
    for k, c in enumerate(caps):
        s0 = float(c["start"])
        e0 = s0 + float(c["dur"])
        nxt = float(caps[k + 1]["start"]) if k + 1 < len(caps) else e0
        if s0 > t + 1e-6:
            segs.append([t, s0, None])
        s0 = max(s0, t)
        end = nxt if nxt - e0 <= 0.0055 + 1e-6 else e0
        end = max(end, s0)
        segs.append([s0, end, None if c.get("hidden") else c])
        t = end
        if end < nxt - 1e-6:
            segs.append([end, nxt, None])
            t = nxt
    return segs


def plan_layer(segs, windows, frame=0.04):
    """[(t0, t1, card-or-None, words-shown-or-None)] — the visible pieces carry the exact
    words they show. See the module docstring for the re-start rule."""
    import captions as capmod
    out = []
    for t0, t1, c in segs:
        if c is None:
            out.append((t0, t1, None, None))
            continue
        words = c.get("words")
        t = t0
        for d, vis in pieces(t0, t1 - t0, windows):
            p0, p1 = t, t + d
            t = p1
            if not vis:
                out.append((p0, p1, None, None))
                continue
            if not words:                       # an old captions.json without word times
                if p0 > t0 + 1e-6:
                    out.append((p0, p1, None, None))   # never replay a half card blind
                else:
                    out.append((p0, p1, c, None))
                continue
            shown = [w for w in words if capmod.word_zone(w, windows) < 0 and w[0] < p1 - 1e-6
                     and (p0 <= t0 + 1e-6 or w[0] >= p0 - frame)]
            if not shown:
                out.append((p0, p1, None, None))
                continue
            first = shown[0][0]
            if p0 > t0 + 1e-6 and first > p0 + frame / 2:
                # re-start: transparent until the first unspoken word, then the rest of the card
                q = capmod.snap(first)
                if q >= p1 - 1e-6:
                    out.append((p0, p1, None, None))
                    continue
                if q > p0 + 1e-6:
                    out.append((p0, q, None, None))
                    p0 = q
            out.append((p0, p1, c, shown if len(shown) < len(words) else None))
    return out


def gate(plan, windows, frame=0.04):
    """The layer's own assertions. Returns a list of problems."""
    import captions as capmod
    bad = []
    for p0, p1, c, shown in plan:
        if c is None or p1 - p0 < 1e-6:
            continue
        win = capmod.inside(p0, windows)
        if win:
            bad.append(f"c{c['i']:02d} visible from {p0:.2f}s, inside hidden window "
                       f"{win[0]:.2f}-{win[1]:.2f}")
        for w in (shown if shown is not None else (c.get("words") or [])):
            if capmod.word_zone(w, windows) >= 0:
                bad.append(f"c{c['i']:02d} shows '{w[2]}' ({w[0]:.2f}s), a word spoken inside "
                           f"a hidden window — the headline already showed it")
    return bad


def ink_span(png, top, height, W, thr=128):
    """(x0, x1) of the caption's visible ink in a rendered still: the columns where the
    alpha reaches `thr` inside the plate's rows, or None for an empty still. Real pixels,
    not the CSS: the centring gate measures what the viewer sees (a plate the layout
    centred on the safe zone measured left margin 347 px, right margin 429 px)."""
    y0 = max(0, int(top) - 10)
    h = int(height) + 20
    r = hfcfg.run(["ffmpeg", "-v", "error", "-i", png, "-vf",
                   f"alphaextract,crop={W}:{h}:0:{y0}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                  text=False)
    b = r.stdout or b""
    rows = len(b) // W
    if not rows:
        return None
    hit = [x for x in range(W) if any(b[y * W + x] >= thr for y in range(rows))]
    return (hit[0], hit[-1] + 1) if hit else None


def centring_gate(spans, g, tol=4):
    """Every visible caption still must sit where grid.centered_box() puts a plate of its
    width (on x 540 up to 800 px). Returns problems."""
    bad = []
    for key, (x0, x1) in sorted(spans.items()):
        err = grid.centring_error([x0, 0, x1, 1], g)
        if abs(err) > tol:
            bad.append(f"{key} not centred: ink x {x0}-{x1} (left margin {x0}px, right margin "
                       f"{g['width'] - x1}px, off by {err:+.0f}px)")
    return bad


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
    ap.add_argument("--outro", default="build/outro.json",
                    help="the outro plan: captions are hidden from its start")
    a = ap.parse_args()
    cfg = hfcfg.load(a.config)
    hfcfg.require("ffmpeg", "ffprobe")

    W = cfg["project"]["width"]
    H = cfg["project"]["height"]
    fps = cfg["project"]["fps"]
    b = cfg["brand"]
    g = grid.from_config(cfg)
    # captions centre on the FRAME (x 540) in the centred lane, x 140-940 on Reels
    # (grid.centered_box); fit_captions.py keeps every plate inside the lane's width
    lane = grid.center_lane(g)
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

    import captions as capmod
    windows = capmod.load_hide(a.hide, a.outro) if a.hide else []
    stale = [c for c in caps if not c.get("hidden") and capmod.inside(float(c["start"]), windows)]
    cross = [c for c in caps if not c.get("hidden") and any(
        float(c["start"]) < x < float(c["start"]) + float(c["dur"]) - 1e-6 for x, _ in windows)]
    plan = plan_layer(layer_segments(caps), windows, 1.0 / fps)
    problems = gate(plan, windows, 1.0 / fps)

    def still(c, shown):
        key = f"c{c['i']:03d}" if shown is None else \
            f"c{c['i']:03d}_{len(shown)}w{int(round(shown[0][0] * 100))}"
        png = os.path.join(png_dir, key + ".png")
        if key in made:
            return png
        made.add(key)
        text = c["text"] if shown is None else capmod.render_text(
            [tuple(w) for w in shown], cfg["language"]["direction"])
        # The slot comes from the beat map — never from a second, hand-kept table.
        top = grid.slot_top(cfg, beatmap, c["start"])
        hp = os.path.join(html_dir, key + ".html")
        with open(hp, "w", encoding="utf-8") as f:
            f.write(PAGE.format(W=W, H=H, faces=faces, top=top,
                                left=lane[0], width=lane[1],
                                dir=cfg["language"]["direction"], family=b["font_family"],
                                size=c.get("size", b["caption_size"]),
                                paint=grid.caption_css(cfg), text=text,
                                # the slab "AI" follows the caption weight (+100: a slab reads
                                # lighter than a sans at the same weight) — 800 in a 500 line
                                # looks like an accidental bold
                                aiw=min(900, int(cfg.get("captions", {}).get("weight", 800)) + 100)))
        shoot(chrome, hp, png, W, H)
        sp = ink_span(png, top, grid.plate_height(c.get("size", b["caption_size"])), W)
        if sp:
            spans[key] = sp
        return png

    made, spans = set(), {}
    blank = os.path.abspath(blank_png(os.path.join(png_dir, "blank.png"), W, H))
    entries = []
    for p0, p1, c, shown in plan:
        if p1 - p0 < 1e-6:
            continue
        img = blank if c is None else os.path.abspath(still(c, shown))
        if entries and entries[-1][0] == img:
            entries[-1][1] += p1 - p0
        else:
            entries.append([img, p1 - p0])
    shown_cards = {c["i"] for _, _, c, _ in plan if c is not None}
    print(f"  rendered {len(made)} stills for {len(shown_cards)} visible cards"
          f" ({len(caps) - len(shown_cards)} hidden)")
    # the composition's end: the outro's window is stored open-ended (start → end + 10 s) so
    # it can never let a card through; printed raw it read "52.32-69.84" on a 59.84 s reel
    comp_end = caps[-1]["start"] + caps[-1]["dur"] if caps else 0.0
    if a.outro and os.path.exists(a.outro):
        try:
            comp_end = float(json.load(open(a.outro, encoding="utf-8")).get("end") or comp_end)
        except (OSError, ValueError):
            pass
    if windows:
        print(f"  hidden over {len(windows)} window(s): "
              + ", ".join(f"{x:.2f}-{min(y, comp_end):.2f}" for x, y in windows if x < comp_end))
    restarted = [(p0, c, shown) for p0, p1, c, shown in plan if c is not None and shown is not None]
    if stale or cross:
        print(f"  ! captions.json is STALE against {a.hide}: {len(stale)} card(s) start inside "
              f"a window, {len(cross)} cross a window edge — handled here (trimmed to the words "
              f"before the window, re-started after it), but run captions.py again so "
              f"captions.json matches:")
        for c in (stale + cross)[:8]:
            print(f"      c{c['i']:02d} {float(c['start']):.2f}s '{c['plain']}'")
    for p0, c, shown in restarted:
        print(f"      c{c['i']:02d} piece at {p0:.2f}s shows only: {' '.join(w[2] for w in shown)}")

    lst = os.path.join(a.workdir, "concat.txt")
    last = None
    with open(lst, "w") as f:
        for img, d in entries:
            last = img
            f.write(f"file '{img}'\n")
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
    problems += centring_gate(spans, g)
    if spans:
        errs = sorted(abs(grid.centring_error([x0, 0, x1, 1], g)) for x0, x1 in spans.values())
        print(f"  centring: {len(spans)} still(s) measured on the pixels, worst {errs[-1]:.1f}px "
              f"off x {g['center_x']}")
    if problems:
        print("  ✗ " + "\n  ✗ ".join(problems))
        return 1
    print("  ✓ no visible caption starts inside a hidden window, none replays a word the "
          "headline already showed, every plate centred on the frame")
    return 0


if __name__ == "__main__":
    sys.exit(main())
