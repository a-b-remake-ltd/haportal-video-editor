#!/usr/bin/env python3
"""The Instagram Reels grid — where things may sit so the platform UI never covers them.

ONE module owns these numbers. The caption slots, the caption-width limit, the outro
layout and the grid gate all read them from here, so a change lands everywhere at once.

Profile "reels" (the default) was measured on real Reels and approved by the editor:

    hidden   top      y 0-220            reel title / status bar
             bottom   y 1520-1920        username, caption text, audio line
             rail     x 940-1080 at y 880-1520   like / comment / share column
    safe     x 60-940, y 220-1520        every readable thing and every logo lives here
    centre   x 500, not 540              the safe zone is asymmetric (the rail eats the right)
    captions band y 1110-1190, centred on x 500
    headline right margin 160 px (RTL stacks hang from x 920)
    bottom cards  anchored to y 1520 and grow UP, at most ~300 px, so they never reach
                  the caption band
    cover    the profile grid shows the reel cropped to 3:4 — y 240-1680. A cover frame
             or opening title must read inside that crop.

Profile "ads" is Meta's official guidance for paid placements: 14 % top, 35 % bottom,
6 % on each side. Stricter — use it when the reel will run as a sponsored ad.

Every number scales with the frame: a 2160x3840 master gets the same zones x2.

Usage
  python3 scripts/grid.py show [--profile ads]
  python3 scripts/grid.py overlay renders/draft.mp4 --at 0.5,3.2,8 -o build/grid.png
  python3 scripts/grid.py overlay build/snap/frame-001.png -o build/grid.png
  python3 scripts/grid.py check index.html [--at 1.0,4.5] [--profile reels]

`check` is the gate: it loads the composition in headless Chrome, seeks the timeline to
each time, and fails on any visible text, image or logo that crosses into a hidden zone.
Mark a purely decorative element that is MEANT to bleed (a background wash, a texture)
with data-grid="bleed" and it is skipped.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

BASE_W, BASE_H = 1080, 1920

PROFILES = {
    "reels": {
        "hidden": {"top": [0, 0, 1080, 220],
                   "bottom": [0, 1520, 1080, 1920],
                   "rail": [940, 880, 1080, 1520]},
        "safe": [60, 220, 940, 1520],
        "center_x": 500,
        "caption_band": [1110, 1190],
        "headline_right_margin": 160,
        "bottom_card_max_h": 300,
        "cover": [0, 240, 1080, 1680],
    },
    "ads": {
        "hidden": {"top": [0, 0, 1080, 269],
                   "bottom": [0, 1248, 1080, 1920],
                   "left": [0, 0, 65, 1920],
                   "right": [1015, 0, 1080, 1920]},
        "safe": [65, 269, 1015, 1248],
        "center_x": 540,
        "caption_band": [1100, 1180],
        "headline_right_margin": 120,
        "bottom_card_max_h": 220,
        "cover": [0, 240, 1080, 1680],
    },
}


def _scale(v, k):
    if isinstance(v, list):
        return [_scale(x, k) for x in v]
    if isinstance(v, dict):
        return {a: _scale(b, k) for a, b in v.items()}
    return round(v * k)


def profile(name="reels", width=BASE_W, height=BASE_H):
    """The grid for one profile, scaled to the frame. Vertical 9:16 only."""
    if name not in PROFILES:
        sys.exit(f"unknown grid profile {name!r} — choose from {', '.join(PROFILES)}")
    if abs(width / height - BASE_W / BASE_H) > 0.01:
        sys.exit(f"grid profiles are 9:16; this frame is {width}x{height}")
    g = _scale(PROFILES[name], width / BASE_W)
    g["name"], g["width"], g["height"] = name, width, height
    x0, _, x1, _ = g["safe"]
    g["safe_width"] = x1 - x0
    return g


def from_config(cfg):
    p = cfg.get("grid", {}).get("profile", "reels")
    return profile(p, cfg["project"]["width"], cfg["project"]["height"])


def caption_top(g, plate_h):
    """Top of a caption plate whose centre sits in the middle of the caption band."""
    a, b = g["caption_band"]
    return round((a + b) / 2 - plate_h / 2)


SPEAKER_SLOTS = ("hook", "std")


def slot_top(cfg, beatmap, t):
    """The caption plate top at time t — ONE function for the builder and the caption layer.

    The beat map picks the slot. For the speaker slots, an analysed reference may move the
    band (config captions.center_y, already clamped into the safe zone by apply_style.py);
    the B-roll slots keep their own positions because they exist to clear the action.
    """
    plate_h = plate_height(cfg["brand"]["caption_size"])
    if beatmap and getattr(beatmap, "BEATS", None):
        kind = beatmap.slot_at(t)
        top = beatmap.SLOT[kind]
    else:
        kind, top = "std", caption_top(from_config(cfg), plate_h)
    cy = cfg.get("captions", {}).get("center_y")
    if kind in SPEAKER_SLOTS:
        # speaker slots always follow the CONFIGURED caption size: the beats.py stub bakes a
        # number at import time and cannot know it
        top = round(float(cy) - plate_h / 2) if cy else caption_top(from_config(cfg), plate_h)
    return top


def caption_css(cfg):
    """The plate's paint for the configured caption style ("plate" or "shadow")."""
    b, c = cfg["brand"], cfg.get("captions", {})
    w = c.get("weight", 800)
    if c.get("style", "plate") == "shadow":
        # white type, soft shadow, no box — the look of most agency / interview reels
        return (f"font-weight:{w}; color:{c.get('shadow_color', '#ffffff')}; background:none; "
                f"box-shadow:none; text-shadow:0 2px 12px rgba(0,0,0,.62), 0 0 2px rgba(0,0,0,.5);")
    return (f"font-weight:{w}; color:{b['caption_ink']}; background:{b['caption_plate']}; "
            f"border-radius:22px; box-shadow:0 10px 34px rgba(0,0,0,0.42);")


def plate_height(font_px, pad_top=20, pad_bottom=26, line_height=1.0):
    return round(font_px * line_height + pad_top + pad_bottom)


def overlaps(r, z):
    return r[0] < z[2] and r[2] > z[0] and r[1] < z[3] and r[3] > z[1]


def violations(rect, g):
    """Which hidden zones a [x0, y0, x1, y1] rect touches, plus 'outside-safe'."""
    out = [k for k, z in g["hidden"].items() if overlaps(rect, z)]
    sx0, sy0, sx1, sy1 = g["safe"]
    if rect[0] < sx0 - 1 or rect[1] < sy0 - 1 or rect[2] > sx1 + 1 or rect[3] > sy1 + 1:
        out.append("outside-safe")
    return out


# ---------------------------------------------------------------- overlay
def _filters(g, k=1.0):
    W, H = g["width"], g["height"]
    f = []
    for z in g["hidden"].values():
        f.append(f"drawbox=x={z[0]}:y={z[1]}:w={z[2] - z[0]}:h={z[3] - z[1]}:"
                 f"color=red@0.35:t=fill")
    x0, y0, x1, y1 = g["safe"]
    t = max(2, round(4 * W / BASE_W))
    f.append(f"drawbox=x={x0}:y={y0}:w={x1 - x0}:h={y1 - y0}:color=lime@0.9:t={t}")
    a, b = g["caption_band"]
    f.append(f"drawbox=x={x0}:y={a}:w={x1 - x0}:h={b - a}:color=yellow@0.8:t={t}")
    cx = g["center_x"]
    f.append(f"drawbox=x={cx - 1}:y={y0}:w=2:h={y1 - y0}:color=lime@0.6:t=fill")
    c = g["cover"]
    f.append(f"drawbox=x={c[0]}:y={c[1]}:w={c[2] - c[0]}:h={c[3] - c[1]}:color=cyan@0.7:t=2")
    return ",".join(f)


def overlay(src, times, out, g, thumb_w=270):
    """Draw the zones over stills (or frames of a video) and hstack them into one PNG."""
    tmp = tempfile.mkdtemp(prefix="grid_")
    stills = []
    is_img = src.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    for i, t in enumerate([None] if is_img else times):
        p = os.path.join(tmp, f"g{i:03d}.png")
        cmd = ["ffmpeg", "-v", "error", "-y"]
        if t is not None:
            cmd += ["-ss", f"{t:.3f}"]
        cmd += ["-i", src, "-frames:v", "1", "-vf",
                f"scale={g['width']}:{g['height']},{_filters(g)},scale={thumb_w}:-2", p]
        r = hfcfg.run(cmd)
        if r.returncode or not os.path.exists(p):
            sys.exit(f"could not grab a frame at {t}s from {src}\n{r.stderr[-400:]}")
        stills.append(p)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    if len(stills) == 1:
        hfcfg.run(["ffmpeg", "-v", "error", "-y", "-i", stills[0], out])
    else:
        ins = []
        for s in stills:
            ins += ["-i", s]
        lab = "".join(f"[{i}]" for i in range(len(stills)))
        hfcfg.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex",
                   f"{lab}hstack={len(stills)}", out])
    print(f"  {out}  ({len(stills)} frame(s); red = hidden by the app, green = safe zone, "
          f"yellow = caption band, cyan = 3:4 profile crop)")


# ------------------------------------------------------------------ check
PROBE_JS = r"""
<script>
(function () {
  const G = __GRID__, TIMES = __TIMES__;
  function inWindow(el, t) {
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      const s = n.getAttribute && n.getAttribute('data-start');
      const d = n.getAttribute && n.getAttribute('data-duration');
      if (s !== null && d !== null && n.id !== 'root' && !n.hasAttribute('data-composition-id')) {
        const a = parseFloat(s), b = a + parseFloat(d);
        if (t < a - 1e-6 || t >= b - 1e-6) return false;
      }
    }
    return true;
  }
  function alpha(el) {
    let a = 1;
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.display === 'none' || cs.visibility === 'hidden') return 0;
      a *= parseFloat(cs.opacity || '1');
    }
    return a;
  }
  function readable(el) {
    if (el.closest('[data-grid="bleed"]')) return false;
    const tag = el.tagName.toLowerCase();
    if (tag === 'img' || tag === 'svg') return true;
    if (['video', 'audio', 'script', 'style'].includes(tag)) return false;
    for (const c of el.childNodes) if (c.nodeType === 3 && c.textContent.trim()) return true;
    return false;
  }
  function run() {
    const tl = (window.__timelines || {}).main;
    const out = [];
    const W = G.width, H = G.height;
    for (const t of TIMES) {
      if (tl) { try { tl.seek(t, false); } catch (e) {} }
      for (const el of document.querySelectorAll('#root *')) {
        if (!readable(el) || !inWindow(el, t) || alpha(el) < 0.05) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) continue;
        if (r.width >= W * 0.95 && r.height >= H * 0.95) continue;   // full-frame plate
        out.push({ t, id: el.id || '', tag: el.tagName.toLowerCase(),
                   cls: (el.getAttribute('class') || '').slice(0, 40),
                   text: (el.textContent || '').trim().slice(0, 30),
                   r: [r.left, r.top, r.right, r.bottom].map(Math.round) });
      }
    }
    document.getElementById('__gridcheck').textContent = JSON.stringify(out);
  }
  window.addEventListener('load', () => setTimeout(run, 300));
})();
</script>
<div id="__gridcheck"></div>
"""


def auto_times(html, end_cap=None):
    """A probe just after every non-media element enters, plus its midpoint."""
    ts = set()
    for m in re.finditer(r'<(div|img|svg|span|section)\b[^>]*data-start="([\d.]+)"'
                         r'[^>]*data-duration="([\d.]+)"', html):
        a, d = float(m.group(2)), float(m.group(3))
        if d <= 0:
            continue
        ts.add(round(a + min(0.6, d / 2), 2))
        ts.add(round(a + d / 2, 2))
    if end_cap:
        ts = {t for t in ts if t < end_cap}
    return sorted(ts)[:400]


def check(html_path, g, times=None, slots=None, plate_h=None, plate_w=None):
    html = open(html_path, encoding="utf-8").read()
    if not times:
        times = auto_times(html)
    if not times:
        print("  (no timed elements found — nothing to check)")
    probe = PROBE_JS.replace("__GRID__", json.dumps(g)).replace("__TIMES__", json.dumps(times))
    page = html.replace("</body>", probe + "\n</body>") if "</body>" in html else html + probe
    tmp = os.path.join(os.path.dirname(os.path.abspath(html_path)), "_gridcheck.html")
    open(tmp, "w", encoding="utf-8").write(page)
    issues = []
    try:
        r = subprocess.run([hfcfg.chrome_path(), "--headless", "--disable-gpu", "--no-sandbox",
                            "--allow-file-access-from-files", "--autoplay-policy=no-user-gesture-required",
                            f"--window-size={g['width']},{g['height']}",
                            "--virtual-time-budget=6000", "--dump-dom", "file://" + tmp],
                           capture_output=True, text=True, timeout=180)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    m = re.search(r'id="__gridcheck">(.*?)</div>', r.stdout, re.S)
    if not m or not m.group(1).strip():
        sys.exit(f"grid check: Chrome returned no measurement\n{r.stderr[-600:]}")
    import html as _h
    rows = json.loads(_h.unescape(m.group(1)))
    seen = set()
    for row in rows:
        v = violations(row["r"], g)
        if not v:
            continue
        key = (row["id"], row["tag"], row["text"], tuple(v))
        if key in seen:
            continue
        seen.add(key)
        who = row["id"] and f"#{row['id']}" or f"<{row['tag']} class={row['cls']!r}>"
        issues.append(f"{row['t']:6.2f}s  {who} {row['text']!r}  rect {row['r']}  → {', '.join(v)}")

    # captions live in an external alpha layer, so check the slot geometry directly
    if slots and plate_h:
        w = plate_w or g["safe_width"]
        cx = g["center_x"]
        for name, top in slots.items():
            rect = [cx - w / 2, top, cx + w / 2, top + plate_h]
            v = violations(rect, g)
            if v:
                issues.append(f"caption slot {name!r} (top {top}, plate {plate_h}px) → {', '.join(v)}")
    return issues, len(rows), len(times)


def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("cmd", choices=["show", "overlay", "check"])
    ap.add_argument("src", nargs="?")
    ap.add_argument("--profile")
    ap.add_argument("--at", help="comma-separated times in seconds")
    ap.add_argument("-o", "--out", default="build/grid.png")
    a = ap.parse_args()
    cfg = hfcfg.load(a.config)
    if a.profile:
        cfg.setdefault("grid", {})["profile"] = a.profile
    g = from_config(cfg)
    times = [float(x) for x in a.at.split(",")] if a.at else None

    if a.cmd == "show":
        print(json.dumps(g, indent=1))
        return 0
    if not a.src:
        sys.exit("give a file: an image/video for overlay, index.html for check")
    if a.cmd == "overlay":
        hfcfg.require("ffmpeg")
        overlay(a.src, times or [0.5], a.out, g)
        return 0

    beatmap, _ = hfcfg.load_beats()
    slots = getattr(beatmap, "SLOT", None) if beatmap else None
    b = cfg["brand"]
    ph = plate_height(b["caption_size"])
    issues, n, nt = check(a.src, g, times, slots, ph, g["safe_width"])
    print(f"== GRID CHECK ({g['name']}) — {n} visible elements over {nt} probe times ==")
    if issues:
        for i in issues:
            print(f"  ✗ {i}")
        x0, y0, x1, y1 = g["safe"]
        print(f"\n  move it inside the green safe zone (x {x0}-{x1}, y {y0}-{y1}), or mark pure "
              f"decoration data-grid=\"bleed\"")
        return 1
    print("  ✓ nothing readable sits under the Reels UI")
    return 0


if __name__ == "__main__":
    sys.exit(main())
