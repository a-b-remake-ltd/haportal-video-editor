#!/usr/bin/env python3
"""The widget kit: literal UI "designed moments", authored per video on ONE visual language.

WHY THIS EXISTS. The edits this skill is measured against get their quality from 8-12
LITERAL UI moments invented from each video's own lines: an inbox that never fills while the
speaker says nobody gives you a chance, a calendar event that keeps being postponed, an
approval dialog where a hand cursor taps "decide", prison bars that slam on "the prison we
built" and burst on "that is why you are free". A fixed menu of moment types
(scripts/moments.py) cannot produce that, because every video's lines are different. So this
is not a menu, it is a KIT: colour tokens, a seek-safe motion vocabulary, widget shells,
UI components, full-frame overlays and the hook world. With it, a new literal widget is a few
lines of Python in the PROJECT's scenes.py, and it comes out premium by default (glass at .84
opacity, Heebo weights, one family of eases, everything inside the Reels grid).

This module never decides WHAT to show. references/storyboard.md is the method for that; the
project's scenes.py is where it happens; scripts/scenes.py loads it and hands the fragments to
build_index.py. The kit only makes whatever was decided look and move right.

The shape of the API
  s = ctx.scene("wr", ctx.t("לחכות", 3), ctx.te("לחכות", 4))   # a Scene: one timed layer
  s.add(kit.widget(s, kit.waiting_room(s, ...), name="w"))      # components return Html
  s.sfx("soft_whoosh", s.start)                                  # placed + levelled later
  return [s.done()]                                              # a fragment dict

  Fragment: {id, start, end, html, css, js[list], sfx[{name,t,kind,base_vol}],
             hide_captions (bool | [[a,b]]), footage (bool | [[a,b]]), punch_ok, z, cls}

Seek-safety by construction (one paused timeline, scrubbed in any order by the renderer):
  * every tween is fromTo(..., immediateRender:false) whose FROM equals what was on screen
  * hidden initial states come from CSS (opacity:0), never from an early tl.set
  * no CSS transform on anything GSAP moves (lint: gsap_css_transform_conflict)
  * no runtime randomness: positions are seeded here and written as literals
  * no tweens of left/top/letterSpacing, no :nth-child selectors (every element has an id)
  * state changes are stacked children switched with steps(), never textContent tweens

All geometry is authored for 1080x1920 (the spec's numbers); everything sits inside the Reels
grid of scripts/grid.py.
"""
from __future__ import annotations

import html as _html
import json
import math
import os
import random
import re
import string
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402
import grid  # noqa: E402

TEMPLATE_DIR = os.path.join(hfcfg.SKILL_DIR, "templates", "kit")
FOOT = "#aroll"          # the footage element (a <video> cannot be nested in a timed div,
                         # so camera moves animate the A-roll element itself, like moments.py)
REF_ORIGIN_Y = 864.0     # the spec's hook numbers assume the camera pivots on the face (y 864)


def r3(v):
    return round(float(v), 3)


class KitError(SystemExit):
    """A clear, actionable authoring error (exits the build with the message)."""


class Html(str):
    """Markup a component produced. Text passed to a component is escaped; Html is not."""


def html(s):
    """Mark a string as trusted markup (e.g. a hand-written <b> inside a title)."""
    return Html(s)


# ======================================================================= tokens
# The house palette (spec §4.1). It is the default when no logo was given; with a logo,
# tokens() derives the same roles from brand/brand.json so every widget follows the brand.
HOUSE = {
    "blue": "#2F9BFF",     # bold keyword
    "blue2": "#8CC8FF",    # light partner word
    "eblue": "#1E8BFF",    # electric blue: gradients, chips, primary buttons
    "lav": "#C9B8FF",      # gradient middle
    "pink": "#FF9ECF",     # gradient tail
    "green": "#30D158",    # done / approved / picked
    "red": "#FF453A",      # no / failed / strike / stamp
    "amber": "#F5A524",    # pending / waiting
    "violet": "#8B5CF6",   # calendar events, task badge
    "world_a": "#1E8BFF", "world_b": "#0B3E86", "world_c": "#061224", "world_d": "#03070F",
    "card": "#0D1526", "card_head": "#141C2E",
    # people colours for avatar rows (content, not brand: a crowd should look like a crowd)
    "p1": "#F5A524", "p2": "#10B981", "p3": "#8B5CF6", "p4": "#EC4899", "p5": "#14B8A6",
    # materials
    "wood": "#C79B66", "wood2": "#B0844F", "warm": "#FFD696",
}


def _rgb(h):
    h = str(h).strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def mix(a, b, t):
    """Blend hex a toward hex b by t (0..1)."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex([ra[i] + (rb[i] - ra[i]) * t for i in range(3)])


def _chroma(h):
    r = _rgb(h)
    return max(r) - min(r)


def tokens(brand=None):
    """The kit's colour roles. House palette by default; with brand/brand.json (written by
    brand_from_logo.py) the roles are DERIVED from the logo: keyword = hl_on_dark (already
    contrast-checked on dark), electric = primary, gradient tail from secondary/accent, the
    world from the brand's gradient pair. Semantic colours (green/red/amber) stay: a red
    strike-through must read as "no" in any brand. A grey or black logo cannot carry the
    electric role, so the house blue stays there (the brand note says so)."""
    t = dict(HOUSE)
    t["source"] = "house"
    c = (brand or {}).get("colors") or {}
    prim = c.get("primary")
    if prim:
        hl = c.get("hl_on_dark") or prim
        if _chroma(hl) < 40:
            hl = HOUSE["blue"]
        e = prim if _chroma(prim) >= 60 else hl
        sec = c.get("secondary") or e
        acc = c.get("accent") or sec
        t.update({
            "blue": hl, "blue2": mix(hl, "#FFFFFF", 0.45), "eblue": e,
            "lav": mix(sec if _chroma(sec) >= 40 else e, "#FFFFFF", 0.55),
            "pink": mix(acc if _chroma(acc) >= 40 else e, "#FFFFFF", 0.45),
            "violet": mix(e, "#8B5CF6", 0.5),
            "world_a": e,
            "world_b": c.get("grad_a") or mix(e, "#000000", 0.6),
            "world_c": c.get("grad_b") or mix(e, "#000000", 0.86),
            "card": mix(c.get("grad_b") or mix(e, "#000000", 0.86), "#0D1526", 0.6),
            "card_head": mix(c.get("grad_a") or mix(e, "#000000", 0.6), "#141C2E", 0.75),
            "source": "brand",
        })
    return t


def tokens_css(t):
    """:root block. --world/--card/--glass are composite tokens so a widget never needs a hex."""
    rgb_e = ", ".join(str(x) for x in _rgb(t["eblue"]))
    rgb_b2 = ", ".join(str(x) for x in _rgb(t["blue2"]))
    return (
        "      /* ---- KIT tokens (scripts/kit.py) — source: %s */\n"
        "      :root { --blue: %s; --blue2: %s; --eblue: %s; --lav: %s; --pink: %s;\n"
        "              --green: %s; --red: %s; --amber: %s; --violet: %s;\n"
        "              --eblue-rgb: %s; --blue2-rgb: %s;\n"
        "              --p1: %s; --p2: %s; --p3: %s; --p4: %s; --p5: %s;\n"
        "              --wood: %s; --wood2: %s; --warm: %s;\n"
        "              --glass: rgba(10,14,24,.84); --glass-edge: rgba(255,255,255,.16);\n"
        "              --card: %s; --card-head: %s; --card-edge: rgba(255,255,255,.12);\n"
        "              --grad: linear-gradient(90deg, var(--eblue), var(--lav) 60%%, var(--pink));\n"
        "              --world: radial-gradient(120%% 70%% at 50%% 110%%, %s 0%%, %s 38%%, %s 72%%, %s 100%%); }\n"
        % (t["source"], t["blue"], t["blue2"], t["eblue"], t["lav"], t["pink"], t["green"],
           t["red"], t["amber"], t["violet"], rgb_e, rgb_b2, t["p1"], t["p2"], t["p3"], t["p4"],
           t["p5"], t["wood"], t["wood2"], t["warm"], t["card"], t["card_head"],
           t["world_a"], t["world_b"], t["world_c"], t["world_d"]))


# ===================================================================== framing
# build/framing.json (written by the framing step): where the head, face, chin and chest sit.
# The defaults are a typical centred selfie/avatar framing.
FRAMING_DEFAULT = {"head_top": 620, "face_cx": 540, "face_cy": 860, "chin": 1120,
                   "chest": [1200, 1520], "free_zones": [[90, 230, 910, 600]]}


def load_framing(root="."):
    fr = dict(FRAMING_DEFAULT)
    p = os.path.join(root, "build", "framing.json")
    src = "default"
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
            for k in FRAMING_DEFAULT:
                if d.get(k) is not None:
                    fr[k] = d[k]
            src = p
        except (OSError, ValueError) as e:
            print(f"  kit: ! {p} unreadable ({e}) — using default framing")
    fr["source"] = src
    return fr


# ======================================================================= text
_LATIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+#%/:'’\-]*")
# A dash used as punctuation. NOT: a number range (10-20), or the hyphen that joins a Hebrew
# prefix letter to a Latin word or a number ("ה-AI", "ב-2026", "מ-100"): that is spelling.
_DASH = re.compile(r"(?<![\d\u05d0-\u05ea])[\-–—](?!\d)|(?<=\d)[–—](?=\d)|[–—]")


def text(s, ctx=None):
    """Escape on-screen text; isolate Latin/number runs (bidi would reorder a Hebrew line) and
    slab any acronym containing an I ("AI" in a heavy Hebrew face reads "Al"). Html passes
    through untouched."""
    if s is None:
        return ""
    if isinstance(s, Html):
        return s
    s = str(s)
    if ctx is not None and _DASH.search(s):
        ctx.note(f"on-screen text {s!r} contains a dash — the house rule is no dashes in "
                 f"on-screen copy (number ranges excepted)")
    out, i = [], 0
    for m in _LATIN.finditer(s):
        out.append(_html.escape(s[i:m.start()]))
        tok = m.group(0)
        core = re.sub(r"[^A-Za-z]", "", tok)
        cls = "kt-ltr"
        if core and core.isupper() and "I" in core and len(core) <= 5:
            cls += " kt-ai"
        out.append(f'<bdi class="{cls}">{_html.escape(tok)}</bdi>')
        i = m.end()
    out.append(_html.escape(s[i:]))
    return Html("".join(out))


def _jv(v):
    """Python value → JS literal (numbers rounded to ms, dict keys bare)."""
    if isinstance(v, Raw):
        return v.s
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        f = round(float(v), 3)
        return str(int(f)) if f == int(f) else repr(f)
    if isinstance(v, dict):
        return "{ " + ", ".join(
            (k if re.match(r"^[A-Za-z_$][\w$]*$", k) else json.dumps(k)) + ": " + _jv(x)
            for k, x in v.items()) + " }"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_jv(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


class Raw:
    def __init__(self, s):
        self.s = s


# ====================================================================== icons
# A small in-house stroke icon set on a 24x24 grid (drawn for this kit, no icon pack).
ICONS = {
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4.5 4.4-7 8-7s7 2.5 8 7"/>',
    "users": '<circle cx="9" cy="8.5" r="3.5"/><path d="M2.5 20c.8-3.8 3.4-6 6.5-6s5.7 2.2 6.5 6"/>'
             '<path d="M15.5 5.2a3.5 3.5 0 0 1 0 6.6M18 14.4c1.9.9 3.1 2.8 3.5 5.6"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "x": '<path d="M6 6l12 12M18 6 6 18"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "hourglass": '<path d="M7 3h10M7 21h10M8 3c0 5 8 5 8 9s-8 4-8 9M16 3c0 5-8 5-8 9s8 4 8 9"/>',
    "inbox": '<path d="M3 13h5l1.5 3h5L16 13h5"/><path d="M5.5 5h13L21 13v6H3v-6z"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.4-5.7M20 4v4.5h-4.5"/>',
    "ladder": '<path d="M7 3v18M17 3v18M7 7h10M7 12h10M7 17h10"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "bell": '<path d="M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "unlock": '<rect x="5" y="11" width="14" height="10" rx="2.5"/><path d="M8 11V8a4 4 0 0 1 7.6-1.8"/>',
    "phone": '<rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M11 18.5h2"/>',
    "cursor": '<path d="M5 3.5 19 10l-6.2 1.9L10.5 18z"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "back": '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    "up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "spark": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>',
    "chat": '<path d="M4 5h16v11H9l-5 4z"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="m4 7 8 6 8-6"/>',
    "send": '<path d="M21 3 3 10.5l7 2.5 2.5 7z"/><path d="m10 13 4.5-4.5"/>',
    "cart": '<path d="M3 4h2.5l2.2 11h10.6L20.5 8H7"/><circle cx="9" cy="19.5" r="1.4"/><circle cx="17" cy="19.5" r="1.4"/>',
    "play": '<path d="M8 5v14l11-7z"/>',
    "pause": '<path d="M8 5v14M16 5v14"/>',
    "star": '<path d="m12 3 2.8 5.8 6.2.9-4.5 4.4 1.1 6.2L12 17.4l-5.6 2.9 1.1-6.2L3 9.7l6.2-.9z"/>',
    "heart": '<path d="M12 20s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7.4 4.3 4.3 0 0 1 19.5 10c0 5.4-7.5 10-7.5 10z"/>',
    "trophy": '<path d="M8 4h8v5a4 4 0 0 1-8 0z"/><path d="M8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4M12 13v4M8 20h8M10 17h4"/>',
    "fire": '<path d="M12 21c-3.9 0-6.5-2.6-6.5-6.1 0-3.4 2.6-5.4 3.6-8.4 1.8 1.6 2.4 3.3 2.4 4.9 1-1 1.7-2.3 1.8-3.9 2.8 2.1 5.2 4.6 5.2 7.6 0 3.4-2.6 5.9-6.5 5.9z"/>',
    "rocket": '<path d="M12 3c3.5 2 5.5 5.6 5.5 9.5L15 16H9l-2.5-3.5C6.5 8.6 8.5 5 12 3z"/><circle cx="12" cy="10" r="1.8"/><path d="m9 16-1.5 4L12 18l4.5 2L15 16"/>',
    "brain": '<path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 3 3 2 2 0 0 0 2-2V6a2 2 0 0 0-2-2z"/>'
             '<path d="M15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-3 3 2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/>',
    "code": '<path d="m8 7-5 5 5 5M16 7l5 5-5 5M14 4l-4 16"/>',
    "robot": '<rect x="4.5" y="8" width="15" height="11" rx="3"/><path d="M12 4v4M9.5 13h.01M14.5 13h.01M9.5 16h5"/><circle cx="12" cy="3.5" r="1"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/>',
    "home": '<path d="M4 11 12 4l8 7v9h-5v-6H9v6H4z"/>',
    "chart": '<path d="M4 20V4M4 20h16"/><path d="m7 15 4-4 3 3 6-7"/>',
    "money": '<rect x="3" y="6.5" width="18" height="11" rx="2"/><circle cx="12" cy="12" r="2.6"/><path d="M6.5 9.5v5M17.5 9.5v5"/>',
    "eye": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="m11 12 8-8M16 7l2.5 2.5M14 9l2 2"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
    "flag": '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
    "doc": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
    "gift": '<rect x="4" y="9" width="16" height="12" rx="1.5"/><path d="M3 9h18M12 9v12M12 9c-1.5-3-5-4-5-1.5S11 9 12 9zM12 9c1.5-3 5-4 5-1.5S13 9 12 9z"/>',
    "door": '<path d="M6 21V4.5A1.5 1.5 0 0 1 7.5 3h9A1.5 1.5 0 0 1 18 4.5V21M3.5 21h17M14.5 12.5h.01"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
}

# The tap cursor (filled white, dark outline): drawn for this kit.
HAND = ('<path d="M9.5 13.4V4.8a1.6 1.6 0 0 1 3.2 0v4.8a1.5 1.5 0 0 1 3 0v.8a1.4 1.4 0 0 1 2.8 0V15'
        'c0 3.6-2.5 6.2-5.9 6.2-2.6 0-4.1-1.2-5.5-3.4l-2.3-3.6a1.45 1.45 0 0 1 2.4-1.6z"/>'
        '<path d="M12.7 9.6v2.6M15.7 10.4v2" fill="none"/>')


def icon(name, size=60, color="currentColor", sw=2.1, cls=""):
    """A stroke icon from ICONS. `color` may be a token (var(--blue)): it is applied through
    CSS `color`, since SVG presentation attributes cannot read var()."""
    p = ICONS.get(name)
    if p is None:
        raise KitError(f"kit: unknown icon {name!r} — have: {', '.join(sorted(ICONS))}")
    return Html(f'<svg class="kt-ico {cls}" width="{size}" height="{size}" viewBox="0 0 24 24" '
                f'fill="none" stroke="currentColor" stroke-width="{sw}" stroke-linecap="round" '
                f'stroke-linejoin="round" style="color:{color}">{p}</svg>')


def hand_svg(hid, size=92, cls="kt-hand"):
    return Html(f'<svg class="{cls}" id="{hid}" viewBox="0 0 24 24" width="{size}" height="{size}" '
                f'fill="#fff" stroke="#0B1020" stroke-width="1.2" stroke-linejoin="round">{HAND}</svg>')


# ===================================================================== JS core
# The motion vocabulary (spec §4.3), emitted ONCE. It closes over the one timeline `tl`.
# Each scene's lines run inside `{ const { word, pop, ... } = __KIT; ... }`, so these short
# names can never collide with another generator's globals.
PRELUDE = r"""      // ---- KIT motion vocabulary (scripts/kit.py) — every call is a seek-safe fromTo
      const __KIT = (function (tl) {
        const X = "expo.out", IN = "power3.in";
        const ft = (sel, a, b, t) => tl.fromTo(sel, a, Object.assign({}, b, { immediateRender: false }), t);
        const word = (sel, t) => ft(sel, { opacity: 0.18, filter: "blur(6px) grayscale(1)" }, { opacity: 1, filter: "blur(0px) grayscale(0)", duration: 0.22, ease: "power2.out" }, t);
        const pop = (sel, t, d = 0.5) => ft(sel, { scale: 0.5, opacity: 0, filter: "blur(10px)" }, { scale: 1, opacity: 1, filter: "blur(0px)", duration: d, ease: X }, t);
        const drop = (sel, t, d = 0.55) => ft(sel, { y: -60, opacity: 0, filter: "blur(14px)" }, { y: 0, opacity: 1, filter: "blur(0px)", duration: d, ease: X }, t);
        const away = (sel, t, d = 0.28) => ft(sel, { y: 0, opacity: 1, filter: "blur(0px)" }, { y: -40, opacity: 0, filter: "blur(12px)", duration: d, ease: IN }, t);
        const fade = (sel, t, d = 0.3, a = 0, b = 1) => ft(sel, { opacity: a }, { opacity: b, duration: d, ease: "power1.out" }, t);
        const slide = (sel, t, dx = 300, d = 0.4) => ft(sel, { x: dx, opacity: 0, filter: "blur(14px)" }, { x: 0, opacity: 1, filter: "blur(0px)", duration: d, ease: X }, t);
        const rise = (sel, t, dy = 260, s = 0.86, b = 24, d = 0.45) => ft(sel, { y: dy, scale: s, opacity: 0, filter: "blur(" + b + "px)" }, { y: 0, scale: 1, opacity: 1, filter: "blur(0px)", duration: d, ease: X }, t);
        const swing = (sel, t, dx = 600, d = 0.45) => ft(sel, { x: dx, rotationY: -18, opacity: 0, filter: "blur(20px)" }, { x: 0, rotationY: 0, opacity: 1, filter: "blur(0px)", duration: d, ease: X }, t);
        const steps = (sel, list) => { const kids = [...document.querySelectorAll(sel + " > *")]; list.forEach(([t, k]) => kids.forEach((el, i) => tl.set(el, { opacity: i === k ? 1 : 0 }, t))); };
        const shake = (sel, t, amp = 14, axis = "y", base = 0) => ft(sel, { [axis]: base }, { [axis]: base + amp, duration: 0.06, yoyo: true, repeat: 3, ease: "none" }, t);
        const spin = (sel, t0, t1, dps = 420) => ft(sel, { rotation: 0 }, { rotation: Math.round(dps * (t1 - t0)), duration: t1 - t0, ease: "none" }, t0);
        const strike = (bar, pill, t) => { ft(bar, { scaleX: 0, opacity: 1 }, { scaleX: 1, opacity: 1, duration: 0.22, ease: "power3.out" }, t); ft(pill, { opacity: 1 }, { opacity: 0.45, duration: 0.2, ease: "power1.out" }, t + 0.22); };
        const stamp = (sel, t, rot = -6) => ft(sel, { opacity: 0, scale: 2.3, rotation: rot - 10 }, { opacity: 1, scale: 1, rotation: rot, duration: 0.18, ease: "power4.in" }, t);
        const draw = (sel, t, d = 0.5) => ft(sel, { strokeDashoffset: 100 }, { strokeDashoffset: 0, duration: d, ease: "power2.inOut" }, t);
        const push = (sel, t, d = 0.6, a = 1, b = 1.08) => ft(sel, { scale: a }, { scale: b, duration: d, ease: "power2.out" }, t);
        const drift = (sel, t0, t1, a = 1, b = 1.04) => ft(sel, { scale: a }, { scale: b, duration: Math.max(0.05, t1 - t0), ease: "none" }, t0);
        const float = (sel, t0, t1, dy = -18) => ft(sel, { y: 0 }, { y: dy, duration: Math.max(0.05, t1 - t0), ease: "sine.inOut" }, t0);
        const pulse = (sel, t, s = 1.15, d = 0.18) => ft(sel, { scale: 1 }, { scale: s, duration: d, yoyo: true, repeat: 1, ease: "power2.out" }, t);
        const tap = (sel, t) => ft(sel, { scale: 1 }, { scale: 0.86, duration: 0.1, yoyo: true, repeat: 1, ease: "power2.out" }, t);
        return { X, IN, ft, word, pop, drop, away, fade, slide, rise, swing, steps, shake, spin, strike, stamp, draw, push, drift, float, pulse, tap };
      })(tl);"""
VOCAB = ("X, IN, ft, word, pop, drop, away, fade, slide, rise, swing, steps, shake, spin, "
         "strike, stamp, draw, push, drift, float, pulse, tap")


# ======================================================================= Scene
class Scene:
    """One timed layer of the composition (one clip): its markup, CSS, timeline lines, SFX
    and caption-hide windows. Ids are prefixed with the scene id, so classes never collide
    (spec §6: a particle class once collided with the tagline's and stacked its words)."""

    LAYERS = {"back": 10, "world": 10, "card": 13, "under": 18, "tint": 21, "front": 45,
              "top": 47}

    def __init__(self, ctx, sid, start, end, layer="front", z=None, punch_ok=False, cls=""):
        if not re.match(r"^[A-Za-z][A-Za-z0-9_-]*$", str(sid)):
            raise KitError(f"kit: scene id {sid!r} must be ASCII letters/digits/-/_ "
                           f"and start with a letter")
        ctx._claim(sid)
        self.ctx = ctx
        self.id = str(sid)
        self.start = r3(ctx.at(start))
        self.end = r3(ctx.at(end))
        if self.end <= self.start:
            raise KitError(f"kit: scene {sid}: end {self.end} <= start {self.start}")
        if layer not in self.LAYERS:
            raise KitError(f"kit: scene {sid}: layer {layer!r} not in {sorted(self.LAYERS)}")
        self.z = self.LAYERS[layer] if z is None else int(z)
        self.punch_ok = punch_ok
        self.cls = cls
        self.parts, self.lines, self._sfx, self._hide, self._foot, self._css = [], [], [], [], [], []
        self._n = {}

    # ---- ids
    def uid(self, name):
        return f"{self.id}-{name}"

    def n(self, prefix):
        """A fresh unique id inside this scene (sp0, sp1, ...)."""
        k = self._n.get(prefix, 0)
        self._n[prefix] = k + 1
        return self.uid(f"{prefix}{k}")

    def q(self, sel):
        """A local name ("w") → "#<scene>-w"; anything starting with #, . or [ is literal."""
        sel = str(sel)
        if sel[:1] in "#.[" or " " in sel or "," in sel:
            return sel
        return "#" + self.uid(sel)

    def at(self, t):
        return r3(self.ctx.at(t))

    # ---- content
    def add(self, *parts):
        for p in parts:
            self.parts.append(str(p))
        return self

    def css(self, s):
        self._css.append(s)
        return self

    def js(self, line):
        self.lines.append(line)
        return self

    def _c(self, fn, *args):
        self.lines.append(f"{fn}({', '.join(_jv(a) for a in args)});")

    # ---- motion vocabulary (each a seek-safe fromTo; see PRELUDE)
    def word(self, sel, t):
        self._c("word", self.q(sel), self.at(t))

    def pop(self, sel, t, d=0.5):
        self._c("pop", self.q(sel), self.at(t), d)

    def drop(self, sel, t, d=0.55):
        self._c("drop", self.q(sel), self.at(t), d)

    def away(self, sel, t, d=0.28):
        self._c("away", self.q(sel), self.at(t), d)

    def fade(self, sel, t, d=0.3, a=0, b=1):
        self._c("fade", self.q(sel), self.at(t), d, a, b)

    def slide(self, sel, t, dx=300, d=0.4):
        self._c("slide", self.q(sel), self.at(t), dx, d)

    def rise(self, sel, t, dy=260, s=0.86, blur=24, d=0.45):
        self._c("rise", self.q(sel), self.at(t), dy, s, blur, d)

    def swing(self, sel, t, dx=600, d=0.45):
        self._c("swing", self.q(sel), self.at(t), dx, d)

    def steps(self, sel, at):
        """[(t, k), ...]: show child k of `sel` from t on (all other children hidden)."""
        self._c("steps", self.q(sel), [[self.at(t), int(k)] for t, k in at])

    def shake(self, sel, t, amp=14, axis="y", base=0):
        self._c("shake", self.q(sel), self.at(t), amp, axis, base)

    def spin(self, sel, t0, t1, dps=420):
        self._c("spin", self.q(sel), self.at(t0), self.at(t1), dps)

    def strike(self, bar, pill, t):
        self._c("strike", self.q(bar), self.q(pill), self.at(t))

    def stamp(self, sel, t, rot=-6):
        self._c("stamp", self.q(sel), self.at(t), rot)

    def draw(self, sel, t, d=0.5):
        self._c("draw", self.q(sel), self.at(t), d)

    def push(self, sel, t, d=0.6, a=1, b=1.08):
        self._c("push", self.q(sel), self.at(t), d, a, b)

    def drift(self, sel, t0, t1=None, a=1, b=1.04):
        self._c("drift", self.q(sel), self.at(t0), self.at(self.end if t1 is None else t1), a, b)

    def float(self, sel, t0, t1=None, dy=-18):
        self._c("float", self.q(sel), self.at(t0), self.at(self.end if t1 is None else t1), dy)

    def pulse(self, sel, t, s=1.15, d=0.18):
        self._c("pulse", self.q(sel), self.at(t), s, d)

    def tap(self, sel, t):
        self._c("tap", self.q(sel), self.at(t))

    def set(self, sel, props, t):
        self.lines.append(f"tl.set({_jv(self.q(sel))}, {_jv(props)}, {_jv(self.at(t))});")

    def tween(self, sel, frm, to, t, d=0.3, ease="power2.out", **kw):
        """A generic fromTo (FROM must equal what is on screen at t)."""
        b = dict(to)
        b.update({"duration": d, "ease": ease})
        b.update(kw)
        self._c("ft", self.q(sel), frm, b, self.at(t))

    # ---- the camera (the A-roll element). Every move records a footage window so the build
    # can refuse two scenes fighting over the same transform, or a punch-in inside one.
    def _footage(self, a, b):
        self._foot.append([r3(a), r3(b)])

    def cam_shake(self, t, amp=14):
        """Impact shake (spec §5): y ±amp for 0.06 s, yoyo, ×3, around the current state."""
        t = self.at(t)
        _, y0 = self.ctx.state_at(t)
        self._c("shake", self.ctx.foot, t, amp, "y", y0)
        self._footage(t, t + 0.26)

    def cam_push(self, t, d=0.6, k=1.08):
        """Slow push on an emotional beat: scale ×k over d. It HOLDS until the next punch-in
        step (a tl.set), which is how a two-camera edit cuts back."""
        t = self.at(t)
        s0, _ = self.ctx.state_at(t)
        self._c("push", self.ctx.foot, t, d, r3(s0), r3(s0 * k))
        self.ctx._push(t + d, k)
        self._footage(t, t + d)

    def cam_sway(self, t, n=5, period=0.32, deg=1.2, dx=16):
        """Sway the frame (puppet strings). Rotation exposes black corners unless the frame is
        scaled ≥ 1.07 (spec §5), so the scale is lifted for the sway and restored after."""
        t = self.at(t)
        s0, y0 = self.ctx.state_at(t)
        ss = r3(max(s0, 1.08))
        t1 = r3(t + period * (n + 1))
        self.set(self.ctx.foot, {"scale": ss}, t)
        self._c("ft", self.ctx.foot, {"rotation": 0, "x": 0},
                {"rotation": deg, "x": dx, "duration": period, "yoyo": True, "repeat": n,
                 "ease": "sine.inOut"}, t)
        self.set(self.ctx.foot, {"rotation": 0, "x": 0, "scale": r3(s0)}, t1)
        self._footage(t, t1)

    # ---- sound, captions
    def sfx(self, name, t, kind="normal", vol=0.2):
        """kind "exempt": a deliberate impact that must stay on its beat (bars, stamps, hook
        whooshes); "normal": slid off any word it would mask (scripts/scenes.py)."""
        self._sfx.append({"name": str(name), "t": self.at(t), "kind": kind, "base_vol": float(vol)})

    def hide(self, a=None, b=None):
        """Hide the captions over [a, b] (default: the whole scene)."""
        self._hide.append([self.at(self.start if a is None else a),
                           self.at(self.end if b is None else b)])

    def done(self):
        return {"id": self.id, "start": self.start, "end": self.end,
                "html": "".join(self.parts), "css": "\n".join(self._css),
                "js": list(self.lines), "sfx": list(self._sfx),
                "hide_captions": [list(w) for w in self._hide],
                "footage": [list(w) for w in self._foot], "punch_ok": self.punch_ok,
                "z": self.z, "cls": self.cls}


# ===================================================================== shells
def _sub(sub, ctx):
    if sub is None or sub == "":
        return ""
    if isinstance(sub, Html) and sub.lstrip().startswith("<small"):
        return sub
    return f"<small>{text(sub, ctx)}</small>"


def widget(s, body="", title=None, sub=None, lead=None, aside=None, eyebrow=None,
           eyebrow_icon=None, t_in=None, t_out=None, enter="drop", top=None, name="w",
           cls="", sfx="soft_whoosh", sfx_vol=0.14):
    """The glass widget in the sky zone (left 90, right 170, top 250 → y 230-600).

    Header (RTL order, right to left): `lead` (an avatar/badge) · title + sub · `aside`
    (a spinner, a status pill). `eyebrow` is the small icon + label line instead of a title.
    Enters with `drop` (or "slide" / "pop" / "none") at t_in (default: the scene start) and
    leaves with `away` at t_out (default: 0.3 s before the scene end; False = no exit)."""
    ctx = s.ctx
    wid = s.uid(name)
    head = ""
    if eyebrow:
        ic = icon(eyebrow_icon, 40, "rgba(255,255,255,.85)", 2.2) if eyebrow_icon else ""
        head += f'<div class="kt-eb">{ic}<span>{text(eyebrow, ctx)}</span></div>'
    if title or sub or lead or aside:
        tt = ""
        if title or sub:
            tt = (f'<div class="kt-tt">{"<b>" + text(title, ctx) + "</b>" if title else ""}'
                  f'{_sub(sub, ctx)}</div>')
        head += f'<div class="kt-hd">{lead or ""}{tt}{aside or ""}</div>'
    style = f' style="top:{r3(top)}px"' if top is not None else ""
    t_in = s.start if t_in is None else s.at(t_in)
    if enter == "drop":
        s.drop("#" + wid, t_in, 0.55)
    elif enter == "slide":
        s.slide("#" + wid, t_in, 300, 0.4)
    elif enter == "pop":
        s.pop("#" + wid, t_in, 0.45)
    elif enter == "none":
        s.set("#" + wid, {"opacity": 1}, t_in)
    else:
        raise KitError(f"kit: widget enter {enter!r} — drop | slide | pop | none")
    if t_out is not False:
        t_out = r3(s.end - 0.3) if t_out is None else s.at(t_out)
        s.away("#" + wid, t_out)
    if sfx:
        s.sfx(sfx, t_in, "normal", sfx_vol)
    return Html(f'<div class="kt-wid kt-glass {cls}" id="{wid}"{style}>{head}{body}</div>')


def panel(s, body, name="p", top=None, left=None, width=None, cls="kt-glass", t_in=None,
          enter="fade"):
    """A free-positioned surface (glass by default) for things that are not a sky widget.
    It starts hidden (CSS) and ENTERS on its own at `t_in` (default: the scene start) with
    `enter` = fade | pop | drop | none — a hidden panel nobody animates in is a silent blank."""
    st = []
    if top is not None:
        st.append(f"top:{r3(top)}px")
    if left is not None:
        st.append(f"left:{r3(left)}px")
    if width is not None:
        st.append(f"width:{r3(width)}px")
    style = f' style="{";".join(st)}"' if st else ""
    pid = s.uid(name)
    t = s.start if t_in is None else s.at(t_in)
    if enter == "pop":
        s.pop("#" + pid, t)
    elif enter == "drop":
        s.drop("#" + pid, t)
    elif enter == "fade":
        s.fade("#" + pid, t, 0.25, 0, 1)
    return Html(f'<div class="kt-panel {cls}" id="{pid}"{style}>{body}</div>')


def card(s, body, head, meta=None, meta_tone="", name="card"):
    """The dark hook card: header strip (title right, meta left) over a body."""
    ctx = s.ctx
    mt = f' class="kt-{meta_tone}"' if meta_tone else ""
    m = f"<em{mt}>{text(meta, ctx)}</em>" if meta else ""
    # the card may sit under the returning frame for its last 0.38 s: layering is intended
    return Html(f'<div class="kt-hcard" id="{s.uid(name)}" data-layout-allow-overlap><div class="kt-ch"><span>'
                f'{text(head, ctx)}</span>{m}</div><div class="kt-cb">{body}</div></div>')


# ================================================================= components
def swap(s, items, at=(), tones=None, cls="", name="sw"):
    """A state stack: every state in one grid cell, switched with steps() (never a
    textContent tween). `at` = [(t, k), ...] or a list of times meaning states 1, 2, ...
    State 0 shows from the start."""
    sid = s.n(name)
    kids = []
    for i, it in enumerate(items):
        tc = f' class="{tones[i]}"' if tones and i < len(tones) and tones[i] else ""
        kids.append(f"<i{tc}>{text(it, s.ctx)}</i>")
    seq = [(0, 0)]
    for i, a in enumerate(at or ()):
        if isinstance(a, (list, tuple)):
            seq.append((s.at(a[0]), int(a[1])))
        else:
            seq.append((s.at(a), i + 1))
    for t, _ in seq[1:]:
        if not s.start <= t <= s.end:
            # the usual cause: ctx.t() found an EARLIER occurrence (prefix-tolerant matching
            # hears "שאתם" as "אתם") — anchor it with after=
            s.ctx.note(f"{s.id}: a state change at {t:.2f}s lies outside the scene "
                       f"({s.start:.2f}-{s.end:.2f}s) — anchor the word with ctx.t(word, after=...)")
    s.steps("#" + sid, seq)
    h = Html(f'<span class="kt-stk {cls}" id="{sid}">{"".join(kids)}</span>')
    h.id = sid
    return h


TONE_ICON = {"wait": "hourglass", "ok": "check", "bad": "x"}


def pill(s, states, at=(), name="pl"):
    """A status pill with stacked states: [("wait", "pending"), ("ok", "approved")]. Tones:
    mute | wait | ok | bad | info."""
    items, tones = [], []
    for tone, label in states:
        ic = icon(TONE_ICON[tone], 30, "currentColor", 2.6) if tone in TONE_ICON else ""
        items.append(Html(f"{ic}<span>{text(label, s.ctx)}</span>"))
        tones.append(f"kt-t-{tone}")
    return swap(s, items, at, tones, cls="kt-pill", name=name)


def spinner(s, t0=None, t1=None, size=54, color="var(--blue)", kind="refresh", name="sp"):
    """A loading spinner that rotates linearly for the widget's whole life (spec §4.3:
    spinners never stand still)."""
    sid = s.n(name)
    t0 = s.start if t0 is None else s.at(t0)
    t1 = s.end if t1 is None else s.at(t1)
    s.spin("#" + sid, t0, t1)
    if kind == "ring":
        g = (f'<svg class="kt-ico" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
             f'style="color:{color}"><circle cx="12" cy="12" r="9" stroke="currentColor" '
             f'stroke-opacity=".2" stroke-width="2.6"/><path d="M12 3a9 9 0 0 1 9 9" '
             f'stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/></svg>')
    else:
        g = icon(kind, size, color, 2.4)
    h = Html(f'<b class="kt-spin" id="{sid}">{g}</b>')
    h.id = sid
    return h


def empty(s, title, sub=None, icon_name="inbox", size=190):
    """An empty state: a big outline icon, a bold line, a quiet line ("no new messages")."""
    return Html(f'<div class="kt-empty">{icon(icon_name, size, "#5B6B8C", 1.3)}'
                f'<b>{text(title, s.ctx)}</b>{_sub(sub, s.ctx)}</div>')


def progress(s, label=None, values=None, fills=(), warm=False, name="pg"):
    """A labelled progress bar. values = [(t, "2%"), (t, "3%")] (stacked, switched at t);
    fills = [(t, d, to), ...] sequential fill segments, `to` in 0..1."""
    fid = s.n(name + "f")
    prev, last = 0.0, -1.0
    for i, (t, d, to) in enumerate(fills or ()):
        t = s.at(t)
        if t < last - 1e-6:
            raise KitError(f"kit: progress {fid}: fill segment {i} starts at {t} before the "
                           f"previous one ends ({last}) — two tweens on scaleX would overlap")
        frm = {"scaleX": prev, "opacity": 1}
        s.tween("#" + fid, frm, {"scaleX": float(to), "opacity": 1}, t, d,
                "power2.out" if i == 0 else "none")
        prev, last = float(to), t + d
    head = ""
    if label or values:
        # the first value is the resting state: it shows from the start
        v = swap(s, [x for _, x in values], [t for t, _ in values[1:]], name="pv") if values else ""
        head =f'<div class="kt-pt"><span>{text(label, s.ctx)}</span><b>{v}</b></div>'
    return Html(f'<div class="kt-pg">{head}<div class="kt-tr{" kt-warm" if warm else ""}">'
                f'<i id="{fid}"></i></div></div>')


def avatars(s, picks, odd=None, odd_t=None, colors=None, size=120, name="kd"):
    """A row of coloured people who get a green check one by one (each on its time in
    `picks`), plus an optional dashed, unpicked "odd one out" with a flipping hourglass."""
    cols = colors or ["var(--p1)", "var(--p2)", "var(--p3)", "var(--p4)", "var(--p5)"]
    kids = []
    for i, t in enumerate(picks):
        kid = s.uid(f"{name}{i}")
        c = cols[i % len(cols)]
        kids.append(f'<div class="kt-kid" id="{kid}"><i style="background:{c};width:{size}px;'
                    f'height:{size}px">{icon("user", int(size * 0.58), "#fff", 2.1)}</i>'
                    f'<b class="kt-ok" id="{kid}ok">{icon("check", int(size * 0.28), "#fff", 3)}</b></div>')
        if t is None:
            continue
        t = s.at(t)
        s.tween(f"#{kid}ok", {"opacity": 0, "scale": 0}, {"opacity": 1, "scale": 1}, t, 0.25,
                "back.out(2.5)")
        s.tween(f"#{kid}", {"y": 0}, {"y": -26}, t, 0.2, "power2.out", yoyo=True, repeat=1)
    h = f'<div class="kt-kids">{"".join(kids)}</div>'
    if odd:
        oid = s.uid(name + "odd")
        h += (f'<div class="kt-odd" id="{oid}"><i>{icon("user", 84, "#8A93A6", 2.1)}</i>'
              f'<span>{text(odd, s.ctx)}</span><b class="kt-hg" id="{oid}hg">'
              f'{icon("hourglass", 40, "var(--amber)", 2.2)}</b></div>')
        if odd_t is not None:
            t, rot = s.at(odd_t), 0
            while t < s.end - 0.3:
                s.tween(f"#{oid}hg", {"rotation": rot}, {"rotation": rot + 180}, t, 0.4,
                        "power2.inOut")
                rot += 180
                t = r3(t + 0.8)
    return Html(h)


def week(s, hit=4, t0=None, step=0.13, pulse_t=None, days=None, name="d"):
    """A week strip whose days light up one by one until day `hit` glows electric and pulses
    ("waiting for Thursday"). days default to the Hebrew week (RTL: Sunday on the right)."""
    days = days or (["א", "ב", "ג", "ד", "ה", "ו", "ש"] if s.ctx.dir == "rtl"
                    else ["M", "T", "W", "T", "F", "S", "S"])
    t0 = s.at(s.start + 0.25 if t0 is None else t0)
    out = []
    for i, d in enumerate(days):
        did = s.uid(f"{name}{i}")
        hitc = " kt-hit" if i == hit else ""
        # the lit layer sits BEHIND the one letter (no duplicate glyph); the letter turns white
        out.append(f'<span class="kt-day" id="{did}"><i class="kt-lit{hitc}" id="{did}l"></i>'
                   f'<span id="{did}t">{text(d, s.ctx)}</span></span>')
        if i <= hit:
            s.set(f"#{did}l", {"opacity": 1}, r3(t0 + step * i))
            s.set(f"#{did}t", {"color": "#ffffff"}, r3(t0 + step * i))
    pt = s.at(pulse_t) if pulse_t is not None else r3(t0 + step * hit + 0.1)
    s.pulse("#" + s.uid(f"{name}{hit}"), pt, 1.2, 0.25)
    return Html(f'<div class="kt-days">{"".join(out)}</div>')


def phone(s, title, sub=None, t0=None, t1=None, fill=0.92, name="ph"):
    """A phone outline next to a title ("the next model / pre-order") with a pre-order bar
    filling and a spinner."""
    t0 = s.at(s.start + 0.07 if t0 is None else t0)
    t1 = s.at(s.end - 0.3 if t1 is None else t1)
    fid = s.uid(name + "f")
    s.tween("#" + fid, {"scaleX": 0.1, "opacity": 1}, {"scaleX": fill, "opacity": 1}, t0,
            max(0.2, t1 - t0), "power1.out")
    sp = spinner(s, s.start, s.end)
    return Html(f'<div class="kt-prow"><div class="kt-phone"><i></i></div><div class="kt-pcol">'
                f'<b>{text(title, s.ctx)}</b>{_sub(sub, s.ctx)}<div class="kt-tr"><i id="{fid}"></i>'
                f'</div></div>{sp}</div>')


def calendar(s, event, labels=None, moves=(), col=0, span=3, fly_t=None, never=None,
             never_t=None, days=None, name="cal"):
    """A week calendar with one event that slides one day later on each time in `moves`
    (its label switching through `labels`: "planned", "postponed", "postponed again"), flies
    off at fly_t, and optionally gets a red rubber stamp (`never`) at never_t."""
    ctx = s.ctx
    days = days or (["א", "ב", "ג", "ד", "ה", "ו", "ש"] if ctx.dir == "rtl"
                    else ["M", "T", "W", "T", "F", "S", "S"])
    inner_w = 820 - 72
    pitch = (inner_w - 92) / 6.0
    eid = s.uid(name + "e")
    width = span * pitch - (pitch - 92)
    side = "right" if ctx.dir == "rtl" else "left"
    lab = swap(s, labels, [t for t in moves][:max(0, len(labels) - 1)]) if labels else ""
    sign = -1 if ctx.dir == "rtl" else 1
    prev = 0.0
    for k, t in enumerate(moves):
        x = r3(sign * (k + 1) * pitch)
        s.tween("#" + eid, {"x": prev}, {"x": x}, t, 0.3, "power2.inOut")
        prev = x
    if fly_t is not None:
        s.tween("#" + eid, {"x": prev, "opacity": 1}, {"x": prev + sign * 900, "opacity": 0},
                fly_t, 0.4, "power3.in")
        s.sfx("soft_whoosh", fly_t, "normal", 0.12)
    for t in moves:
        s.sfx("swap_pop", t, "normal", 0.1)
    st = ""
    if never and never_t is not None and (s.end - 0.3) - (s.at(never_t) + 0.18) < 0.3:
        ctx.note(f"{s.id}: the stamp lands {s.at(never_t) + 0.18:.2f}s and the widget leaves at "
                 f"{s.end - 0.3:.2f}s by default — give it ≥ 0.3 s of hold (end the scene later, or "
                 f"pass t_out to the widget)")
    if never:
        st = stamp(s, never, never_t if never_t is not None else s.end - 0.6, x=150, y=120,
                   rot=-8, size=96)
    cells = "".join(f"<span>{text(d, ctx)}</span>" for d in days)
    boxes = "".join("<i></i>" for _ in days)
    return Html(f'<div class="kt-cal"><div class="kt-cgrid">{cells}</div><div class="kt-cells">{boxes}</div>'
                f'<div class="kt-evt" id="{eid}" style="{side}:{r3(col * pitch)}px;min-width:{r3(width)}px">'
                f'<b>{text(event, ctx)}</b><small>{lab}</small></div>{st}</div>')


def today(s, label, ring_t, big=None, t_in=None, t_out=None, x=None, top=None, name="pg"):
    """A white calendar page ("today") that pops in, with a hand-drawn red marker circle
    drawn around it at ring_t."""
    G = s.ctx.G
    pid = s.uid(name)
    left = (G["center_x"] if x is None else x) - 210
    tp = s.ctx.sky["top"] if top is None else top
    t_in = s.at(s.start + 0.04 if t_in is None else t_in)
    s.pop("#" + pid, t_in, 0.45)
    s.draw(f"#{pid}r path", ring_t, 0.5)
    if t_out is not False:
        s.away("#" + pid, s.end - 0.3 if t_out is None else t_out)
    s.sfx("soft_whoosh", t_in, "normal", 0.14)
    s.sfx("typing", ring_t, "normal", 0.1)
    body = (f'<b class="kt-pbig">{text(big, s.ctx)}</b>' if big is not None
            else icon("calendar", 140, "#0B1020", 1.4))
    return Html(f'<div class="kt-page" id="{pid}" style="left:{r3(left)}px;top:{r3(tp)}px">'
                f'<div class="kt-ph">{text(label, s.ctx)}</div><div class="kt-pd">{body}</div>'
                f'<svg class="kt-ring" id="{pid}r" viewBox="0 0 400 300"><path pathLength="100" '
                f'd="M200 22C90 18 22 70 26 150s90 132 190 128 166-62 160-136S300 20 186 30"/></svg></div>')


def hand(s, t_in, t_tap, dx=260, dy=240, size=92, name="hand", style=""):
    """The tap cursor: slides in from (dx, dy), taps (scale .85 yoyo) at t_tap. Position it
    with `style` (left/top/%) inside its parent; the dialog puts it on its yes-button."""
    hid = s.uid(name)
    t_in, t_tap = s.at(t_in), s.at(t_tap)
    s.tween("#" + hid, {"opacity": 0, "x": dx, "y": dy}, {"opacity": 1, "x": 0, "y": 0}, t_in,
            min(0.5, max(0.2, t_tap - t_in - 0.1)), "power2.out")
    s.tween("#" + hid, {"scale": 1}, {"scale": 0.85}, r3(t_tap - 0.08), 0.1, "power2.out",
            yoyo=True, repeat=1)
    h = hand_svg(hid, size)
    if style:
        h = Html(h.replace('class="kt-hand"', f'class="kt-hand" style="{style}"', 1))
    return h


def dialog(s, title, wait, done, no, yes, yes_done, show_t, tap_t, spin=True, name="dl"):
    """An approval dialog ("waiting for someone else's approval…"); at show_t two buttons
    appear, a hand cursor taps the yes-button at tap_t, it turns green with a check and the
    subtitle flips to `done`. The comic twist: the viewer approves themself."""
    ctx = s.ctx
    show_t, tap_t = s.at(show_t), s.at(tap_t)
    sub = swap(s, [wait, done], [tap_t])
    sp = spinner(s, s.start, show_t) if spin else ""
    if spin:
        s.set("#" + sp.id, {"opacity": 0}, show_t)
    bid, yid = s.uid(name + "b"), s.uid(name + "y")
    lab = swap(s, [yes, Html(f'<span>{text(yes_done, ctx)}</span>{icon("check", 40, "#fff", 3)}')],
               [tap_t], cls="kt-blab")
    s.tween("#" + bid, {"opacity": 0, "y": 20}, {"opacity": 1, "y": 0}, show_t, 0.3, "expo.out")
    hd = hand(s, show_t + 0.14, tap_t, name=name + "h")
    s.set(f"#{yid}g", {"opacity": 1}, tap_t)
    s.pulse("#" + yid, tap_t, 1.06, 0.12)
    s.sfx("swap_pop", show_t, "normal", 0.12)
    s.sfx("click", tap_t, "exempt", 0.5)
    s.sfx("ding", tap_t + 0.05, "normal", 0.18)
    return Html(f'<div class="kt-hd"><div class="kt-tt"><b>{text(title, ctx)}</b><small>{sub}</small>'
                f'</div>{sp}</div><div class="kt-btns" id="{bid}"><span class="kt-btn">{text(no, ctx)}</span>'
                f'<span class="kt-btn kt-yes" id="{yid}"><em id="{yid}g"></em>{lab}{hd}</span></div>')


def task(s, title, flip_t, who=("Unassigned", "You"), status=(("mute", "Open"), ("ok", "In progress")),
         label="Owner", pulse_t=None, badge="check", name="tk"):
    """A task card: a badge, a title, a status pill (open → in progress) and an owner row
    whose chip flips from "unassigned" to "you" at flip_t (pulsing at pulse_t first)."""
    ctx = s.ctx
    st = pill(s, list(status), [flip_t], name=name + "s")
    asg = swap(s, [who[0], Html(f'<em>{icon("user", 30, "#fff", 2.4)}</em><span>{text(who[1], ctx)}</span>')],
               [flip_t], tones=["kt-none", "kt-you"], cls="kt-asg", name=name + "a")
    if pulse_t is not None:
        s.pulse("#" + asg.id, pulse_t, 1.15, 0.18)
    s.pulse("#" + asg.id, s.at(flip_t) + 0.02, 1.2, 0.2)
    s.sfx("swap_pop", flip_t, "normal", 0.14)
    return Html(f'<div class="kt-hd"><i class="kt-tsq">{icon(badge, 34, "#fff", 3)}</i><div class="kt-tt">'
                f'<b>{text(title, ctx)}</b></div>{st}</div><div class="kt-row"><span>{text(label, ctx)}</span>'
                f'{asg}</div>')


def waiting_room(s, title, wait, fail, fail_t, leave=None, leave_t=None, press_t=None,
                 host="?", widget_name="w", name="wr"):
    """A video-call waiting room: a "?" host, "waiting for the host to join…", a spinner. At
    fail_t it flips to the red `fail` line and the widget shakes; a red `leave` button pops
    at leave_t and gets pressed at press_t."""
    ctx = s.ctx
    fail_t = s.at(fail_t)
    sub = swap(s, [wait, fail], [fail_t], tones=["", "kt-red"])
    sp = spinner(s, s.start, s.end)
    s.set("#" + sp.id, {"opacity": 0.25}, fail_t)
    s.shake(widget_name, fail_t, 14, "x")
    s.sfx("message", fail_t, "normal", 0.16)
    lv = ""
    if leave:
        lid = s.uid(name + "l")
        lt = s.at(leave_t if leave_t is not None else fail_t + 0.5)
        s.tween("#" + lid, {"opacity": 0, "scale": 0.6}, {"opacity": 1, "scale": 1}, lt, 0.3,
                "back.out(2)")
        s.sfx("pop", lt, "normal", 0.2)
        if press_t is not None:
            s.tap("#" + lid, press_t)
            s.sfx("click", press_t, "normal", 0.3)
        lv = f'<div class="kt-leave" id="{lid}">{text(leave, ctx)}</div>'
    return Html(f'<div class="kt-hd"><i class="kt-host">{text(host, ctx)}</i><div class="kt-tt">'
                f'<b>{text(title, ctx)}</b><small>{sub}</small></div>{sp}</div>{lv}')


def notify(s, app, title, body=None, meta=None, icon_name="bell"):
    """A notification banner (app badge, app name + time, bold title, one line)."""
    ctx = s.ctx
    m = f"<span>{text(meta, ctx)}</span>" if meta else ""
    p = f"<p>{text(body, ctx)}</p>" if body else ""
    return Html(f'<div class="kt-nt"><i class="kt-app">{icon(icon_name, 50, "#fff", 2.2)}</i><div class="kt-ntx">'
                f'<div class="kt-nmeta"><span>{text(app, ctx)}</span>{m}</div><b>{text(title, ctx)}</b>{p}</div></div>')


def chat(s, msgs, typing=None, name="ch"):
    """A chat thread: msgs = [(side "in"|"out", text, t), ...], each bubble pops on its t;
    typing = (t0, t1) shows the three-dot indicator before the first incoming reply."""
    ctx = s.ctx
    out = []
    if typing:
        a, b = s.at(typing[0]), s.at(typing[1])
        tid = s.uid(name + "ty")
        out.append(f'<div class="kt-bub kt-in kt-typing" id="{tid}"><i id="{tid}0"></i><i id="{tid}1"></i>'
                   f'<i id="{tid}2"></i></div>')
        s.fade("#" + tid, a, 0.15)
        for k in range(3):
            n = max(1, int((b - a) / 0.3) - 1)
            s.tween(f"#{tid}{k}", {"y": 0}, {"y": -12}, r3(a + 0.1 * k), 0.15, "sine.inOut",
                    yoyo=True, repeat=n)
        s.fade("#" + tid, b, 0.1, 1, 0)
    for i, (side, msg, t) in enumerate(msgs):
        bid = s.uid(f"{name}{i}")
        out.append(f'<div class="kt-bub kt-{side}" id="{bid}">{text(msg, ctx)}</div>')
        s.tween("#" + bid, {"opacity": 0, "y": 30, "scale": 0.9}, {"opacity": 1, "y": 0, "scale": 1},
                t, 0.3, "back.out(1.6)")
        s.sfx("message" if side == "in" else "pop", t, "normal", 0.14)
    return Html(f'<div class="kt-chat">{"".join(out)}</div>')


def strike_pills(s, items, top=None, name="sp"):
    """Glass pills that pop in on their word and get a red RTL strike-through (then dim):
    items = [(text, t_in, t_strike), ...]. The thing the speaker tells you to STOP doing."""
    ctx = s.ctx
    out = []
    for i, (label, t_in, t_st) in enumerate(items):
        pid, bid = s.uid(f"{name}{i}"), s.uid(f"{name}{i}b")
        out.append(f'<div class="kt-spill kt-glass" id="{pid}">{text(label, ctx)}<i class="kt-strike" id="{bid}"></i></div>')
        s.pop("#" + pid, t_in, 0.4)
        s.sfx("swap_pop", t_in, "normal", 0.14)
        if t_st is not None:
            s.strike("#" + bid, "#" + pid, t_st)
            s.sfx("click", t_st, "normal", 0.4)
    tp = ctx.sky["top"] + 50 if top is None else top
    return Html(f'<div class="kt-pills" style="top:{r3(tp)}px">{"".join(out)}</div>')


def stamp(s, label, t, x=None, y=None, rot=-8, size=96, tone="red", name=None, sfx=True):
    """A rubber stamp that slams (scale 2.3 → 1, rotation → rot, 0.18 s power4.in) at t.
    x/y = its centre in its parent's coordinates (default: the safe-zone centre, y 420)."""
    sid = s.n(name or "st")
    x = s.ctx.G["center_x"] if x is None else x
    y = 420 if y is None else y
    s.stamp("#" + sid, t, rot)
    if sfx:
        s.sfx("glass_snap", t + 0.16, "exempt", 0.16)
    tone_c = "" if tone == "red" else f" kt-st-{tone}"
    # a zero-size anchor at (x, y) centres the stamp without a CSS transform on the element
    # GSAP rotates and scales (lint: gsap_css_transform_conflict)
    return Html(f'<div class="kt-stampw" data-layout-allow-overlap style="left:{r3(x)}px;top:{r3(y)}px"><div class="kt-stamp{tone_c}" '
                f'id="{sid}" style="font-size:{size}px">{text(label, s.ctx)}</div></div>')


def percent(s, values=None, count=None, top=None, bar=True, size=230, name="pc"):
    """The Rollin percent: a huge number on the chest that swaps with a blur-in, over a rounded
    bar that fills to match. values = [(t, "10%"), (t, "20%")] or count = (t0, t1, a, b)
    for a running count (suffix "%")."""
    ctx = s.ctx
    if count:
        t0, t1, a, b = s.at(count[0]), s.at(count[1]), float(count[2]), float(count[3])
        n = max(2, min(24, int((t1 - t0) * 12)))
        values = []
        for i in range(n + 1):
            u = i / float(n)
            e = 1 - (1 - u) ** 3
            values.append((r3(t0 + (t1 - t0) * u), f"{int(round(a + (b - a) * e))}%"))
    if not values:
        raise KitError("kit: percent needs values=[(t, '10%'), ...] or count=(t0, t1, a, b)")
    sw = swap(s, [Html(text(v, ctx)) for _, v in values], [t for t, _ in values[1:]],
              cls="kt-numstk", name=name + "n")
    # the number lands with a blur-in (Rollin); later swaps re-blur only when they are far
    # enough apart that two filter tweens cannot overlap (a running count just steps)
    ts = [s.at(t) for t, _ in values]
    s.tween("#" + sw.id, {"filter": "blur(12px)", "scale": 1.1}, {"filter": "blur(0px)", "scale": 1},
            ts[0], 0.25, "power3.out")
    for i in range(1, len(ts)):
        gap = (ts[i + 1] if i + 1 < len(ts) else s.end) - ts[i]
        if gap >= 0.24 and ts[i] - ts[i - 1] >= 0.26:
            s.tween("#" + sw.id, {"filter": "blur(10px)", "scale": 1.06}, {"filter": "blur(0px)", "scale": 1},
                    ts[i], min(0.2, gap - 0.02), "power3.out")
    fid = s.uid(name + "f")
    if bar:
        prev = None
        for i, (t, v) in enumerate(values):
            m = re.search(r"\d+(?:\.\d+)?", str(v))
            if not m:
                continue
            to = max(0.0, min(1.0, float(m.group(0)) / 100.0))
            if prev is None:
                s.tween("#" + fid, {"scaleX": 0, "opacity": 1}, {"scaleX": to, "opacity": 1},
                        s.at(t), 0.35, "power3.out")
            else:
                d = 0.3
                if i + 1 < len(values):
                    d = min(0.3, max(0.04, s.at(values[i + 1][0]) - s.at(t) - 0.005))
                s.tween("#" + fid, {"scaleX": prev}, {"scaleX": to}, s.at(t), d, "power2.out")
            prev = to
    tp =(ctx.framing["chest"][0] + 20) if top is None else top
    br = f'<div class="kt-tr kt-pbar"><i id="{fid}"></i></div>' if bar else ""
    return Html(f'<div class="kt-pct" style="top:{r3(tp)}px"><div class="kt-num" style="font-size:{size}px">'
                f'{sw}</div>{br}</div>')


def stack(s, lines, top=None, size=104, align="center", lead=0.04, name="h"):
    """A word-by-word stacked headline (the hook's thin opener). lines: markup string —
    " / " breaks a line, *bold keyword*, ^light partner^, +bold white+, =gradient=, plain =
    thin; or [[(word, cls), ...], ...]. Each word lands on its spoken start (from words.json)."""
    ctx = s.ctx
    rows = parse_markup(lines) if isinstance(lines, str) else lines
    toks = [w for row in rows for w, _ in row]
    times = ctx.sync(toks, s.start, s.end)
    G = ctx.G
    gx0, _, gx1, gy1 = G["safe"]
    if top is None:
        top = min(ctx.framing["chest"][0] + 15, gy1 - 12 - len(rows) * size * 1.04)
    out, k = [], 0
    for row in rows:
        sp = []
        for w, cls in row:
            wid = s.uid(f"{name}{k}")
            sp.append(f'<span class="kt-w {cls}" id="{wid}">{text(w, ctx)}</span>')
            s.word("#" + wid, max(s.start, r3(times[k][0] - lead)))
            k += 1
        out.append(f'<div class="kt-ln">{" ".join(sp)}</div>')
    al = {"center": "center", "right": "flex-start" if ctx.dir == "rtl" else "flex-end",
          "left": "flex-end" if ctx.dir == "rtl" else "flex-start"}[align]
    return Html(f'<div class="kt-stack" style="top:{r3(top)}px;font-size:{size}px;align-items:{al}">'
                f'{"".join(out)}</div>')


_MK = {"*": "kt-bold", "^": "kt-light", "+": "kt-white", "=": "kt-grad", "_": "kt-thin"}


def parse_markup(s):
    rows = []
    for ln in re.split(r"\s+/\s+", str(s).strip()):
        row = []
        for seg in re.split(r"(\*[^*]+\*|\^[^^]+\^|\+[^+]+\+|=[^=]+=|_[^_]+_)", ln):
            if not seg.strip():
                continue
            cls = "kt-thin"
            if len(seg) > 2 and seg[0] in _MK and seg[-1] == seg[0]:
                cls, seg = _MK[seg[0]], seg[1:-1]
            row += [(w, cls) for w in seg.split()]
        if row:
            rows.append(row)
    return rows


# ==================================================================== overlays
def sparks(s, t, n=18, box=(120, 500, 960, 1300), up=(-900, -200), spread=520, seed=11,
           name="spk"):
    """A burst of glowing particles (solid + box-shadow glow: never radial gradients, which
    blacken the capture in bulk). Positions are seeded here, never Math.random at runtime."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        sid = s.uid(f"{name}{i}")
        x = box[0] + (box[2] - box[0]) * (i % 7) / 6.0
        y = rng.uniform(box[1], box[3])
        out.append(f'<i class="kt-spark" id="{sid}" style="left:{r3(x)}px;top:{r3(y)}px"></i>')
        s.tween("#" + sid, {"opacity": 1, "x": 0, "y": 0, "scale": 1},
                {"opacity": 0, "x": round(rng.uniform(-spread, spread)),
                 "y": round(rng.uniform(up[0], up[1])), "scale": 0.3},
                r3(s.at(t) + rng.uniform(0, 0.08)), 0.9, "power3.out")
    return Html("".join(out))


def bars(s, slam_t=None, lift_t=None, fade_t=None, burst_t=None, n=7, opacity=0.85,
         spark_n=18, seed=11, shake=True, name="bars"):
    """Full-frame steel prison bars. Plant: slam down at slam_t (staggered, power4.in, the
    first bar LANDS on slam_t, camera shake on impact), lift away at lift_t. Callback: fade
    back in at fade_t and burst outward with sparks at burst_t. Decoration: data-grid=bleed."""
    ctx = s.ctx
    W, H = ctx.W, ctx.H
    cid = s.uid(name)
    gap = (W - 120 - 34) / float(max(1, n - 1))
    bs = "".join(f'<i class="kt-bar" id="{cid}b{k}" style="left:{r3(60 + k * gap)}px"></i>'
                 for k in range(n))
    cr = (f'<i class="kt-cross" id="{cid}c0" style="top:{round(H * 0.219)}px"></i>'
          f'<i class="kt-cross" id="{cid}c1" style="top:{round(H * 0.698)}px"></i>')
    if slam_t is not None:
        slam_t = s.at(slam_t)
        s.set("#" + cid, {"opacity": 1}, r3(slam_t - 0.24))
        for k in range(n):
            s.tween(f"#{cid}b{k}", {"y": -H}, {"y": 0}, r3(slam_t - 0.24 + 0.025 * k), 0.24, "power4.in")
        for c in range(2):
            s.tween(f"#{cid}c{c}", {"scaleX": 0}, {"scaleX": 1}, slam_t, 0.2, "power3.out")
        if shake:
            s.cam_shake(slam_t)
        s.sfx("bars", slam_t - 0.02, "exempt", 0.42)
    if fade_t is not None:
        s.fade("#" + cid, fade_t, 0.6, 0, opacity)
        s.sfx("bars", fade_t, "exempt", 0.2)
    if lift_t is not None:
        lift_t = s.at(lift_t)
        for k in range(n):
            s.tween(f"#{cid}b{k}", {"y": 0}, {"y": -H}, r3(lift_t + 0.02 * k), 0.32, "power3.in")
        for c in range(2):
            s.tween(f"#{cid}c{c}", {"y": 0}, {"y": -H}, r3(lift_t + 0.02 * (n + c)), 0.32, "power3.in")
        s.sfx("whoosh_low", lift_t, "exempt", 0.16)
    sp = ""
    if burst_t is not None:
        burst_t = s.at(burst_t)
        c = (n - 1) / 2.0
        for k in range(n):
            d = k - c
            s.tween(f"#{cid}b{k}", {"x": 0, "y": 0, "rotation": 0, "opacity": 1},
                    {"x": round(d * 240), "y": round(-300 - abs(d) * 120), "rotation": round(d * 22),
                     "opacity": 0}, burst_t, 0.7, "power3.out")
        for cc in range(2):
            s.tween(f"#{cid}c{cc}", {"scaleX": 1, "opacity": 1}, {"scaleX": 1.6, "opacity": 0},
                    burst_t, 0.4, "power3.out")
        sp = sparks(s, burst_t, spark_n, seed=seed, name=name + "s")
        s.sfx("shatter", burst_t - 0.12, "exempt", 0.32)
    init = "" if fade_t is not None else ' style="opacity:0"' if slam_t is not None else ""
    if fade_t is None and slam_t is None:
        s.set("#" + cid, {"opacity": opacity}, s.start)
    return Html(f'<div class="kt-full kt-bars" id="{cid}" data-grid="bleed"{init}>{bs}{cr}</div>{sp}')


def puppet(s, drop_t, sway_t=None, sway_n=5, snap_t=None, out_t=None, anchors=None,
           bar_y=None, name="pup"):
    """A wooden puppet control bar drops in at the top; strings draw down to the speaker's
    shoulders and head. At sway_t the bar and the CAMERA sway together ("someone else moves
    your life"); at snap_t the strings snap and the bar flies off (the callback). Anchors
    come from the framing map. Decoration: data-grid=bleed."""
    ctx = s.ctx
    fr = ctx.framing
    G = ctx.G
    cx = max(320, min(ctx.W - 320, fr["face_cx"]))
    ch0 = fr["chest"][0]
    anchors = anchors or [(max(60, cx - 320), ch0 + 70), (cx, fr["head_top"] + 20),
                          (min(ctx.W - 60, cx + 320), ch0 + 70)]
    by = bar_y if bar_y is not None else max(G["safe"][1] + 42, fr["head_top"] - 360)
    pid = s.uid(name)
    lines = []
    for i, (x, y) in enumerate(anchors):
        x1 = cx + (i - (len(anchors) - 1) / 2.0) * 190
        lines.append(f'<line class="kt-str" id="{pid}s{i}" x1="{r3(x1)}" y1="{r3(by + 13)}" '
                     f'x2="{r3(x)}" y2="{r3(y)}" pathLength="100"/>')
    svg = (f'<svg class="kt-pup" id="{pid}" data-grid="bleed" viewBox="0 0 {ctx.W} {ctx.H}" '
           f'width="{ctx.W}" height="{ctx.H}"><g id="{pid}g">{"".join(lines)}<g id="{pid}x">'
           f'<rect x="{r3(cx - 230)}" y="{r3(by - 17)}" width="460" height="34" rx="17" style="fill:var(--wood)"/>'
           f'<rect x="{r3(cx - 17)}" y="{r3(by - 79)}" width="34" height="170" rx="17" style="fill:var(--wood2)"/>'
           f'</g></g></svg>')
    drop_t = s.at(drop_t)
    s.tween(f"#{pid}x", {"y": -200, "opacity": 0}, {"y": 0, "opacity": 1}, drop_t, 0.4, "expo.out")
    for i in range(len(anchors)):
        s.draw(f"#{pid}s{i}", r3(drop_t + 0.1 + 0.08 * i), 0.4)
    s.sfx("soft_whoosh", drop_t, "normal", 0.16)
    if sway_t is not None:
        s.tween(f"#{pid}g", {"rotation": 0}, {"rotation": 7}, sway_t, 0.32, "sine.inOut",
                yoyo=True, repeat=sway_n, svgOrigin=f"{r3(cx)} {r3(by)}")
        s.cam_sway(sway_t, sway_n)
        s.sfx("snap", sway_t, "normal", 0.1)
    if snap_t is not None:
        snap_t = s.at(snap_t)
        for i in range(len(anchors)):
            s.tween(f"#{pid}s{i}", {"opacity": 1, "y": 0}, {"opacity": 0, "y": (-1) ** i * 60},
                    snap_t, 0.25, "power2.out")
        s.tween(f"#{pid}x", {"y": 0, "rotation": 0, "opacity": 1}, {"y": -500, "rotation": -25, "opacity": 0},
                r3(snap_t + 0.02), 0.5, "power3.in")
        s.sfx("snap", snap_t, "exempt", 0.4)
    elif out_t is not False:
        s.fade("#" + pid, s.end - 0.3 if out_t is None else out_t, 0.25, 1, 0)
    return Html(svg)


def light_leak(s, t, d=1.8, twinkles=14, seed=11, region=None, name="lk"):
    """A warm light leak sweeping across the frame with twinkles ("something beautiful")."""
    ctx = s.ctx
    lid = s.uid(name)
    t = s.at(t)
    s.tween("#" + lid, {"opacity": 0, "x": -200}, {"opacity": 1, "x": 120}, t, 1.0, "power2.out")
    s.tween("#" + lid, {"opacity": 1}, {"opacity": 0}, max(t + 1.0, r3(t + d - 0.6)), 0.6, "power2.in")
    rng = random.Random(seed)
    x0, y0, x1, y1 = region or (ctx.G["safe"][0] + 20, ctx.sky["top"] - 10, ctx.G["safe"][2] - 40,
                                ctx.sky["bottom"])
    tw = []
    for i in range(twinkles):
        tid = s.uid(f"{name}t{i}")
        tw.append(f'<i class="kt-twk" id="{tid}" style="left:{round(rng.uniform(x0, x1))}px;'
                  f'top:{round(rng.uniform(y0, y1))}px"></i>')
        tt = r3(t + 0.1 + (d - 0.7) * i / max(1, twinkles - 1))
        s.tween("#" + tid, {"opacity": 0, "scale": 0.2}, {"opacity": 1, "scale": 1}, tt, 0.3,
                "sine.inOut", yoyo=True, repeat=1)
    s.sfx("riser_short", t, "normal", 0.1)
    return Html(f'<div class="kt-leak" id="{lid}" data-grid="bleed"></div>{"".join(tw)}')


def streak(s, t, d=0.75, cx=None, cy=None, rx=560, ry=140, tilt=-10, color="#FFFFFF",
           glow="var(--blue2)", width=7, name="sk"):
    """The Rollin light streak: a bright comet segment orbiting the speaker along a tilted
    ellipse, with a soft glow, in d seconds. Seek-safe: a stroke-dashoffset sweep."""
    ctx = s.ctx
    fr = ctx.framing
    cx = fr["face_cx"] if cx is None else cx
    cy = (fr["chin"] + 60) if cy is None else cy
    sid = s.uid(name)
    path = (f"M{r3(cx + rx)} {r3(cy)} A{rx} {ry} 0 1 1 {r3(cx - rx)} {r3(cy)} "
            f"A{rx} {ry} 0 1 1 {r3(cx + rx)} {r3(cy)}")
    tr = f"rotate({tilt} {r3(cx)} {r3(cy)})"
    svg = (f'<svg class="kt-streak" id="{sid}" data-grid="bleed" viewBox="0 0 {ctx.W} {ctx.H}" '
           f'width="{ctx.W}" height="{ctx.H}"><g transform="{tr}">'
           f'<path id="{sid}g" d="{path}" pathLength="100" style="stroke:{glow}" stroke-width="{width * 5}" '
           f'stroke-opacity=".45" fill="none" stroke-linecap="round" stroke-dasharray="30 170" stroke-dashoffset="30"/>'
           f'<path id="{sid}c" d="{path}" pathLength="100" style="stroke:{color}" stroke-width="{width}" '
           f'fill="none" stroke-linecap="round" stroke-dasharray="24 176" stroke-dashoffset="24"/></g></svg>')
    t = s.at(t)
    for part, a in (("g", 30), ("c", 24)):
        s.tween(f"#{sid}{part}", {"strokeDashoffset": a}, {"strokeDashoffset": -100}, t, d, "power1.inOut")
    s.fade("#" + sid, t, 0.08)
    s.sfx("whoosh_high", t, "normal", 0.12)
    return Html(svg)


def _png_alpha(path):
    """(w, h, coverage(x, y) -> 0..1) from an 8-bit non-interlaced PNG, stdlib only. Used to
    build the brand mark out of bricks. Returns None when the file is something else."""
    try:
        data = open(path, "rb").read()
    except OSError:
        return None
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, idat, w = 8, b"", None
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, bd, ct, _, _, il = struct.unpack(">IIBBBBB", chunk)
            if bd != 8 or il != 0 or ct not in (0, 2, 4, 6):
                return None
        elif typ == b"IDAT":
            idat += chunk
        pos += 12 + ln
    if not w:
        return None
    bpp = {0: 1, 2: 3, 4: 2, 6: 4}[ct]
    raw = zlib.decompress(idat)
    stride = w * bpp
    rows, prev, i = [], bytearray(stride), 0
    for _ in range(h):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line

    def cov(x, y):
        px = rows[y][x * bpp:(x + 1) * bpp]
        if ct in (4, 6):
            return px[-1] / 255.0
        lum = sum(px[:3]) / (3.0 * 255) if ct == 2 else px[0] / 255.0
        return 1.0 - lum          # dark ink on white
    return w, h, cov


def brick_points(shape, box, ctx=None):
    """Brick centres + rotations for a shape fitted into box (x0, y0, x1, y1).
    "arch" — two columns and a half-ring (a doorway: building your own way out);
    "wall" — a stepped wall; "brand" — the logo mark rasterised into bricks when brand.json
    has a compact mark (aspect 0.5-1.6), else the arch. Returns (pts, bw, bh, glow_rect)."""
    x0, y0, x1, y1 = box
    if shape == "brand":
        pts = _brand_bricks(box, ctx)
        if pts:
            return pts
        if ctx:
            ctx.note("bricks: brand mark is not a compact shape (or no brand.json) — built the arch")
        shape = "arch"
    if shape == "wall":
        k = min(1.0, (y1 - y0) / 330.0)
        bw, bh = 100 * k, 52 * k
        pts, rows = [], 5
        cx = (x0 + x1) / 2.0
        for r in range(rows):
            nb = rows - r + 1
            for c in range(nb):
                pts.append((cx + (c - (nb - 1) / 2.0) * (bw + 6 * k), y1 - bh / 2 - r * (bh + 6 * k), 0))
        return pts, bw, bh, None
    # arch: native height 5 rows × 58 + a 120 ring + brick = 430 px
    k = min(1.0, (y1 - y0) / 430.0)
    bw, bh, pitch, R = 100 * k, 52 * k, 58 * k, 120 * k
    cx = (x0 + x1) / 2.0
    base = y1 - bh / 2
    pts = []
    for side in (-1, 1):
        for r in range(5):
            pts.append((cx + side * R, base - r * pitch, 0))
    yc = base - 4 * pitch
    for a in range(5):
        ang = math.pi - math.pi * (a + 0.5) / 5
        pts.append((cx + R * math.cos(ang), yc - R * math.sin(ang), round(90 - math.degrees(ang), 1)))
    glow = (cx - R + bw / 2 - 6, yc - R + bh / 2 + 4, 2 * (R - bw / 2) + 12, base + bh / 2 - (yc - R + bh / 2 + 4))
    return pts, bw, bh, glow


def _brand_bricks(box, ctx):
    if not ctx or not ctx.brand:
        return None
    lg = ctx.brand.get("logo") or {}
    p = lg.get("trimmed") or lg.get("src")
    if not p:
        return None
    p = p if os.path.isabs(p) else os.path.join(ctx.root, p)
    img = _png_alpha(p)
    if not img:
        return None
    w, h, cov = img
    a = w / float(h)
    if not 0.5 <= a <= 1.6:
        return None
    x0, y0, x1, y1 = box
    for rows in (7, 6, 5):
        bh = (y1 - y0) / rows - 4
        bw = bh * 1.9
        cols = max(2, int(round(rows * a * (bh + 4) / (bw + 4))))
        pts = []
        for r in range(rows):
            for c in range(cols):
                xs = range(int(c * w / cols), max(int(c * w / cols) + 1, int((c + 1) * w / cols)), max(1, w // (cols * 6)))
                ys = range(int(r * h / rows), max(int(r * h / rows) + 1, int((r + 1) * h / rows)), max(1, h // (rows * 6)))
                vals = [cov(x, y) for x in xs for y in ys]
                if vals and sum(vals) / len(vals) >= 0.5:
                    cxp = (x0 + x1) / 2.0 + (c - (cols - 1) / 2.0) * (bw + 4)
                    pts.append((cxp, y0 + bh / 2 + r * (bh + 4), 0))
        if 6 <= len(pts) <= 42:
            pts.sort(key=lambda p: -p[1])           # build bottom-up
            return pts, bw, bh, None
    return None


def bricks(s, t, glow_t=None, shape="arch", box=None, step=0.042, t_out=None, name="bk"):
    """Bricks fall in one by one (staggered, back.out) and build a symbol ("start building"),
    with a light glowing inside it at glow_t. shape: arch | wall | brand | [(x, y, rot), ...]."""
    ctx = s.ctx
    box = box or (ctx.sky["left"], ctx.sky["top"] - 10, ctx.W - ctx.sky["right"], ctx.sky["bottom"])
    if isinstance(shape, (list, tuple)):
        pts, bw, bh, glow = [tuple(p) + (0,) * (3 - len(p)) for p in shape], 100, 52, None
    else:
        pts, bw, bh, glow = brick_points(shape, box, ctx)
    t = s.at(t)
    cid = s.uid(name)
    out = []
    for i, (x, y, rot) in enumerate(pts):
        bid = f"{cid}{i}"
        out.append(f'<i class="kt-brk" id="{bid}" style="left:{r3(x - bw / 2)}px;top:{r3(y - bh / 2)}px;'
                   f'width:{r3(bw)}px;height:{r3(bh)}px"></i>')
        s.tween("#" + bid, {"opacity": 0, "y": -320, "rotation": rot + (-1) ** i * 20},
                {"opacity": 1, "y": 0, "rotation": rot}, r3(t + step * i), 0.3, "back.out(1.6)")
    g = ""
    if glow and glow_t is not None:
        gx, gy, gw, gh = glow
        g = (f'<i class="kt-bglow" id="{cid}g" style="left:{r3(gx)}px;top:{r3(gy)}px;width:{r3(gw)}px;'
             f'height:{r3(gh)}px;border-radius:{r3(gw / 2)}px {r3(gw / 2)}px 0 0"></i>')
        s.fade(f"#{cid}g", glow_t, 0.3)
        s.sfx("ding", glow_t, "normal", 0.14)
    s.sfx("bricks", t, "exempt", 0.2)
    if t_out is not False:
        s.away("#" + cid, s.end - 0.28 if t_out is None else t_out)
    return Html(f'<div class="kt-full" id="{cid}">{"".join(out)}{g}</div>')


def glow_ring(s, t, d=None, cx=None, cy=None, r=None, name="gl"):
    """A blue glow ring + halo around the speaker ("the power passes to you")."""
    ctx = s.ctx
    fr = ctx.framing
    cx = fr["face_cx"] if cx is None else cx
    cy = (fr["face_cy"] + 70) if cy is None else cy
    r = 360 if r is None else r
    t = s.at(t)
    d = (s.end - t) if d is None else d
    hid, rid = s.uid(name + "h"), s.uid(name + "r")
    s.tween("#" + hid, {"opacity": 0, "scale": 0.6}, {"opacity": 1, "scale": 1.1}, t, 0.6, "power2.out")
    s.tween("#" + hid, {"opacity": 1}, {"opacity": 0}, max(t + 0.6, r3(t + d - 0.5)), 0.5, "power2.in")
    s.tween("#" + rid, {"opacity": 0, "scale": 0.8}, {"opacity": 0.55, "scale": 1.08}, t, 0.6, "power2.out")
    s.tween("#" + rid, {"opacity": 0.55}, {"opacity": 0}, max(t + 0.6, r3(t + d - 0.4)), 0.4, "power2.in")
    s.sfx("riser_short", t, "normal", 0.1)
    return Html(f'<i class="kt-halo" id="{hid}" data-grid="bleed" style="left:{r3(cx - 1.4 * r)}px;'
                f'top:{r3(cy - 1.7 * r)}px;width:{r3(2.8 * r)}px;height:{r3(3.4 * r)}px"></i>'
                f'<i class="kt-ringo" id="{rid}" data-grid="bleed" style="left:{r3(cx - r)}px;top:{r3(cy - 1.2 * r)}px;'
                f'width:{r3(2 * r)}px;height:{r3(2.4 * r)}px"></i>')


# ======================================================================== hook
class HookCard(Scene):
    """One card of the hook world: the dark card (header strip + your body) and its big
    gradient title under it at y≈1110. Build its body with components, then pass it to hook()."""

    def __init__(self, ctx, sid, start, end, big, head, meta=None, meta_tone="", enter=None,
                 title_t=None):
        super().__init__(ctx, sid, start, end, layer="card")
        self.big, self.head, self.meta, self.meta_tone = big, head, meta, meta_tone
        self.enter, self.title_t = enter, title_t
        self._body = []

    def add(self, *parts):
        self._body += [str(p) for p in parts]
        return self

    def finish(self, i):
        ctx = self.ctx
        enter = self.enter or ("rise", "swing", "lift")[i % 3]
        cid, tid = self.uid("card"), self.uid("t")
        # 140 px unless the title (plus its 4 % drift) would leave the safe width
        import moments
        gw = ctx.G["safe"][2] - ctx.G["safe"][0]
        w100 = moments._tw(str(self.big), 100, 800) / 100.0
        # 0.90: the width model runs a few % short on heavy Hebrew (a real title measured
        # 886 px where the model said 864) and the title drifts to 104 %
        size = int(min(140, 0.90 * gw / max(0.1, w100) / 1.04))
        fs = f' style="font-size:{size}px"' if size < 140 else ""
        self.parts = [card(self, "".join(self._body), self.head, self.meta, self.meta_tone),
                      f'<div class="kt-hbigw"><div class="kt-hbig kt-grad" id="{tid}"{fs}>'
                      f'{text(self.big, ctx)}</div></div>']
        t0 = r3(self.start + 0.05)
        if enter == "rise":
            self.rise("#" + cid, t0, 260, 0.86, 24, 0.45)
        elif enter == "swing":
            self.swing("#" + cid, t0, 600, 0.45)
        elif enter == "lift":
            self.rise("#" + cid, t0, 300, 1, 20, 0.45)
        else:
            raise KitError(f"kit: hook card {self.id}: enter {enter!r} — rise | swing | lift")
        # spec §4.4: the title fades up 0.3 s after its card (title_t may pin it to a word)
        tt = t0 + 0.3 if self.title_t is None else self.at(self.title_t)
        tt = max(t0 + 0.3, min(tt, self.end - 0.4))
        self.tween("#" + tid, {"opacity": 0, "y": 40}, {"opacity": 1, "y": 0}, tt, 0.3, "expo.out")
        self.drift("#" + cid, r3(t0 + 0.45), self.end)
        self.drift("#" + tid, r3(tt + 0.3), self.end)
        return self.done()


def hook_card(ctx, sid, start, end, big, head, meta=None, meta_tone="", enter=None, title_t=None):
    return HookCard(ctx, sid, start, end, big, head, meta, meta_tone, enter, title_t)


def hook(ctx, cards, out, back, intro=None, intro_start=0.0, sid="hook", land=None, size=104):
    """The hook world (spec §4.4, exact). The opening words land as a thin headline on the
    chest; at `out` the frame flies away (scale .34, y −900, radius 60, blur 14, 0.32 s
    power3.in) into a dark brand world with ambient drift; 2-3 cards illustrate the first
    lines; at `back` the frame returns (scale .4, y −700 → the punch scale at that moment,
    0.38 s power3.out) through a blue screen tint fading .9 → 0. Captions are hidden from the
    first word to the landing. Returns a list of fragments."""
    out, back = r3(ctx.at(out)), r3(ctx.at(back))
    if not cards or not 1 <= len(cards) <= 4:
        raise KitError("kit: hook needs 2-3 cards (spec §3.1)")
    if back - out > 5.2:
        ctx.note(f"hook: the speaker is off screen {back - out:.2f}s (spec: at most ~5 s)")
    landing = r3(back + 0.38)
    frags = []
    # 1. the thin opener on the chest
    if intro:
        si = Scene(ctx, sid + "-in", intro_start, out, layer="front")
        si.add(stack(si, intro, size=size))
        frags.append(si.done())
    # 2. the world + the camera fly-out / return
    sw = Scene(ctx, sid + "-world", r3(out - 0.02), r3(back + 0.6), layer="world")
    wid = sw.uid("bg")
    sw.add(f'<div class="kt-world" id="{wid}" data-grid="bleed"></div>')
    sw.fade("#" + wid, out, 0.25)
    sw.drift("#" + wid, out, back, 1, 1.12)
    sw.fade("#" + wid, r3(back + 0.35), 0.2, 1, 0)
    for t, kind, _ in ctx.scale_events():
        if out + 1e-6 < t < landing - 1e-6:
            raise KitError(f"kit: hook: a punch-in/beat step at {t:.2f}s lands inside the hook "
                           f"({out:.2f}-{landing:.2f}s) — the hook controls the camera there; "
                           f"move it to ≥ {landing:.2f}s (spec §5)")
    s0, y0 = ctx.state_at(r3(out - 0.001))
    if land is not None:
        sE, yE = float(land), ctx.state_at(landing)[1]
    else:
        sE, yE = ctx.state_at(landing)
    k_o, k_b = ctx.origin_shift(0.34), ctx.origin_shift(0.4)
    F = ctx.foot
    sw._c("ft", F, {"scale": r3(s0), "y": r3(y0), "filter": "blur(0px)", "clipPath": "inset(0px round 0px)"},
          {"scale": 0.34, "y": r3(-900 + k_o + y0), "filter": "blur(14px)",
           "clipPath": f"inset(0px round {r3(60 / 0.34)}px)", "duration": 0.32, "ease": "power3.in"}, out)
    sw.set(F, {"opacity": 0}, r3(out + 0.32))
    sw.set(F, {"opacity": 1}, back)
    sw._c("ft", F, {"scale": 0.4, "y": r3(-700 + k_b + yE), "filter": "blur(16px)",
                    "clipPath": f"inset(0px round {r3(60 / 0.4)}px)"},
          {"scale": r3(sE), "y": r3(yE), "filter": "blur(0px)", "clipPath": "inset(0px round 0px)",
           "duration": 0.38, "ease": "power3.out"}, back)
    sw.set(F, {"clipPath": "inset(0px 0px 0px 0px)", "filter": "none"}, landing)
    sw._footage(out, landing)
    sw.hide(intro_start if intro else out, landing)
    sw.sfx("whoosh_impact", out, "exempt", 0.32)
    sw.sfx("whoosh_impact", back, "exempt", 0.24)
    frags.append(sw.done())
    # 3. the cards
    last = None
    for i, c in enumerate(cards):
        if not isinstance(c, HookCard):
            raise KitError("kit: hook cards must come from kit.hook_card(...)")
        if c.start < out - 0.05:
            raise KitError(f"kit: hook card {c.id} starts {c.start}s, before the fly-out {out}s")
        if last is not None and c.start > last.end + 0.05:
            ctx.note(f"hook: gap between cards {last.id} and {c.id} ({last.end}-{c.start}s) — the world sits empty")
        if i == 0:
            c.sfx("swap_pop", c.start + 0.05, "normal", 0.18)
        else:
            c.sfx("soft_whoosh", c.start, "exempt", 0.2)
        frags.append(c.finish(i))
        last = c
    if last.end < back - 0.05:
        ctx.note(f"hook: last card {last.id} ends {last.end}s, before the return {back}s")
    # 4. the blue screen tint over the returning frame
    st = Scene(ctx, sid + "-tint", back, r3(back + 0.6), layer="tint")
    tid = st.uid("t")
    st.add(f'<div class="kt-tint" id="{tid}" data-grid="bleed"></div>')
    st.fade("#" + tid, r3(back + 0.05), 0.5, 0.9, 0)
    frags.append(st.done())
    return frags


# ====================================================================== recipes
# Full scenes composed from the components: the quick path for media.json "scenes" and a
# readable example of how to compose. Times may be seconds or spoken words ("לחכות@2").
def _w(ctx, sid, start, end):
    return Scene(ctx, sid, start, end)


def r_week(ctx, id, start, end, title, hit=4, days=None, pulse_t=None, icon_name="calendar"):
    s = _w(ctx, id, start, end)
    s.add(widget(s, week(s, hit, None, 0.13, pulse_t, days), eyebrow=title, eyebrow_icon=icon_name))
    s.sfx("clock", s.start + 0.05, "normal", 0.2)
    return s.done()


def r_phone(ctx, id, start, end, title, sub=None):
    s = _w(ctx, id, start, end)
    s.add(widget(s, phone(s, title, sub), enter="slide", sfx="swap_pop"))
    return s.done()


def r_inbox(ctx, id, start, end, title, empty_title, empty_sub=None, icon_name="inbox"):
    s = _w(ctx, id, start, end)
    s.add(widget(s, empty(s, empty_title, empty_sub, icon_name, 120), title=title,
                 aside=spinner(s)))
    return s.done()


def r_waiting_room(ctx, id, start, end, title, wait, fail, fail_t, leave=None, leave_t=None,
                   press_t=None, host="?"):
    s = _w(ctx, id, start, end)
    s.add(widget(s, waiting_room(s, title, wait, fail, fail_t, leave, leave_t, press_t, host)))
    return s.done()


def r_dialog(ctx, id, start, end, title, wait, done, no, yes, yes_done, show_t, tap_t):
    s = _w(ctx, id, start, end)
    s.add(widget(s, dialog(s, title, wait, done, no, yes, yes_done, show_t, tap_t)))
    return s.done()


def r_task(ctx, id, start, end, title, flip_t, who, status, label, pulse_t=None):
    s = _w(ctx, id, start, end)
    s.add(widget(s, task(s, title, flip_t, who, status, label, pulse_t)))
    return s.done()


def r_calendar(ctx, id, start, end, title, event, labels=None, moves=(), fly_t=None, never=None,
               never_t=None):
    s = _w(ctx, id, start, end)
    s.add(widget(s, calendar(s, event, labels, moves, fly_t=fly_t, never=never, never_t=never_t),
                 eyebrow=title, eyebrow_icon="calendar"))
    return s.done()


def r_today(ctx, id, start, end, label, ring_t, big=None):
    s = _w(ctx, id, start, end)
    s.add(today(s, label, ring_t, big))
    return s.done()


def r_notify(ctx, id, start, end, app, title, body=None, meta=None, icon_name="bell"):
    s = _w(ctx, id, start, end)
    s.add(widget(s, notify(s, app, title, body, meta, icon_name), sfx="comment_ping"))
    return s.done()


def r_chat(ctx, id, start, end, msgs, typing=None):
    s = _w(ctx, id, start, end)
    s.add(widget(s, chat(s, msgs, typing), sfx=None))
    return s.done()


def r_strike(ctx, id, start, end, items, top=None):
    s = _w(ctx, id, start, end)
    s.add(strike_pills(s, items, top))
    cid = s.uid("pills")
    s.parts[-1] = s.parts[-1].replace('<div class="kt-pills"', f'<div class="kt-pills" id="{cid}"', 1)
    s.away("#" + cid, s.end - 0.28)
    return s.done()


def r_percent(ctx, id, start, end, values=None, count=None, top=None):
    s = _w(ctx, id, start, end)
    s.add(percent(s, values, count, top))
    s.hide()
    return s.done()


def r_streak(ctx, id, start, end, t=None, **kw):
    s = _w(ctx, id, start, end)
    s.add(streak(s, s.start if t is None else t, **kw))
    return s.done()


def r_bars(ctx, id, start, end, slam_t=None, lift_t=None, fade_t=None, burst_t=None):
    s = _w(ctx, id, start, end)
    s.add(bars(s, slam_t, lift_t, fade_t, burst_t))
    return s.done()


def r_puppet(ctx, id, start, end, drop_t=None, sway_t=None, snap_t=None):
    s = _w(ctx, id, start, end)
    s.add(puppet(s, s.start if drop_t is None else drop_t, sway_t, 5, snap_t))
    return s.done()


def r_leak(ctx, id, start, end, t=None):
    s = _w(ctx, id, start, end)
    s.add(light_leak(s, s.start if t is None else t, s.end - (s.start if t is None else ctx.at(t))))
    return s.done()


def r_bricks(ctx, id, start, end, glow_t=None, shape="arch"):
    s = _w(ctx, id, start, end)
    s.add(bricks(s, s.start, glow_t, shape))
    return s.done()


def r_stamp(ctx, id, start, end, label, t=None, x=None, y=None, rot=-8):
    s = _w(ctx, id, start, end)
    s.add(stamp(s, label, s.start if t is None else t, x, y, rot))
    return s.done()


def r_glow(ctx, id, start, end, push=True):
    s = _w(ctx, id, start, end)
    s.add(glow_ring(s, s.start))
    if push:
        s.cam_push(s.start)
    return s.done()


RECIPES = {"week": r_week, "phone": r_phone, "inbox": r_inbox, "waiting_room": r_waiting_room,
           "dialog": r_dialog, "task": r_task, "calendar": r_calendar, "today": r_today,
           "notify": r_notify, "chat": r_chat, "strike": r_strike, "percent": r_percent,
           "streak": r_streak, "bars": r_bars, "puppet": r_puppet, "leak": r_leak,
           "bricks": r_bricks, "stamp": r_stamp, "glow": r_glow}


def recipe(ctx, name, **kw):
    if name not in RECIPES:
        raise KitError(f"kit: unknown recipe {name!r} — have: {', '.join(sorted(RECIPES))}")
    try:
        return RECIPES[name](ctx, **kw)
    except TypeError as e:
        raise KitError(f"kit: recipe {name} ({kw.get('id')}): {e}")


# ========================================================================= CSS
HEAVY_CLASSES = ("kt-glass", "kt-world", "kt-leak", "kt-halo", "kt-bglow", "kt-hand",
                 "kt-streak")


def heavy_count(htmls):
    """Elements carrying blur/backdrop-filter/radial-gradient/clip-path (spec §9.6: above ~40
    the capture renders black). Counted on the markup, hidden ones included."""
    n = 0
    for h in htmls:
        for m in re.finditer(r'class="([^"]*)"', h):
            if any(c in m.group(1).split() for c in HEAVY_CLASSES):
                n += 1
        n += len(re.findall(r'style="[^"]*(?:radial-gradient|blur\(|clip-path)', h))
    return n


def css(ctx):
    """The kit stylesheet (templates/kit/*.css), with the grid geometry substituted."""
    G = ctx.G
    gx0, gy0, gx1, gy1 = G["safe"]
    rtl = ctx.dir == "rtl"
    kw = dict(gx0=gx0, gw=gx1 - gx0, cx=G["center_x"], dir=ctx.dir,
              sky_l=ctx.sky["left"], sky_r=ctx.sky["right"], sky_t=ctx.sky["top"],
              card_t=ctx.sky["top"] + 80, card_h=690,
              fill_origin="100% 50%" if rtl else "0% 50%",
              strike_origin="100% 50%" if rtl else "0% 50%",
              hbig_t=round(G["safe"][1] + 890))
    out = [tokens_css(ctx.tokens)]
    for name in ("kit", "hook", "overlays"):
        p = os.path.join(TEMPLATE_DIR, name + ".css")
        out.append(string.Template(open(p, encoding="utf-8").read()).substitute(kw))
    return "\n".join(out)


def catalogue():
    """(name, first docstring line) for every public component — `scenes.py list`."""
    names = ["widget", "panel", "card", "swap", "pill", "spinner", "empty", "progress", "avatars",
             "week", "phone", "calendar", "today", "hand", "dialog", "task", "waiting_room",
             "notify", "chat", "strike_pills", "stamp", "percent", "stack", "sparks", "bars",
             "puppet", "light_leak", "streak", "bricks", "glow_ring", "hook_card", "hook", "icon"]
    mod = sys.modules[__name__]
    out = []
    for n in names:
        doc = (getattr(mod, n).__doc__ or "").strip().split("\n")[0]
        out.append((n, doc))
    return out
