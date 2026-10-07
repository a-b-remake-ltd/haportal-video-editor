#!/usr/bin/env python3
"""The Instagram Reels grid — where things may sit so the platform UI never covers them.

ONE module owns these numbers. The caption slots, the caption-width limit, the outro
layout and the grid gate all read them from here, so a change lands everywhere at once.

Profile "reels" (the default) was measured on real Reels and approved by the editor:

    hidden   top      y 0-220            reel title / status bar
             bottom   y 1520-1920        username, caption text, audio line
             rail     x 940-1080 at y 880-1520   like / comment / share column
    safe     x 60-940, y 220-1520        every readable thing and every logo lives here
    centre   x 540, the FRAME centre     see centered_box(): an element up to 800 px wide
                                         (right edge <= 940) sits on x 540; only a wider
                                         one shifts left, just enough to clear the rail
    captions band y 1110-1190, centred on x 540 (plate at most 800 px wide)
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
  python3 scripts/grid.py selftest            # the centring rule + gates, negative tests

`check` is the gate: it loads the composition in headless Chrome, seeks the timeline to
each time, and fails on any visible text, image or logo that crosses into a hidden zone,
on any element tagged data-center that is never where centered_box() puts it (±4 px),
and on any sky widget (data-sky) that reaches below the measured head top − 20.
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
        # the FRAME centre, not the safe zone's (500): next to a centred speaker a block
        # centred on 500 reads as off-centre. centered_box() keeps it clear of the rail.
        "center_x": 540,
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
    # the widest element that can still sit exactly on the centre (800 on Reels): twice
    # the distance from the centre to the nearer safe edge
    g["max_centered_w"] = 2 * min(g["center_x"] - x0, x1 - g["center_x"])
    return g


def centered_box(width, y0=None, y1=None, g=None):
    """(x0, x1) of an element `width` px wide — THE centring rule, used by every layout.

    WHY: everything used to centre on the safe zone's middle (x 500), because the like /
    comment rail covers x >= 940. Next to a speaker framed on the frame centre that reads
    as off-centre — the editor's note after a real test reel was "captions aren't centred,
    many elements aren't centred". So:
      * an element that fits (width <= max_centered_w, 800 on Reels: its right edge stays
        <= 940) sits on the FRAME centre, x 540
      * only a wider element shifts LEFT, just enough to keep its right edge on the safe
        edge (940), and never past the left safe edge (60): it is then clipped to the
        safe width
    y0/y1 (optional) narrow the check to the rows the element occupies: a hidden zone that
    does not reach those rows does not push it. On Reels the safe zone's right edge is 940
    at every height, so above the rail (y < 880) the same rule holds — one rule, so a
    widget does not jump sideways when it moves down the frame.
    """
    g = g or profile()
    sx0, sy0, sx1, sy1 = g["safe"]
    right = sx1
    for z in g["hidden"].values():
        if z[0] <= g["center_x"]:
            continue                     # a left-hand zone is the safe edge's business
        if y0 is not None and y1 is not None and not (y0 < z[3] and y1 > z[1]):
            continue
        right = min(right, z[0])
    w = min(float(width), right - sx0)
    x0 = g["center_x"] - w / 2.0
    if x0 + w > right:
        x0 = right - w
    x0 = max(sx0, x0)
    return round(x0, 2), round(x0 + w, 2)


def center_lane(g=None):
    """(left, width) of the widest box that is still centred on the frame: x 140-940 on
    Reels. Containers whose content flows (a caption, a chip row, a headline, a big title)
    use it, so their centred content lands on x 540 and wraps or is fitted inside 800 px."""
    g = g or profile()
    x0, x1 = centered_box(g["max_centered_w"], g=g)
    return round(x0), round(x1 - x0)


def centring_error(rect, g=None, y0=None, y1=None):
    """How far a measured [x0, y0, x1, y1] rect is from where centered_box() puts an
    element of its width (px, signed: + = too far right). The gate's one formula."""
    g = g or profile()
    a, b = centered_box(rect[2] - rect[0], rect[1] if y0 is None else y0,
                        rect[3] if y1 is None else y1, g)
    return round(((rect[0] + rect[2]) - (a + b)) / 2.0, 1)


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
    cx = g["center_x"]              # the frame centre: what every centred element sits on
    f.append(f"drawbox=x={cx - 1}:y={y0}:w=2:h={y1 - y0}:color=lime@0.6:t=fill")
    lx, lw = center_lane(g)         # the 800 px centred lane (x 140-940 on Reels)
    f.append(f"drawbox=x={lx}:y={y0}:w={lw}:h={y1 - y0}:color=lime@0.35:t=2")
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
  function box(el) {
    // data-center="content": the union of the children (a centred row of chips / words);
    // anything else: the element itself (a widget, a card)
    if (el.getAttribute('data-center') !== 'content') return el.getBoundingClientRect();
    let a = null;
    for (const c of el.children) {
      const r = c.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) continue;
      a = a ? { left: Math.min(a.left, r.left), top: Math.min(a.top, r.top),
                right: Math.max(a.right, r.right), bottom: Math.max(a.bottom, r.bottom) } :
              { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
    }
    return a;
  }
  function run() {
    const tl = (window.__timelines || {}).main;
    const out = [], cen = [], sky = [];
    const W = G.width, H = G.height;
    for (const t of TIMES) {
      if (tl) { try { tl.seek(t, false); } catch (e) {} }
      for (const el of document.querySelectorAll('#root *')) {
        const tagged = el.hasAttribute('data-center') || el.hasAttribute('data-sky');
        if (tagged && inWindow(el, t) && alpha(el) >= 0.05) {
          const r = box(el);
          if (r && r.right - r.left >= 2) {
            const row = { t, id: el.id || (el.getAttribute('class') || '').slice(0, 30),
                          r: [r.left, r.top, r.right, r.bottom].map(Math.round) };
            if (el.hasAttribute('data-center')) cen.push(row);
            if (el.hasAttribute('data-sky')) sky.push(row);
          }
        }
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
    document.getElementById('__gridcheck').textContent = JSON.stringify({ out, cen, sky });
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
    data = json.loads(_h.unescape(m.group(1)))
    rows = data["out"] if isinstance(data, dict) else data
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

    if isinstance(data, dict):
        issues += centring_issues(data.get("cen") or [], g)
        head = framing_head_top(html_path)
        issues += sky_issues(data.get("sky") or [], g, head)
        LAST.update(centred=len({r["id"] for r in data.get("cen") or []}),
                    sky=len({r["id"] for r in data.get("sky") or []}), head_top=head)

    # captions live in an external alpha layer, so check the slot geometry directly; the
    # widest plate is placed by the same centring rule as everything else
    if slots and plate_h:
        w = plate_w or g["max_centered_w"]
        for name, top in slots.items():
            x0, x1 = centered_box(w, top, top + plate_h, g)
            rect = [x0, top, x1, top + plate_h]
            v = violations(rect, g)
            if v:
                issues.append(f"caption slot {name!r} (top {top}, plate {plate_h}px) → {', '.join(v)}")
    return issues, len(rows), len(times)


CENTRE_TOL = 4      # px: the editor measures left margin == right margin by eye at ~this
LAST = {}           # what the last check() measured beyond the text rows (for the summary)


def centring_issues(rows, g, tol=CENTRE_TOL):
    """The centring gate. Every element tagged data-center (the kit's widgets and hook
    cards, the caption-free centred rows: chips, pills, big titles, stamps, cards) must sit
    where centered_box() puts an element of its measured width, at SOME probe time: a
    widget mid-slide or mid-shake is legitimately off, a widget that is never on its mark
    is a layout bug. Returns issue strings."""
    best = {}
    for row in rows:
        err = centring_error(row["r"], g)
        k = row["id"]
        if k not in best or abs(err) < abs(best[k][0]):
            best[k] = (err, row)
    out = []
    for k, (err, row) in sorted(best.items()):
        if abs(err) > tol:
            x0, _, x1, _ = row["r"]
            out.append(f"{row['t']:6.2f}s  #{k} not centred: off by {err:+.0f}px (left margin "
                       f"{x0}px, right margin {g['width'] - x1}px; rule: centred on "
                       f"x {g['center_x']} up to {g['max_centered_w']}px wide)")
    return out


def framing_head_top(html_path):
    """The measured head top from build/framing.json next to the composition, else None
    (the default framing is a guess, so the head gate only runs on a measured one)."""
    p = os.path.join(os.path.dirname(os.path.abspath(html_path)), "build", "framing.json")
    try:
        v = json.load(open(p, encoding="utf-8")).get("head_top")
        return float(v) if v is not None else None
    except (OSError, ValueError):
        return None


def sky_issues(rows, g, head_top, air=20, tol=2):
    """A sky widget (data-sky) must end above the head: bottom <= head_top - air. A
    calendar 500 px tall once covered the top of the head on a normal avatar framing."""
    if head_top is None:
        return []
    lim = head_top - air
    worst = {}
    for row in rows:
        if row["r"][3] > lim + tol and (row["id"] not in worst or row["r"][3] > worst[row["id"]]["r"][3]):
            worst[row["id"]] = row
    return [f"{row['t']:6.2f}s  #{k} sky widget reaches y {row['r'][3]}, over the head "
            f"(head top {head_top:.0f} − {air}px air = {lim:.0f})" for k, row in sorted(worst.items())]


def selftest():
    """Positive + negative tests of the centring rule and its gates — `grid.py selftest`.
    No Chrome, no video. Exit 1 on a failure."""
    g = profile()
    fails, n = [], [0]

    def want(name, got, exp):
        n[0] += 1
        if got != exp:
            fails.append(f"{name}: got {got!r}, want {exp!r}")
    want("800 fits → centred on 540", centered_box(800, g=g), (140.0, 940.0))
    want("300 → centred on 540", centered_box(300, g=g), (390.0, 690.0))
    want("820 → right edge 940", centered_box(820, g=g), (120.0, 940.0))
    want("880 → the safe zone", centered_box(880, g=g), (60, 940.0))
    want("too wide → clipped to the safe zone", centered_box(2000, g=g), (60, 940))
    want("above the rail, same rule", centered_box(820, 230, 600, g), (120.0, 940.0))
    want("lane", center_lane(g), (140, 800))
    a = profile("ads")
    want("ads: symmetric safe zone → 540", centered_box(600, g=a), (240.0, 840.0))
    want("4K doubles", centered_box(1600, g=profile("reels", 2160, 3840)), (280.0, 1880.0))
    # the gate: a widget on the OLD safe-zone centre (left 90, right 170) must fail …
    old = [{"t": 1.0, "id": "w", "r": [90, 250, 910, 560]}]
    want("old widget flagged", len(centring_issues(old, g)), 1)
    # … a centred one passes, and one centred at SOME probe (mid-slide earlier) passes
    ok = [{"t": 1.0, "id": "w", "r": [400, 250, 1200, 560]},
          {"t": 1.6, "id": "w", "r": [140, 250, 940, 560]}]
    want("centred widget passes", centring_issues(ok, g), [])
    wide = [{"t": 1.0, "id": "c", "r": [60, 1300, 940, 1500]}]
    want("880 card on the safe edge passes", centring_issues(wide, g), [])
    # the sky gate: a widget ending at 750 on a head at 610 fails; at 560 passes
    want("tall sky widget flagged", len(sky_issues([{"t": 1, "id": "cal", "r": [140, 250, 940, 750]}],
                                                g, 610)), 1)
    want("short sky widget passes", sky_issues([{"t": 1, "id": "cal", "r": [140, 250, 940, 588]}],
                                               g, 610), [])
    want("no framing → no head gate", sky_issues([{"t": 1, "id": "x", "r": [0, 0, 1, 1900]}], g, None), [])
    for f in fails:
        print(f"  ✗ {f}")
    print(f"  grid selftest: {'FAIL' if fails else 'ok'} ({n[0] - len(fails)}/{n[0]})")
    return 1 if fails else 0


def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("cmd", choices=["show", "overlay", "check", "selftest"])
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

    if a.cmd == "selftest":
        return selftest()
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
    issues, n, nt = check(a.src, g, times, slots, ph, g["max_centered_w"])
    print(f"== GRID CHECK ({g['name']}) — {n} visible elements over {nt} probe times ==")
    if issues:
        for i in issues:
            print(f"  ✗ {i}")
        x0, y0, x1, y1 = g["safe"]
        print(f"\n  move it inside the green safe zone (x {x0}-{x1}, y {y0}-{y1}), or mark pure "
              f"decoration data-grid=\"bleed\"; centre it with grid.centered_box (x "
              f"{g['center_x']} up to {g['max_centered_w']}px); end a sky widget above the head")
        return 1
    print("  ✓ nothing readable sits under the Reels UI")
    if LAST:
        hd = (f"head top {LAST['head_top']:.0f}" if LAST.get("head_top") is not None
              else "no measured framing: head gate off")
        print(f"  ✓ {LAST.get('centred', 0)} centred element(s) on x {g['center_x']} (±{CENTRE_TOL}px), "
              f"{LAST.get('sky', 0)} sky widget(s) clear of the head ({hd})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
