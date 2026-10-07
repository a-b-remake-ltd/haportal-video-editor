#!/usr/bin/env python3
"""Designed moments — hand-built premium reel devices, generalised and brand-coloured.

WHY THIS EXISTS. The strongest talking-head reels (premium agency work, the Rollin-style
interview, a platform intro) all have a few DESIGNED MOMENTS: the page turns to paper on a
big idea, a viewer's question types itself onto a comment card, a checklist ticks off as the
benefits are named, a stamp slams on the claim, the search bar types what he says, the frame
flies away into a brand world on the hook. Each was hand-coded per project with hard-coded
gold and navy. This module rebuilds the SAME choreography from a media.json entry, coloured
only through the brand tokens, placed inside the Reels grid, synced to the spoken words.

What it returns is FRAGMENTS, exactly like scripts/outro.py: element specs, CSS, timeline
lines, SFX cues, caption-hide windows and transition names. build_index.py emits them through
its own clip() so every timed value still lands in build/expected.json and the END guard.
It never writes index.html itself (except the standalone `preview` test bench).

The footage rule (HyperFrames): a <video> nested inside a timed element FREEZES. So the moments
that move the speaker (paper's page turn, fly, punch) animate the A-roll element ITSELF — the
same approach outro.py uses for its shrink — and always return it to the beat map's state, so
the hand-back is seamless. Every tween is a fromTo with immediateRender:false whose FROM state
equals what was on screen before it (seek-safe in any order), and every animated element gets
a hard-kill tl.set at its out-point.

Types (see `moments.py list` and references/moments.md):
  paper      the paper world with the page turn in and out; a statement and/or a sheet
  question   a viewer's comment card, words revealed as spoken
  checklist  the bottom card, rows ticking on the spoken words
  stamp      a stamp slam on a claim
  chips      2-4 pills popping on the words that name them
  searchbar  a search pill typing the query in sync with the speech
  fly        the Rollin hook: the frame shrinks into a card and flies into a brand world
  punch      punch-in steps on the speaker

Usage
  python3 scripts/moments.py list
  python3 scripts/moments.py preview <type> [--entry '{json}' | --media media.json --id m1]
  python3 scripts/moments.py selftest          # the cue-search tests
          [--pad 1.2] [--out build/moments_preview/<type>] [--render] [--sheet 0.12]
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import string
import sys
import unicodedata
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402
import grid  # noqa: E402

TEMPLATE_DIR = os.path.join(hfcfg.SKILL_DIR, "templates", "moments")
TYPES = ("paper", "question", "checklist", "stamp", "chips", "searchbar", "fly", "punch")
# Moments that drive the A-roll element itself. Two of them can never overlap: they would
# fight over the same transform / clip-path.
FOOTAGE_TYPES = ("paper", "fly")

# SFX levels: how far under the VOICE each cue lands (references/sound.md: ~7 dB under the
# voice on a transition; quieter for small UI ticks that repeat).
BELOW = {"page": 8.0, "whoosh": 8.0, "rush": 8.0, "flight": 7.0, "slam": 6.0, "pop": 10.0,
         "tick": 11.0}

r3 = lambda v: round(float(v), 3)


# ------------------------------------------------------------------ the contract
FIELDS = {
    "paper": {
        "lines": "statement: str with '/' line breaks, *keyword* in brand colour, _word_ bold; or a list of lines",
        "kicker": "optional small eyebrow above the statement",
        "sheet": "optional {title, rows:[str | {text, cue}]} white sheet with check rows",
        "question": "optional {who, initials, text} comment card on the page",
        "size": "optional statement font px (auto-fitted otherwise)",
        "turn": "page-turn duration, default 0.5 s (in at start, out ending at end)",
        "watermark": "false to drop the logo watermark, or an image path",
        "watermark_crop": "[x0,y0,x1,y1] in logo px — cut the MARK out of a wordmark",
        "watermark_size": "px of the crop's long side on screen (default 1150)",
    },
    "question": {
        "who": "the viewer's name (or a label: שאלה מהתגובות)",
        "initials": "1-2 letters for the avatar",
        "text": "the question, revealed word by word as spoken",
        "meta": "optional grey note after the name (e.g. עכשיו)",
    },
    "checklist": {
        "title": "small title line of the card",
        "rows": "2-4 rows: str (auto-cued on its first word) or {text, cue}",
        "look": "paper (default) | dark",
    },
    "stamp": {
        "text": "the stamp text (short: 1-3 words)",
        "sub": "optional small second line",
        "color": "brand (default) | warn",
        "on": "footage (default, paper-backed plate) | paper (ink only)",
        "cue": "a spoken word (lands as it begins) or seconds; default = start + 0.17",
        "land": "start (default) | end — land after the cue word instead",
        "rotate": "degrees, default -6",
        "at": "[x, y] centre, default [500, 400]; or just y",
        "size": "font px, default 108",
    },
    "chips": {
        "items": "2-4 of str or {text, cue, accent}",
        "at": "top (y 250, default) | low (y 1250) | y px",
    },
    "searchbar": {
        "query": "the typed text — the speaker's words",
        "world": "gradient (default: a brand world that dissolves to footage) | footage",
        "y": "pill top on the gradient (default 420)",
        "y_footage": "pill top once on footage (default 250)",
        "dissolve": "seconds the world dissolves (default 55% through)",
    },
    "fly": {
        "lines": "optional big statement in the world (same markup as paper)",
        "cards": "optional floating cards: str | {text} | {img: path}",
        "card_scale": "the footage card's scale before it flies, default 0.6",
        "y": "statement centre y, default 860",
    },
    "punch": {
        "steps": "[[t, factor], ...] hard punch-in steps (factor × the beat's scale)",
        "scale": "or one factor for the whole window (in at start, out at end)",
    },
}

EXAMPLES = {
    "paper": {"id": "m1", "type": "paper", "start": 25.80, "end": 33.90,
              "kicker": "המדריך המלא", "lines": "שלושה *צעדים* / *לפני* שמתחילים",
              "watermark_crop": [165, 15, 348, 230]},
    "question": {"id": "m2", "type": "question", "start": 38.30, "end": 42.50,
                 "who": "שאלה מהתגובות", "initials": "?",
                 "text": "כמה זמן לוקח לראות תוצאות?"},
    "checklist": {"id": "m3", "type": "checklist", "start": 54.10, "end": 56.90,
                  "title": "מה צריך", "rows": ["בלי ניסיון קודם", "נגיש לכולם"]},
    "stamp": {"id": "m4", "type": "stamp", "start": 9.50, "end": 10.75, "text": "חינם",
              "sub": "בלי כרטיס אשראי", "color": "brand", "cue": "חינם"},
    "chips": {"id": "m5", "type": "chips", "start": 24.00, "end": 25.80,
              "items": [{"text": "אינסטגרם", "cue": "באינסטגרם,"}, "טיקטוק",
                        {"text": "וואטסאפ", "cue": "ובוואטסאפ", "accent": True}]},
    "searchbar": {"id": "m6", "type": "searchbar", "start": 35.30, "end": 38.30,
                  "query": "איך מגדילים מכירות באינסטגרם"},
    "fly": {"id": "m7", "type": "fly", "start": 2.40, "end": 6.70,
            "lines": "עברנו את / *1,000* לקוחות", "cards": ["אינסטגרם", "טיקטוק", "יוטיוב"]},
    "punch": {"id": "m8", "type": "punch", "start": 7.29, "end": 10.79,
              "steps": [[7.29, 1.12], [9.04, 1.0], [9.79, 1.14]]},
}


# ------------------------------------------------------------------ small helpers
def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _js(s):
    return json.dumps(str(s), ensure_ascii=False)


_LATIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+#%/:'’\-]*")


def _word_html(w):
    """One display word with every Latin/number run ISOLATED (bidi would reorder the line
    otherwise) and every all-caps acronym containing an I slabbed (Heebo 800 reads "AI" as
    "Al")."""
    out, i = [], 0
    for m in _LATIN.finditer(w):
        out.append(_esc(w[i:m.start()]))
        tok = m.group(0)
        cls = "m-ltr"
        core = re.sub(r"[^A-Za-z]", "", tok)
        if core and core.isupper() and "I" in core and len(core) <= 5:
            cls += " m-ai"
        out.append(f'<span class="{cls}">{_esc(tok)}</span>')
        i = m.end()
    out.append(_esc(w[i:]))
    return "".join(out)


def _has_latin(w):
    return bool(re.search(r"[A-Za-z]", w))


def _norm(w):
    """Comparable form of a word: no niqqud, no punctuation, no hyphen/maqaf, lower case."""
    w = unicodedata.normalize("NFD", str(w))
    w = "".join(c for c in w if not unicodedata.combining(c))
    w = re.sub(r"[^\w]", "", w, flags=re.UNICODE).replace("_", "")
    return w.lower()


_PREFIX = "והבלמשכ"


def _variants(n):
    """Hebrew glues prepositions onto words (ב/ל/ו/ה/מ/ש/כ): "בפייסבוק" is "פייסבוק" said
    with "on". Strip up to two leading prefix letters so a chip labelled "פייסבוק" still finds
    the spoken "בפייסבוק"."""
    v = {n}
    cur = n
    for _ in range(2):
        if len(cur) > 3 and cur[0] in _PREFIX:
            cur = cur[1:]
            v.add(cur)
    return v


def _same(a, b):
    if not a or not b:
        return False
    return a == b or bool(_variants(a) & _variants(b))


def _words(ctx):
    out = []
    for w in ctx.get("words") or []:
        if isinstance(w, dict):
            out.append((float(w.get("s", w.get("start", 0))), float(w.get("e", w.get("end", 0))),
                        str(w.get("w", w.get("word", "")))))
        else:
            out.append((float(w[0]), float(w[1]), str(w[2])))
    return out


def sync(tokens, ctx, t0, t1):
    """Spoken (start, end) for each display token, searched IN ORDER inside [t0, t1].

    The transcript splits some words differently from how a graphic writes them ("מ" +
    "-אפס" is "מאפס"), so a token may also match two consecutive spoken words. A token that
    is not spoken (a label, a paraphrase) is interpolated between its matched neighbours, so
    the reveal still follows the speech rhythm. Returns (times, matched_count)."""
    sp = [(s, e, _norm(w)) for s, e, w in _words(ctx) if t0 - 0.3 <= s <= t1 + 0.05]
    out = [None] * len(tokens)
    j = 0
    for i, tok in enumerate(tokens):
        n = _norm(tok)
        if not n:
            continue
        for k in range(j, min(len(sp), j + SYNC_LOOKAHEAD)):
            if _same(n, sp[k][2]):
                out[i] = (sp[k][0], sp[k][1])
                j = k + 1
                break
            if k + 1 < len(sp) and _same(n, sp[k][2] + sp[k + 1][2]):
                out[i] = (sp[k][0], sp[k + 1][1])
                j = k + 2
                break
    matched = sum(1 for x in out if x)
    # interpolate the gaps between anchors
    n = len(tokens)
    idx = [i for i in range(n) if out[i]]
    lo, hi = t0, max(t0 + 0.2, t1 - 0.25)
    if not idx:
        step = (hi - lo) / max(1, n)
        return [(lo + i * step, lo + (i + 1) * step) for i in range(n)], 0
    res = list(out)
    bounds = [(-1, lo)] + [(i, out[i][0]) for i in idx] + [(n, hi)]
    for (a, ta), (b, tb) in zip(bounds, bounds[1:]):
        gap = b - a - 1
        if gap <= 0:
            continue
        start = out[a][1] if a >= 0 else ta
        start = min(start, tb)
        step = (tb - start) / (gap + 1) if b < n else (tb - start) / max(1, gap)
        for g in range(gap):
            s = start + g * step if b == n else start + (g + 1) * step - step * 0.5
            res[a + 1 + g] = (s, s + max(0.12, step * 0.9))
    # monotonic
    prev = -1e9
    for i in range(n):
        s, e = res[i]
        s = max(s, prev)
        res[i] = (s, max(e, s + 0.05))
        prev = s
    return res, matched


SYNC_LOOKAHEAD = 9     # sync(): how far past the previous match the NEXT display token may be


def find_phrase(tokens, ctx, t0, t1, look=SYNC_LOOKAHEAD):
    """The spoken span (start, end) of a cue phrase, searched over the WHOLE window
    [t0 − 0.3, t1 + 0.05]: the first word may sit anywhere in it (the nearest occurrence
    after the window start wins), and each following word must come within `look` spoken
    words of the one before. Prefix-tolerant the same way as sync() ("פייסבוק" finds the
    spoken "בפייסבוק"), and a token may match two spoken words the transcript split.

    WHY not sync(): sync() walks display tokens in order with a lookahead of 9 spoken words
    from the previous match — right for revealing a title word by word, wrong for a cue.
    Starting from the window's first word, a cue word said as word 10 or later of a long
    window was simply "not spoken" and the moment fell back to its start.
    Returns (start, end) or None."""
    sp = [(s, e, _norm(w)) for s, e, w in _words(ctx) if t0 - 0.3 <= s <= t1 + 0.05]
    ns = [_norm(t) for t in tokens]
    ns = [n for n in ns if n]
    if not ns or not sp:
        return None

    def match_at(k, n):
        """Spoken words consumed by token n at index k: 1, 2 (a split word) or 0."""
        if _same(n, sp[k][2]):
            return 1
        if k + 1 < len(sp) and _same(n, sp[k][2] + sp[k + 1][2]):
            return 2
        return 0

    first_only = None
    for k0 in range(len(sp)):
        used = match_at(k0, ns[0])
        if not used:
            continue
        span = [sp[k0][0], sp[k0 + used - 1][1]]
        if first_only is None:
            first_only = tuple(span)
        j, ok = k0 + used, True
        for n in ns[1:]:
            hit = None
            for k in range(j, min(len(sp), j + look)):
                u = match_at(k, n)
                if u:
                    hit = (k, u)
                    break
            if not hit:
                ok = False
                break
            span[1] = sp[hit[0] + hit[1] - 1][1]
            j = hit[0] + hit[1]
        if ok:
            return tuple(span)
    # the whole phrase is not there in order: land on its first word rather than nowhere
    return first_only


def cue_time(cue, ctx, t0, t1, edge="start"):
    """A cue as seconds: a number is a time; a string is a spoken word (or phrase) searched
    over the WHOLE window [t0, t1] (find_phrase) — its start, or its END for things that
    should land after the word, like a stamp."""
    if cue is None:
        return None
    if isinstance(cue, (int, float)):
        return float(cue)
    span = find_phrase(str(cue).split(), ctx, t0, t1)
    if not span:
        print(f"  moments: ! cue word {cue!r} not spoken between {t0:.2f} and {t1:.2f}s — "
              f"falling back to the window")
        return None
    return span[0] if edge == "start" else span[1]


def selftest():
    """Negative + positive tests for the cue search — `moments.py selftest`, exit 1 on a
    failure. The bug it guards: a cue word late in a long window was "not spoken"."""
    words = [[i * 0.4, i * 0.4 + 0.3, w] for i, w in enumerate(
        "אז היום אני רוצה לספר לכם על משהו שקרה לי השבוע כשניסיתי לבנות אתר עם "
        "כלי חדש ובסוף זה עבד בפייסבוק וגם ובאינסטגרם בלי לשלם שקל אחד חינם".split())]
    ctx = {"words": words}
    fails = []

    def expect(cond, what):
        print(f"  {'✓' if cond else '✗'} {what}")
        if not cond:
            fails.append(what)

    i20 = 20                                   # "בפייסבוק" is word 20 of the window
    expect(words[i20][2] == "בפייסבוק", "fixture: the cue sits at word 20")
    t = cue_time("פייסבוק", ctx, 0.0, 12.0)
    expect(t is not None and abs(t - words[i20][0]) < 1e-6,
           f"cue at word 20 is found, prefix-tolerant (ב+פייסבוק) → {t}")
    t = cue_time("חינם", ctx, 0.0, 12.0, edge="end")
    expect(t is not None and abs(t - words[-1][1]) < 1e-6, f"last word, edge=end → {t}")
    t = cue_time("אינסטגרם בלי", ctx, 0.0, 12.0)
    expect(t is not None and abs(t - words[22][0]) < 1e-6,
           f"two-word phrase late in the window (ו+ב+אינסטגרם, word 22) → {t}")
    t = cue_time("טיקטוק", ctx, 0.0, 12.0)
    expect(t is None, "a word that is not spoken is still reported as not spoken")
    t = cue_time("אתר", ctx, 6.0, 12.0)
    expect(t is None, "a word spoken BEFORE the window is not taken")
    w2 = words + [[12.4, 12.7, "בפייסבוק"]]
    t = cue_time("פייסבוק", {"words": w2}, 7.0, 13.0)
    expect(t is not None and abs(t - words[i20][0]) < 1e-6,
           "two occurrences: the nearest after the window start wins")
    print(f"  {'all passed' if not fails else str(len(fails)) + ' FAILED'}")
    return 1 if fails else 0


# ------------------------------------------------------------------- text width
def _tw(text, fs, weight=800):
    """Rough rendered width of one line. Heebo Hebrew averages ~0.56 em bold, ~0.5 thin;
    deliberately generous — the grid gate is the real check."""
    k = 0.0
    hb = 0.50 if weight <= 400 else 0.53 if weight <= 500 else 0.57
    for c in str(text):
        if c == " ":
            k += 0.26
        elif c.isdigit():
            k += 0.58
        elif "a" <= c.lower() <= "z":
            k += 0.70 if c.isupper() else 0.56
        elif "֐" <= c <= "׿":
            k += hb
        else:
            k += 0.32
    return k * fs


# ------------------------------------------------------------------ statement markup
_MARK = re.compile(r"(\*[^*]+\*|_[^_]+_)")


def parse_lines(lines, plain="m-t"):
    """'/' separates lines, *keyword* is the brand colour, _word_ bold; plain words thin.
    Returns [[(word, cls), ...], ...]."""
    if isinstance(lines, str):
        lines = [x.strip() for x in lines.split("/")]
    out = []
    for ln in lines or []:
        row = []
        for seg in _MARK.split(str(ln)):
            if not seg.strip():
                continue
            if seg.startswith("*") and seg.endswith("*"):
                cls, seg = "m-k", seg[1:-1]
            elif seg.startswith("_") and seg.endswith("_"):
                cls, seg = "m-b", seg[1:-1]
            else:
                cls = plain
            row += [(w, cls) for w in seg.split()]
        if row:
            out.append(row)
    return out


def fit_size(rows, width, max_fs, min_fs=48):
    fs = max_fs
    for row in rows:
        txt = " ".join(w for w, _ in row)
        bold = any(c != "m-t" for _, c in row)
        w = _tw(txt, 100, 800 if bold else 300) / 100.0
        if w > 0:
            fs = min(fs, width / w)
    return int(max(min_fs, fs))


# ---------------------------------------------------------------------- builder
class _B:
    """Accumulates one moment's fragments."""

    def __init__(self, entry, ctx):
        self.e = entry
        self.ctx = ctx
        self.id = entry["id"]
        self.T = r3(entry["start"])
        self.E = r3(entry["end"])
        self.elements, self.js, self.sfx = [], [], []
        self.hide, self.transitions, self.notes = [], [], []
        self.css = ""

    def el(self, tag, eid, cls, start, dur, inner="", extra=""):
        self.elements.append(dict(tag=tag, id=eid, cls=cls, start=r3(start), dur=r3(dur),
                                  inner=inner, extra=extra))

    def line(self, s):
        self.js.append("      " + s)

    def word_in(self, sel, t, dy=14):
        self.line(f'tl.fromTo("{sel}", {{ opacity: 0, y: {dy}, filter: "blur(6px)" }}, '
                  f'{{ opacity: 1, y: 0, filter: "blur(0px)", duration: 0.24, ease: "power2.out", '
                  f'immediateRender: false }}, {r3(t)});')

    def up(self, sel, t, d=0.5, dy=60):
        self.line(f'tl.fromTo("{sel}", {{ opacity: 0, y: {dy}, filter: "blur(10px)" }}, '
                  f'{{ opacity: 1, y: 0, filter: "blur(0px)", duration: {d}, ease: "expo.out", '
                  f'immediateRender: false }}, {r3(t)});')

    def kill(self, sel, t):
        self.line(f'tl.set("{sel}", {{ opacity: 0 }}, {r3(t)});')

    def cue(self, name, t, below, on_word_ok=False, tag=""):
        c = _sfx(self.ctx, name, t, below, on_word_ok, f"{self.id}-sfx{len(self.sfx)}{tag}")
        if c:
            self.sfx.append(c)

    def out(self):
        return dict(elements=self.elements, css=self.css, timeline=self.js, sfx=self.sfx,
                    hide_captions=self.hide, transitions=self.transitions, notes=self.notes)


def _foot(ctx):
    return ctx.get("foot") or '"#aroll"'


def _state(ctx, t):
    """(scale, y) of the A-roll at t — the beat map's state times any active punch."""
    s, y = ctx["state_at"](t)
    return s * _punch_factor(ctx, t), y


def _punch_factor(ctx, t):
    f = 1.0
    for a, b, steps in ctx.get("_punch", []):
        if a - 1e-6 <= t < b - 1e-6:
            for ts, k in steps:
                if t >= ts - 1e-6:
                    f = k
    return f


def _template(name, ctx, **kw):
    G = ctx["G"]
    gx0, gy0, gx1, gy1 = G["safe"]
    base = dict(W=ctx["W"], H=ctx["H"], gx0=gx0, gy0=gy0, gx1=gx1, gy1=gy1, gw=gx1 - gx0,
                gh=gy1 - gy0, cx=G["center_x"], dir=ctx["dir"],
                bcmax=G["bottom_card_max_h"], bottom=ctx["H"] - gy1,
                # question card / search pill: the safe zone less 30 px of air each side;
                # the comment card sits 40 px above the grid's bottom line
                qx=gx0 + 30, qw=gx1 - gx0 - 60, qbottom=ctx["H"] - gy1 + 40,
                px=gx0 + 30, pw=gx1 - gx0 - 60, col_ox=G["center_x"] - gx0)
    base.update(kw)
    return string.Template(open(os.path.join(TEMPLATE_DIR, name + ".css"),
                                encoding="utf-8").read()).substitute(base)


def _css_once(ctx, *names, **kw):
    done = ctx.setdefault("_css_done", set())
    out = []
    for n in ("common",) + names:
        if n in done:
            continue
        done.add(n)
        out.append(_template(n, ctx, **kw))
    return "\n".join(out)


# -------------------------------------------------------------------------- sfx
_PEAK = {}


def _sfx(ctx, name, t, below, on_word_ok, cid):
    """A cue from assets/sfx, levelled against THIS speaker and kept OFF the words.

    Level: vol = 10^((voice_ref − below − cue_peak)/20), the outro's loudness method.
    Placement (references/sound.md): an effect must never sit on a word — try a hair earlier,
    then the word's end when it is within 0.35 s, else duck it to 55 %. A pop that NAMES a
    word (a chip) may sit on it (`on_word_ok`), quieter."""
    import outro
    root = ctx.get("root", ".")
    sfx_dir = ctx.get("sfx_dir", "assets/sfx")
    rel = f"{sfx_dir}/{name}.wav"
    full = os.path.join(root, rel)
    if not os.path.exists(full):
        os.makedirs(os.path.dirname(full), exist_ok=True)
        if name.startswith("outro_"):
            outro.synth_sfx(os.path.join(root, sfx_dir))
        else:
            src = os.path.join(hfcfg.SKILL_DIR, "assets", "sfx", name + ".wav")
            if os.path.exists(src):
                shutil.copy2(src, full)
    if not os.path.exists(full):
        print(f"  moments: ! sfx {rel} missing — run scripts/setup_assets.py; cue skipped")
        return None
    vol_k = 1.0
    if not on_word_ok:
        t, ducked = place_off_words(t, _words(ctx), bounds=ctx.get("bounds") or ())
        if ducked:
            vol_k = 0.55
            ctx.setdefault("_ducked", []).append(f"{cid} {name} at {t:.2f}s")
    try:
        with wave.open(full) as wv:
            dur = wv.getnframes() / float(wv.getframerate())
    except Exception:
        dur = 1.0
    end = ctx.get("end") or (t + dur)
    dur = min(dur, end - t)
    if dur <= 0.05 or t < 0:
        return None
    vref = _voice_ref(ctx)
    vol = 0.35
    if vref is not None:
        if full not in _PEAK:
            m = outro.momentary(full)
            _PEAK[full] = max(m) if m else None
        if _PEAK[full] is not None:
            vol = 10 ** ((vref - below - _PEAK[full]) / 20.0)
    if on_word_ok:
        vol *= 0.8
    vol = round(max(0.02, min(1.0, vol * vol_k)), 3)
    return {"id": cid, "src": rel, "start": r3(t), "duration": r3(dur), "volume": vol}


def place_off_words(t, ws, before=0.25, after=0.35, min_gap=0.06, bounds=()):
    """Where a cue may start so its attack is not on a word (references/sound.md).

    Connected speech often has NO gap at a word's end (the next word starts on the same
    frame), so "slide to the word's end" alone still lands on a word. Look for the nearest
    real pause (≥ min_gap) whose start lies within [t − before, t + after] and start the cue
    there; when the speaker gives none, keep t and report it so the caller ducks the cue."""
    def inside(tt):
        return any(w[0] - 0.04 <= tt < w[1] - 0.02 for w in ws)
    if not inside(t):
        return t, False
    gaps = []
    if ws and ws[0][0] - 0.04 > 0:
        gaps.append((0.0, ws[0][0] - 0.04))
    for a, b in zip(ws, ws[1:]):
        if b[0] - a[1] >= min_gap:
            gaps.append((a[1], b[0]))
    if ws:
        gaps.append((ws[-1][1], 1e9))
    best = None
    # The CUT's segment boundaries are measured silence (cut_aroll.py lands speech 0.04 s
    # after each one), unlike word timings, which Whisper smears across pauses: a cue that
    # starts 0.08 s before a boundary is in the gap whatever the word table says.
    for bd in bounds or ():
        cand = bd - 0.08
        if cand >= 0 and t - before <= cand <= t + after:
            if best is None or abs(cand - t) < abs(best - t):
                best = cand
    for g0, g1 in gaps:
        cand = g0 + 0.01
        if t - before <= cand <= t + after and not inside(cand):
            if best is None or abs(cand - t) < abs(best - t):
                best = cand
        elif g0 < t - before < g1 - 0.03:    # a pause that already contains the window start
            cand = t - before
            if best is None or abs(cand - t) < abs(best - t):
                best = cand
    if best is not None:
        return best, False
    return t, True


def _voice_ref(ctx):
    if "voice_ref" in ctx and ctx["voice_ref"] is not None:
        return ctx["voice_ref"]
    if ctx.get("_vref_done"):
        return None
    ctx["_vref_done"] = True
    import outro
    p = os.path.join(ctx.get("root", "."), ctx.get("aroll_path") or "assets/aroll.mp4")
    v = outro.voice_reference(p) if os.path.exists(p) else None
    ctx["voice_ref"] = v
    return v


# ======================================================================== types
def _statement(b, rows, prefix, t_min, t_max, fs, cls="m-p-lines"):
    """A word-synced statement block; returns html."""
    toks = [w for row in rows for w, _ in row]
    times, m = sync(toks, b.ctx, b.T, b.E)
    if toks and not m:
        b.notes.append(f"{b.id}: none of the statement words were found in the speech — "
                       f"revealed on an even rhythm instead")
    # A page must never sit empty waiting for its words (references/graphics.md): when the
    # first word is spoken well after the page arrives, the FIRST line lands on the arrival
    # (staggered), and the rest stay synced to the speech.
    early = bool(times) and times[0][0] > t_min + 0.6
    if early:
        # the words would then appear BEFORE they are spoken — the signature is that
        # they land as said. Say so, so the editor moves the moment's start instead.
        print(f"  moments: ! {b.id}: the statement's first word is spoken "
              f"{times[0][0] - b.T:.2f}s after the moment opens — its first line is shown on "
              f"arrival, out of sync. Start the moment ~0.4s before \"{toks[0]}\" "
              f"({times[0][0]:.2f}s) to keep every word landing as it is said.")
    html, k = [], 0
    for li, row in enumerate(rows):
        spans = []
        for w, c in row:
            spans.append(f'<span class="m-w {c}" id="{prefix}{k}">{_word_html(w)}</span>')
            t = t_min + 0.12 * k if (early and li == 0) else times[k][0]
            t = min(max(t, t_min + 0.06 * k), t_max)
            b.word_in(f"#{prefix}{k}", t)
            k += 1
        html.append(f'<div class="m-ln">{" ".join(spans)}</div>')
    return f'<div class="{cls}" style="font-size:{fs}px">{"".join(html)}</div>'


def _rows_html(b, rows, prefix, t_min, t_max, fs=None):
    """Check rows (KO .row with the CK tick), each revealed on its cue."""
    items = []
    for r in rows:
        items.append(r if isinstance(r, dict) else {"text": str(r)})
    firsts = [it["text"].split()[0] if it["text"].split() else "" for it in items]
    auto, _ = sync(firsts, b.ctx, t_min, t_max)
    html, ts = [], []
    prev = t_min - 0.25
    for i, it in enumerate(items):
        t = cue_time(it.get("cue"), b.ctx, t_min - 0.3, t_max) if it.get("cue") is not None else None
        if t is None:
            t = auto[i][0]
        t = min(max(t, prev + 0.22, t_min), t_max)
        prev = t
        ts.append(t)
        st = f' style="font-size:{fs}px;margin-top:{round(fs * 0.28)}px"' if fs else ""
        html.append(f'<div class="m-row" id="{prefix}{i}"{st}>'
                    f'<svg class="m-ck" viewBox="0 0 46 46"><circle cx="23" cy="23" r="21"/>'
                    f'<path id="{prefix}{i}c" d="M13 23.5 L20 30.5 L33 16"/></svg>'
                    f'<span>{_word_html(it["text"])}</span></div>')
        dx = 40 if b.ctx["dir"] == "rtl" else -40
        b.line(f'tl.fromTo("#{prefix}{i}", {{ opacity: 0, x: {dx}, filter: "blur(6px)" }}, '
               f'{{ opacity: 1, x: 0, filter: "blur(0px)", duration: 0.35, ease: "expo.out", '
               f'immediateRender: false }}, {r3(t)});')
        b.line(f'tl.fromTo("#{prefix}{i}c", {{ strokeDashoffset: 30 }}, {{ strokeDashoffset: 0, '
               f'duration: 0.3, ease: "power2.out", immediateRender: false }}, {r3(t + 0.08)});')
        b.cue("click", t, BELOW["tick"], tag=f"r{i}")
    return "".join(html), ts


def _question_card(b, q, pid, t_in, onpaper):
    text = str(q.get("text", "")).strip()
    toks = text.split()
    n = len(text)
    fs = 76 if n <= 36 else 68 if n <= 60 else 58
    # at most 3 lines in the card's text box (a line breaks on whole words, so budget 2.6)
    inner_w = 820 - 84
    while fs > 44 and _tw(text, fs, 700) / inner_w > 2.6:
        fs -= 4
    times, m = sync(toks, b.ctx, b.T, b.E)
    if toks and not m:
        b.notes.append(f"{b.id}: the question's words are not in the speech — revealed on an "
                       f"even rhythm")
    spans = []
    for k, w in enumerate(toks):
        spans.append(f'<span class="m-w" id="{pid}w{k}">{_word_html(w)}</span>')
        b.word_in(f"#{pid}w{k}", max(times[k][0], t_in + 0.12 + 0.04 * k), dy=10)
    meta = f'<small>{_esc(q["meta"])}</small>' if q.get("meta") else ""
    who = (f'<div class="m-q-who"><i>{_word_html(q.get("initials", "?"))}</i>'
           f'<span>{_word_html(q.get("who", ""))}</span>{meta}</div>')
    inner = f'{who}<div class="m-q-t" style="font-size:{fs}px">{" ".join(spans)}</div>'
    if not onpaper:
        return inner
    return f'<div class="m-q m-onpaper" id="{pid}">{inner}</div>'



def _page_turn_clips(ctx, s, push, rtl, out):
    """clip-path insets (local px) for the footage edge to ride EXACTLY on the hairline.

    The A-roll is scaled about x = W/2, so local x maps to screen cx + (x − cx)·s + push·p.
    The line, the clip and the push all share one ease, so with both ends solved the edge
    and the line coincide on every frame — no gap, no overlap, no jump."""
    W = ctx["W"]
    cx = W / 2.0
    loc = lambda screen, xoff: cx + (screen - xoff - cx) / s
    if out:
        if rtl:   # eaten from the right; line W → −6
            e0, e1 = loc(W, 0), loc(-6, push)
            return f"inset(0px {r3(W - e0)}px 0px 0px)", f"inset(0px {r3(W - e1)}px 0px 0px)"
        e0, e1 = loc(-6, 0), loc(W, push)
        return f"inset(0px 0px 0px {r3(e0)}px)", f"inset(0px 0px 0px {r3(e1)}px)"
    if rtl:       # grows back from the right edge; line W → −6, no push
        e0, e1 = loc(W, 0), loc(-6, 0)
        return f"inset(0px 0px 0px {r3(e0)}px)", f"inset(0px 0px 0px {r3(e1)}px)"
    e0, e1 = loc(-6, 0), loc(W, 0)
    return f"inset(0px {r3(W - e0)}px 0px 0px)", f"inset(0px {r3(W - e1)}px 0px 0px)"


def _paper(b):
    e, ctx = b.e, b.ctx
    W, H = ctx["W"], ctx["H"]
    T, E = b.T, b.E
    turn = float(e.get("turn", 0.5))
    if E - T < 2 * turn + 0.6:
        sys.exit(f"moment {b.id} (paper): {E - T:.2f}s is too short for two page turns — "
                 f"give it at least {2 * turn + 0.6:.1f}s")
    B = r3(E - turn)
    rtl = ctx["dir"] == "rtl"
    push = -60 if rtl else 60
    FOOT = _foot(ctx)
    gx0, gy0, gx1, gy1 = ctx["G"]["safe"]
    b.css = _css_once(ctx, "rows", "paper", "question")

    # ---- the page content
    inner = ['<div class="m-p-frame" data-grid="bleed"></div>']
    wm = _watermark(b)
    if wm:
        inner.append(wm)
    col = []
    t_min, t_max = T + turn * 0.7, B - 0.25
    if e.get("kicker"):
        col.append(f'<div class="m-p-kicker m-w" id="{b.id}-k">{_word_html(e["kicker"])}</div>')
        b.word_in(f"#{b.id}-k", T + turn * 0.6)
    rows = parse_lines(e.get("lines")) if e.get("lines") else []
    if rows:
        maxfs = 150 if not (e.get("sheet") or e.get("question")) else 100
        fs = int(e.get("size") or fit_size(rows, gx1 - gx0 - 60, maxfs))
        col.append(_statement(b, rows, f"{b.id}-w", t_min, t_max, fs))
    if e.get("question"):
        col.append(_question_card(b, e["question"], f"{b.id}-q", t_min, True))
        b.up(f"#{b.id}-q", t_min - 0.1)
    if e.get("sheet"):
        sh = e["sheet"]
        rws = sh.get("rows") or []
        if len(rws) > 6:
            sys.exit(f"moment {b.id}: a sheet holds at most 6 rows")
        rhtml, ts = _rows_html(b, rws, f"{b.id}-r", t_min + 0.3, t_max)
        title = f'<div class="m-c-title">{_word_html(sh["title"])}</div>' if sh.get("title") else ""
        col.append(f'<div class="m-sheet" id="{b.id}-s">{title}{rhtml}</div>')
        # the sheet arrives just before its first row is said — never an empty sheet
        b.up(f"#{b.id}-s", max(t_min, (ts[0] - 0.4) if ts else t_min))
    inner.append(f'<div class="m-p-col" id="{b.id}-col">{"".join(col)}</div>')
    b.el("div", b.id, "m-paper", T, E - T, "".join(inner))
    b.el("div", f"{b.id}-wa", "m-wipe", T, turn, extra=' data-grid="bleed" data-layout-ignore')
    b.el("div", f"{b.id}-wb", "m-wipe", B, turn, extra=' data-grid="bleed" data-layout-ignore')

    # ---- the page turn out (footage → paper) and back (paper → footage)
    x0, x1 = (W, -6) if rtl else (-6, W)
    s_out, _ = _state(ctx, T)
    s_back, _ = _state(ctx, B)
    c0, c1 = _page_turn_clips(ctx, s_out, push, rtl, True)
    r0, r1 = _page_turn_clips(ctx, s_back, push, rtl, False)
    ease = '"power2.inOut"'
    b.line(f"// ---- MOMENT {b.id} paper {T}-{E}s (scripts/moments.py): page turn out, back at {B}")
    b.line(f'tl.fromTo("#{b.id}-wa", {{ x: {x0} }}, {{ x: {x1}, duration: {turn}, ease: {ease}, '
           f'immediateRender: false }}, {T});')
    b.line(f'tl.fromTo({FOOT}, {{ clipPath: "{c0}", x: 0 }}, {{ clipPath: "{c1}", x: {push}, '
           f'duration: {turn}, ease: {ease}, immediateRender: false }}, {T});')
    b.line(f'tl.set({FOOT}, {{ opacity: 0 }}, {r3(T + turn)});')
    b.line(f'tl.fromTo("#{b.id}-col", {{ scale: 1 }}, {{ scale: 1.03, duration: {r3(E - T)}, '
           f'ease: "none", immediateRender: false }}, {T});')
    b.line(f'tl.set({FOOT}, {{ opacity: 1, x: 0 }}, {B});')
    b.line(f'tl.fromTo({FOOT}, {{ clipPath: "{r0}" }}, {{ clipPath: "{r1}", duration: {turn}, '
           f'ease: {ease}, immediateRender: false }}, {B});')
    b.line(f'tl.fromTo("#{b.id}-wb", {{ x: {x0} }}, {{ x: {x1}, duration: {turn}, ease: {ease}, '
           f'immediateRender: false }}, {B});')
    b.line(f'tl.set({FOOT}, {{ clipPath: "inset(0px 0px 0px 0px)", x: 0, opacity: 1 }}, {E});')
    b.kill(f"#{b.id}-wa", T + turn)
    b.kill(f"#{b.id}-wb", E)
    b.hide.append([T, E])
    b.transitions += ["page-turn"]
    b.cue("outro_page", T, BELOW["page"])
    b.cue("outro_page", B, BELOW["page"], tag="b")


def _watermark(b):
    e, ctx = b.e, b.ctx
    if e.get("watermark") is False:
        return ""
    brand = ctx.get("brand") or {}
    lg = brand.get("logo") or {}
    src = e.get("watermark") if isinstance(e.get("watermark"), str) else (
        lg.get("trimmed") or lg.get("src"))
    if not src:
        return ""
    if not os.path.exists(os.path.join(ctx.get("root", "."), src)):
        b.notes.append(f"{b.id}: watermark {src} not found — page has no watermark")
        return ""
    lw = float(lg.get("w") or 600)
    lh = float(lg.get("h") or 200)
    crop = e.get("watermark_crop") or [0, 0, lw, lh]
    x0, y0, x1, y1 = [float(v) for v in crop]
    cw, ch = x1 - x0, y1 - y0
    size = float(e.get("watermark_size") or (1150 if crop != [0, 0, lw, lh] else 1250))
    k = size / max(cw, ch)
    bw, bh = cw * k, ch * k
    cxw, cyw = e.get("watermark_at") or (ctx["W"] * 0.52, ctx["H"] * 0.54)
    left, top = cxw - bw / 2, cyw - bh / 2
    b.line(f'tl.fromTo("#{b.id}-wm", {{ rotation: -6, scale: 1 }}, {{ rotation: 4, scale: 1.12, '
           f'duration: {r3(b.E - b.T)}, ease: "none", immediateRender: false }}, {b.T});')
    op = float(e.get("watermark_opacity", 0.07))
    return (f'<div class="m-p-wm" id="{b.id}-wm" data-grid="bleed" style="left:{r3(left)}px;'
            f'top:{r3(top)}px;width:{r3(bw)}px;height:{r3(bh)}px;opacity:{op}">'
            f'<i style="background-image:url(\'{_esc(src)}\');left:{r3(-x0 * k)}px;'
            f'top:{r3(-y0 * k)}px;width:{r3(lw * k)}px;height:{r3(lh * k)}px"></i></div>')


def _question(b):
    ctx, e = b.ctx, b.e
    G = ctx["G"]
    gx0, gy0, gx1, gy1 = G["safe"]
    b.css = _css_once(ctx, "question")
    html = _question_card(b, e, b.id, b.T, False)
    b.el("div", b.id, "m-q", b.T, b.E - b.T, html)
    b.line(f"// ---- MOMENT {b.id} question {b.T}-{b.E}s")
    b.up(f"#{b.id}", b.T)
    b.line(f'tl.to("#{b.id}", {{ opacity: 0, y: 24, duration: 0.16, ease: "power2.in" }}, '
           f'{r3(b.E - 0.16)});')
    b.kill(f"#{b.id}", b.E)
    b.hide.append([b.T, b.E])
    b.cue("pop", b.T, BELOW["pop"])


def _checklist(b):
    ctx, e = b.ctx, b.e
    rows = e.get("rows") or []
    if not 1 <= len(rows) <= 4:
        sys.exit(f"moment {b.id} (checklist): 1-4 rows fit the bottom card (grid max "
                 f"{ctx['G']['bottom_card_max_h']} px) — got {len(rows)}. Use a paper sheet "
                 f"for a longer list.")
    b.css = _css_once(ctx, "rows", "checklist")
    # the card may grow to the grid's 300 px: fewer rows → bigger type
    fs = {1: 56, 2: 50, 3: 42}.get(len(rows), 35)
    rhtml, ts = _rows_html(b, rows, f"{b.id}-r", b.T + 0.35, b.E - 0.3, fs)
    title = f'<div class="m-c-title">{_word_html(e["title"])}</div>' if e.get("title") else ""
    look = " m-dark" if e.get("look") == "dark" else ""
    b.el("div", b.id, "m-check" + look, b.T, b.E - b.T, title + rhtml)
    b.line(f"// ---- MOMENT {b.id} checklist {b.T}-{b.E}s")
    b.up(f"#{b.id}", b.T, dy=70)
    b.kill(f"#{b.id}", b.E)
    b.cue("whoosh_low", b.T - 0.05, BELOW["whoosh"])


def _stamp(b):
    ctx, e = b.ctx, b.e
    G = ctx["G"]
    gx0, gy0, gx1, gy1 = G["safe"]
    b.css = _css_once(ctx, "stamp")
    text = str(e.get("text", "")).strip()
    fs = int(e.get("size", 108))
    w = _tw(text, fs, 900) + 2 * 34 + 14
    while w > (gx1 - gx0) * 0.9 and fs > 40:
        fs -= 4
        w = _tw(text, fs, 900) + 2 * 34 + 14
    h = fs * 1.02 + 30 + 14 + (fs * 0.36 * 1.1 + 4 if e.get("sub") else 0)
    at = e.get("at", [G["center_x"], 400])
    if isinstance(at, (int, float)):
        at = [G["center_x"], at]
    x, y = float(at[0]), float(at[1])
    rot = float(e.get("rotate", -6))
    land = None
    if e.get("cue") is not None:
        # default: the plate hits AS the word begins (KO), 0.06 s before its onset, so the
        # slam sound sits in the gap before the word, never on it; "land": "end" puts it
        # after the word instead (a verdict that should follow the line)
        edge = "end" if e.get("land") == "end" else "start"
        land = cue_time(e["cue"], ctx, b.T - 0.4, b.E, edge=edge)
        if land is not None and isinstance(e["cue"], str):
            land += 0.02 if edge == "end" else -0.06
    if land is None:
        land = b.T + 0.17
    land = max(land, b.T + 0.17)
    if b.E - land < 0.5:
        b.notes.append(f"{b.id}: the stamp holds only {b.E - land:.2f}s after it lands — "
                       f"give it ≥ 0.6 s (move end)")
    t0 = max(b.T, land - 0.17)
    # slam from as big as the SAFE ZONE allows — the grid holds even for three frames
    s0 = round(max(1.0, min(2.3, (gx1 - gx0) * 0.98 / w,
                            2 * min(y - gy0, gy1 - y) / max(1.0, h * 1.2))), 3)
    cls = "m-st" + (" m-warn" if e.get("color") == "warn" else "") + \
        (" m-ink" if e.get("on") == "paper" else "")
    sub = f"<small>{_word_html(e['sub'])}</small>" if e.get("sub") else ""
    inner = f'<div class="{cls}" id="{b.id}-s" style="font-size:{fs}px">{_word_html(text)}{sub}</div>'
    style = f' style="top:{r3(y - h / 2)}px;left:{r3(gx0 + (x - G["center_x"]))}px"'
    b.el("div", b.id, "m-stamp", t0, b.E - t0, inner, extra=style)
    sel = f"#{b.id}-s"
    b.line(f"// ---- MOMENT {b.id} stamp lands {r3(land)}s")
    b.line(f'tl.fromTo("{sel}", {{ opacity: 0, scale: {s0}, rotation: {rot - 8} }}, '
           f'{{ opacity: 1, scale: 1, rotation: {rot}, duration: {r3(land - t0)}, ease: "power4.in", '
           f'immediateRender: false }}, {r3(t0)});')
    for i, (dx, dy) in enumerate([(7, -4), (-5, 3), (3, -2), (0, 0)]):
        b.line(f'tl.to("{sel}", {{ x: {dx}, y: {dy}, duration: 0.04, ease: "sine.inOut" }}, '
               f'{r3(land + 0.005 + i * 0.04)});')
    if b.E - land > 0.5:
        b.line(f'tl.fromTo("{sel}", {{ scale: 1 }}, {{ scale: 1.04, duration: {r3(b.E - land - 0.2)}, '
               f'ease: "none", immediateRender: false }}, {r3(land + 0.2)});')
    b.kill(sel, b.E)
    b.cue("outro_slam", land - 0.01, BELOW["slam"])


def _chips(b):
    ctx, e = b.ctx, b.e
    G = ctx["G"]
    items = [it if isinstance(it, dict) else {"text": str(it)} for it in e.get("items") or []]
    if not 1 <= len(items) <= 4:
        sys.exit(f"moment {b.id} (chips): 2-4 items read at a glance — got {len(items)}")
    if len(items) == 1:
        b.notes.append(f"{b.id}: one chip is a label, not a set — consider a stamp")
    b.css = _css_once(ctx, "chips")
    at = e.get("at", "top")
    y = 250 if at == "top" else 1250 if at == "low" else float(at)
    fs = 54 if len(items) <= 2 else 46 if len(items) == 3 else 40
    tot = sum(_tw(it["text"], fs, 800) + 72 for it in items) + 18 * (len(items) - 1)
    if tot > G["safe_width"]:
        b.notes.append(f"{b.id}: chips wrap to two rows ({tot:.0f}px > {G['safe_width']}px)")
    firsts = [it["text"].split()[0] for it in items]
    auto, _ = sync(firsts, ctx, b.T, b.E)
    html, prev = [], b.T - 0.2
    for i, it in enumerate(items):
        t = cue_time(it.get("cue"), ctx, b.T - 0.2, b.E) if it.get("cue") is not None else None
        t = auto[i][0] if t is None else t
        t = min(max(t, prev + 0.16, b.T), b.E - 0.3)
        prev = t
        acc = " m-acc" if it.get("accent") else ""
        html.append(f'<span class="m-chip{acc}" id="{b.id}-c{i}" style="font-size:{fs}px">'
                    f'{_word_html(it["text"])}</span>')
        sel = f"#{b.id}-c{i}"
        b.line(f'tl.fromTo("{sel}", {{ opacity: 0, scale: 0.3 }}, {{ opacity: 1, scale: 1.1, '
               f'duration: 0.26, ease: "power4.out", immediateRender: false }}, {r3(t)});')
        b.line(f'tl.to("{sel}", {{ scale: 1, duration: 0.2, ease: "back.out(3)" }}, {r3(t + 0.26)});')
        b.cue("pop", t, BELOW["pop"], on_word_ok=True, tag=f"c{i}")
    b.el("div", b.id, "m-chips", b.T, b.E - b.T, "".join(html), extra=f' style="top:{r3(y)}px"')
    b.kill(f"#{b.id}", b.E)


def _searchbar(b):
    ctx, e = b.ctx, b.e
    G = ctx["G"]
    gx0, gy0, gx1, gy1 = G["safe"]
    px, pw = gx0 + 30, gx1 - gx0 - 60
    b.css = _css_once(ctx, "searchbar")
    q = str(e.get("query", "")).strip()
    world = e.get("world", "gradient") != "footage"
    y = float(e.get("y", 420 if world else 250))
    y_f = float(e.get("y_footage", 250))
    avail = pw - 80 - 60 - 22 - 12
    fs = 60.0
    wrap = False
    tw = _tw(q, fs, 500)
    if tw > avail:
        fs = max(44.0, fs * avail / tw)
        if _tw(q, fs, 500) > avail:
            wrap = True
            b.notes.append(f"{b.id}: query is long — the pill wraps to two lines; shorten it")
    fs = int(fs)
    toks = q.split()
    times, m = sync(toks, ctx, b.T, b.E)
    if toks and not m:
        b.notes.append(f"{b.id}: the query is not in the speech — typed on an even rhythm")
    t_min = b.T + (0.4 if world else 0.25)
    spans, sets, prev = [], [], t_min - 0.03
    ci = 0
    for k, w in enumerate(toks):
        s, en = times[k]
        nxt = times[k + 1][0] if k + 1 < len(toks) else en + 0.3
        en = max(s + 0.08, min(en, nxt))
        chars = [c for c in w]
        cs = []
        for j, c in enumerate(chars + [" "]):
            t = s + (en - s) * j / max(1, len(chars))
            t = max(t, prev + 0.03, t_min)
            prev = t
            cid = f"{b.id}-ch{ci}"
            ci += 1
            cs.append(f'<span class="m-ch" id="{cid}">{_esc(c)}</span>')
            sets.append((cid, t))
        inner = "".join(cs)
        if _has_latin(w):
            inner = f'<span class="m-ltr">{"".join(cs[:-1])}</span>{cs[-1]}'
        spans.append(inner)
    t_typed = prev
    ico = ('<svg class="m-s-ico" viewBox="0 0 52 52"><circle cx="22" cy="22" r="14"/>'
           '<path d="M32.5 32.5 L45 45"/></svg>')
    qcls = "m-s-q" + (" m-wrap" if wrap else "")
    pill = (f'<div class="m-s-pill" id="{b.id}-p" style="top:{r3(y)}px'
            f'{";border-radius:46px" if wrap else ""}">{ico}<div class="{qcls}" '
            f'style="font-size:{fs}px">{"".join(spans)}<span class="m-caret" id="{b.id}-cr"></span>'
            f'</div></div>')
    inner = (f'<div class="m-world" id="{b.id}-wd" data-grid="bleed" style="opacity:0"></div>' if world else "") + pill
    b.el("div", b.id, "m-search", b.T, b.E - b.T, inner)
    b.line(f"// ---- MOMENT {b.id} searchbar {b.T}-{b.E}s ({'gradient world' if world else 'footage'})")
    tp = b.T + (0.12 if world else 0.0)
    if world:
        b.line(f'tl.fromTo("#{b.id}-wd", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, '
               f'ease: "power1.out", immediateRender: false }}, {b.T});')
    b.line(f'tl.fromTo("#{b.id}-p", {{ opacity: 0, y: 30, scale: 0.92, filter: "blur(8px)" }}, '
           f'{{ opacity: 1, y: 0, scale: 1, filter: "blur(0px)", duration: 0.45, ease: "expo.out", '
           f'immediateRender: false }}, {r3(tp)});')
    for cid, t in sets:
        b.line(f'tl.set("#{cid}", {{ display: "inline" }}, {r3(t)});')
    # the caret blinks once the typing stops (explicit steps — seek-safe, no repeat)
    t = t_typed + 0.45
    on = False
    while t < b.E - 0.1:
        b.line(f'tl.set("#{b.id}-cr", {{ opacity: {1 if on else 0} }}, {r3(t)});')
        on = not on
        t += 0.45
    if world:
        td = float(e.get("dissolve") or (b.T + 0.55 * (b.E - b.T)))
        td = min(max(td, tp + 0.6), b.E - 0.5)
        b.line(f'tl.fromTo("#{b.id}-wd", {{ opacity: 1 }}, {{ opacity: 0, duration: 0.45, '
               f'ease: "power1.inOut", immediateRender: false }}, {r3(td)});')
        b.line(f'tl.fromTo("#{b.id}-p", {{ y: 0, scale: 1 }}, {{ y: {r3(y_f - y)}, scale: 0.86, '
               f'duration: 0.5, ease: "power3.inOut", immediateRender: false }}, {r3(td)});')
    b.kill(f"#{b.id}-p", b.E)
    b.hide.append([b.T, b.E])
    b.cue("whoosh_low", b.T, BELOW["whoosh"])


_CARD_SPOTS = [(110, 320, -6), (600, 290, 5), (100, 1250, 4), (600, 1290, -5),
               (80, 560, -3), (640, 1060, 4)]


def _fly(b):
    ctx, e = b.ctx, b.e
    G = ctx["G"]
    gx0, gy0, gx1, gy1 = G["safe"]
    T, E = b.T, b.E
    if E - T < 1.6:
        sys.exit(f"moment {b.id} (fly): {E - T:.2f}s — the fly out and back needs ≥ 1.6 s")
    b.css = _css_once(ctx, "fly")
    # the world and its gradient reuse the searchbar's .m-world rule
    if "searchbar" not in ctx["_css_done"]:
        b.css += "\n" + _template("searchbar", ctx)
        ctx["_css_done"].add("searchbar")
    FOOT = _foot(ctx)
    s0, y0 = _state(ctx, T)
    sE, yE = _state(ctx, E)
    if abs(y0) > 1 or abs(yE) > 1:
        b.notes.append(f"{b.id}: the A-roll is not full-screen here (y {y0:g}) — a fly reads "
                       f"best from a full-screen beat")
    S1 = float(e.get("card_scale", 0.6))
    R = 56.0
    Rl = r3(R / S1)
    fly_y = -1500
    # one continuous move, like Rollin: the frame shrinks into a card (0.32 s) while a
    # power3.in rise starts under it, so it whips up out of frame as the shrink ends; the
    # motion smear ramps in only while it actually travels fast. The return mirrors it.
    SH, FL, BL = 0.32, 0.56, 0.26
    t_out = r3(T + 0.04 + FL)
    tc = r3(E - FL - 0.04)
    # ---- world content
    inner = ['<div class="m-world" data-grid="bleed"></div>', f'<div class="m-f-in" id="{b.id}-in">']
    t_min, t_max = T + 0.5, tc - 0.1
    rows = parse_lines(e.get("lines")) if e.get("lines") else []
    if rows:
        cy = float(e.get("y", 860))
        # kinetic look: every line sized to the width on its own (the keyword line gets big)
        sizes = [int(e.get("size") or fit_size([row], gx1 - gx0 - 40, 230 if len(row) == 1 else 150, 56))
                 for row in rows]
        hgt = sum(sizes) * 1.04
        st = _statement(b, rows, f"{b.id}-w", t_min, t_max, sizes[0], cls="m-f-lines")
        for i, fsz in enumerate(sizes):
            st = st.replace('<div class="m-ln">', f'<div class="m-ln" style="font-size:{fsz}px">', 1)
        inner.append(st.replace('class="m-f-lines" style="', f'class="m-f-lines" style="top:{r3(cy - hgt / 2)}px;', 1))
    for i, c in enumerate((e.get("cards") or [])[:6]):
        c = c if isinstance(c, dict) else {"text": str(c)}
        lx, ty, rot = _CARD_SPOTS[i]
        if c.get("img"):
            body = f'<img src="{_esc(c["img"])}" alt="" />'
            cls, wpx = "m-f-card m-img", 230
        else:
            body = _word_html(c.get("text", ""))
            cls, wpx = "m-f-card", _tw(c.get("text", ""), 52, 700) + 76
        lx = max(gx0 + 10, min(lx, gx1 - wpx - 10))
        inner.append(f'<div class="{cls}" id="{b.id}-c{i}" style="left:{r3(lx)}px;top:{ty}px">{body}</div>')
        t = t_min + 0.09 * i
        # rack focus in; a slow drift for the whole hold (separate properties, no overlap)
        b.line(f'tl.fromTo("#{b.id}-c{i}", {{ opacity: 0, scale: 1.18, rotation: {rot}, '
               f'filter: "blur(14px)" }}, {{ opacity: 1, scale: 1, rotation: {rot}, '
               f'filter: "blur(0px)", duration: 0.6, ease: "power3.out", immediateRender: false }}, {r3(t)});')
        b.line(f'tl.fromTo("#{b.id}-c{i}", {{ y: 0 }}, {{ y: {-22 - 8 * (i % 3)}, '
               f'duration: {r3(max(0.2, E - t))}, ease: "none", immediateRender: false }}, {r3(t)});')
    inner.append("</div>")
    b.el("div", b.id, "m-fly", T, E - T, "".join(inner))
    # ---- the footage: shrink + whip up → (world) → drop back + grow
    b.line(f"// ---- MOMENT {b.id} fly {T}-{E}s: footage card out at {T}, back by {E}")
    b.line(f'tl.fromTo("#{b.id}-in", {{ scale: 1 }}, {{ scale: 1.05, duration: {r3(E - T)}, '
           f'ease: "none", immediateRender: false }}, {T});')
    b.line(f'tl.fromTo({FOOT}, {{ scale: {r3(s0)}, clipPath: "inset(0px round 0px)" }}, '
           f'{{ scale: {S1}, clipPath: "inset(0px round {Rl}px)", duration: {SH}, '
           f'ease: "power2.inOut", immediateRender: false }}, {T});')
    b.line(f'tl.fromTo({FOOT}, {{ y: {r3(y0)} }}, {{ y: {fly_y}, duration: {FL}, ease: "power3.in", '
           f'immediateRender: false }}, {r3(T + 0.04)});')
    b.line(f'tl.fromTo({FOOT}, {{ filter: "blur(0px)" }}, {{ filter: "blur(16px)", duration: {BL}, '
           f'ease: "power2.in", immediateRender: false }}, {r3(t_out - BL)});')
    b.line(f'tl.set({FOOT}, {{ opacity: 0, filter: "none" }}, {t_out});')
    b.line(f'tl.set({FOOT}, {{ opacity: 1 }}, {tc});')
    b.line(f'tl.fromTo({FOOT}, {{ y: {fly_y} }}, {{ y: {r3(yE)}, duration: {FL}, ease: "power3.out", '
           f'immediateRender: false }}, {tc});')
    b.line(f'tl.fromTo({FOOT}, {{ filter: "blur(16px)" }}, {{ filter: "blur(0px)", duration: {BL}, '
           f'ease: "power2.out", immediateRender: false }}, {tc});')
    b.line(f'tl.fromTo({FOOT}, {{ scale: {S1}, clipPath: "inset(0px round {Rl}px)" }}, '
           f'{{ scale: {r3(sE)}, clipPath: "inset(0px round 0px)", duration: {SH}, '
           f'ease: "power2.inOut", immediateRender: false }}, {r3(E - SH)});')
    b.line(f'tl.set({FOOT}, {{ clipPath: "inset(0px 0px 0px 0px)", filter: "none", opacity: 1 }}, {E});')
    b.hide.append([T, E])
    b.transitions += ["frame-fly"]
    b.cue("outro_rush", T, BELOW["rush"])
    b.cue("whoosh_high", T + SH - 0.04, BELOW["flight"], tag="f")
    b.cue("whoosh_low", tc, BELOW["flight"], tag="b")


def _punch(b):
    ctx, e = b.ctx, b.e
    FOOT = _foot(ctx)
    steps = _punch_steps(e)
    b.line(f"// ---- MOMENT {b.id} punch {b.T}-{b.E}s")
    beats = [t for t in ctx.get("beat_starts", []) if b.T < t < b.E]
    marks = sorted(set([t for t, _ in steps] + beats))
    for t in marks:
        base, y = ctx["state_at"](t)
        f = 1.0
        for ts, k in steps:
            if t >= ts - 1e-6:
                f = k
        if abs(y) > 1:
            b.notes.append(f"{b.id}: punch at {t:.2f}s on a non-full-screen beat (y {y:g})")
        b.line(f'tl.set({FOOT}, {{ scale: {r3(base * f)} }}, {r3(t)});')
    base, _ = ctx["state_at"](b.E)
    b.line(f'tl.set({FOOT}, {{ scale: {r3(base)} }}, {b.E});')


def _punch_steps(e):
    if e.get("steps"):
        st = sorted((float(t), float(k)) for t, k in e["steps"])
    else:
        st = [(float(e["start"]), float(e.get("scale", 1.1)))]
    for _, k in st:
        if k > 1.16:
            print(f"  moments: ! {e['id']} (punch): factor {k} crops past 1.16 — on 1080p "
                  f"footage that is visibly soft (KO stayed at 1.10-1.14)")
        if k < 1.0:
            sys.exit(f"moment {e['id']} (punch): factor {k} < 1 would expose the frame edge")
    return st


BUILDERS = {"paper": _paper, "question": _question, "checklist": _checklist, "stamp": _stamp,
            "chips": _chips, "searchbar": _searchbar, "fly": _fly, "punch": _punch}


# ===================================================================== public API
def fragments(entry, ctx):
    """One media.json moment → dict(elements, css, timeline, sfx, hide_captions,
    transitions, notes). `ctx` from make_ctx(). CSS is emitted once per type per ctx."""
    t = entry.get("type")
    if t not in BUILDERS:
        sys.exit(f"moment {entry.get('id')}: unknown type {t!r} — choose from {', '.join(TYPES)}")
    for k in ("id", "start", "end"):
        if k not in entry:
            sys.exit(f"moment {entry.get('id', '?')} ({t}): missing {k!r}")
    if float(entry["end"]) <= float(entry["start"]):
        sys.exit(f"moment {entry['id']}: end {entry['end']} ≤ start {entry['start']}")
    if ctx.get("end") and float(entry["end"]) > ctx["end"] + 1e-6:
        sys.exit(f"moment {entry['id']}: ends {entry['end']}s, past the A-roll END {ctx['end']:.3f}s")
    b = _B(entry, ctx)
    BUILDERS[t](b)
    return b.out()


def collect(entries, ctx):
    """Every moment of a reel, merged. Validates the cross-moment rules (no two footage
    moments overlap, nothing that moves the footage inside a matted hook), assigns a free
    track lane to every element, and merges the caption-hide windows.

    Returns dict(elements [.. +lane], css, timeline, sfx, hide_captions [[a,b]],
                 transitions [{name,id,start,end}], notes)."""
    ents = []
    for i, e in enumerate(entries or []):
        e = {k: v for k, v in e.items() if not str(k).startswith("_")}
        e.setdefault("id", f"m{i + 1}")
        ents.append(e)
    ids = [e["id"] for e in ents]
    if len(set(ids)) != len(ids):
        sys.exit(f"moments: duplicate ids {ids}")
    foot = sorted((float(e["start"]), float(e["end"]), e["id"]) for e in ents
                  if e.get("type") in FOOTAGE_TYPES)
    for (a0, a1, ia), (b0, b1, ib) in zip(foot, foot[1:]):
        if b0 < a1 - 1e-6:
            sys.exit(f"moments {ia} and {ib} both move the footage and overlap "
                     f"({a0:.2f}-{a1:.2f} / {b0:.2f}-{b1:.2f})")
    he = ctx.get("hook_end")
    for e in ents:
        if he and e.get("type") in FOOTAGE_TYPES + ("punch",) and float(e["start"]) < he - 1e-6:
            sys.exit(f"moment {e['id']} ({e['type']}) starts inside the matted hook "
                     f"(ends {he:.2f}s): the matte sits ON TOP of the A-roll and would not move "
                     f"with it. Start it at or after {he:.2f}s, or drop the matte.")
    ctx["_punch"] = [(float(e["start"]), float(e["end"]), _punch_steps(e))
                     for e in ents if e.get("type") == "punch"]
    out = dict(elements=[], css=[], timeline=[], sfx=[], hide_captions=[], transitions=[],
               notes=[])
    # punches first: their sets must exist before the moments that read the punched state
    for e in sorted(ents, key=lambda x: (x.get("type") != "punch", float(x["start"]))):
        f = fragments(e, ctx)
        out["elements"] += f["elements"]
        if f["css"]:
            out["css"].append(f["css"])
        out["timeline"] += f["timeline"]
        out["sfx"] += f["sfx"]
        out["hide_captions"] += f["hide_captions"]
        out["transitions"] += [{"name": n, "id": e["id"], "start": r3(e["start"]),
                                "end": r3(e["end"])} for n in f["transitions"]]
        out["notes"] += f["notes"]
    out["css"] = "\n".join(out["css"])
    out["hide_captions"] = merge_windows(out["hide_captions"])
    # lanes: interval colouring, so overlapping moment elements never share a track
    lanes = []
    for el in sorted(out["elements"], key=lambda x: x["start"]):
        for i, busy in enumerate(lanes):
            if busy <= el["start"] + 1e-6:
                lanes[i] = el["start"] + el["dur"]
                el["lane"] = i
                break
        else:
            lanes.append(el["start"] + el["dur"])
            el["lane"] = len(lanes) - 1
    for i, s in enumerate(out["sfx"]):
        s["lane"] = i % 4
    if ctx.get("_ducked"):
        out["notes"].append("no pause near these cues, ducked to 55 %: " + "; ".join(ctx["_ducked"]))
    for n in out["notes"]:
        print(f"  moments: ! {n}")
    return out


def merge_windows(ws):
    ws = sorted([float(a), float(b)] for a, b in ws)
    out = []
    for a, b in ws:
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([r3(a), r3(b)])
    return [[r3(a), r3(b)] for a, b in out]


def make_state_at(beats):
    """The A-roll's (scale, y) at t, mirroring build_index.py's per-beat rule:
    panel → (1.02, 770); any other non-hook beat → (1.02, 0); before the first → (1, 0)."""
    bl = sorted((float(s), k) for s, k, *_ in (beats or []) if k != "hook")

    def state_at(t):
        st = (1.0, 0.0)
        for s, k in bl:
            if t >= s - 1e-6:
                st = (1.02, 770.0) if k == "panel" else (1.02, 0.0)
        return st
    return state_at


def load_brand_json(cfg, root="."):
    b = cfg.get("brand", {})
    p = b.get("json") or os.path.join(os.path.dirname(b.get("css") or "brand/brand.css") or "brand",
                                      "brand.json")
    p = os.path.join(root, p)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def make_ctx(cfg, media=None, end=None, beatmap=None, words=None, root=".", hook_end=None,
             voice_ref=None):
    """Everything the moment builders need. build_index passes its cfg, media, END and the
    loaded beat map; words default to src/words.json."""
    media = media or {}
    if words is None:
        wp = os.path.join(root, "src", "words.json")
        words = json.load(open(wp, encoding="utf-8")) if os.path.exists(wp) else []
    beats = list(getattr(beatmap, "BEATS", []) or []) if beatmap else []
    bp = os.path.join(root, "src", "bounds.json")
    bounds = json.load(open(bp, encoding="utf-8")).get("bounds", []) if os.path.exists(bp) else []
    if hook_end is None and media.get("hook") and media["hook"].get("matte"):
        hook_end = float(media["hook"]["end"])
    return {
        "cfg": cfg, "G": grid.from_config(cfg),
        "W": cfg["project"]["width"], "H": cfg["project"]["height"],
        "fps": float(cfg["project"].get("fps", 25)),
        "dir": cfg.get("language", {}).get("direction", "rtl"),
        "words": words, "brand": load_brand_json(cfg, root), "end": end,
        "state_at": make_state_at(beats), "beat_starts": [float(b[0]) for b in beats],
        "hook_end": hook_end, "root": root, "foot": '"#aroll"',
        "aroll_path": media.get("aroll", "assets/aroll.mp4"),
        "sfx_dir": cfg.get("audio", {}).get("sfx_dir", "assets/sfx"),
        "voice_ref": voice_ref, "bounds": [float(x) for x in bounds],
    }


# ======================================================================= preview
PREVIEW_HTML = """<!doctype html>
<!-- GENERATED by scripts/moments.py preview — a test bench for one moment, not a deliverable.
     The A-roll is a window of the real one ({t0:.2f}-{t1:.2f}s); times are shifted by -{t0:.2f}s. -->
<html lang="{lang}">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={W}, height={H}" />
    <title>moment preview</title>
    <script src="assets/vendor/gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      :root {{ --brand-primary: #4fe6d8; --brand-primary-rgb: 79, 230, 216;
               --brand-secondary: #3d8bff; --brand-secondary-rgb: 61, 139, 255;
               --brand-accent: #D97757; --brand-accent-rgb: 217, 119, 87;
               --brand-ink: #0a0a0a; --brand-paper: #f7f5f0; --brand-on-primary: #0a0a0a;
               --hl-on-dark: #4fe6d8; --hl-on-light: #0b6b66;
               --brand-grad-a: #0e384e; --brand-grad-b: #03141e;
               --brand-font: "{family}"; --display-font: "{family}"; }}
{brand_css}
{font_css}
      html, body {{ margin: 0; width: {W}px; height: {H}px; overflow: hidden; background: #000; }}
      #root {{ position: relative; width: {W}px; height: {H}px; overflow: hidden; background: #000; }}
      video {{ position: absolute; inset: 0; width: {W}px; height: {H}px; object-fit: cover; }}
      #aroll {{ z-index: 20; transform-origin: 50% 30%; }}
      .cap {{ position: absolute; left: {gx0}px; width: {gw}px; z-index: 50; text-align: center;
              direction: {dir}; font-family: var(--brand-font), "Inter", sans-serif;
              font-weight: 800; line-height: 1.0; top: {cap_top}px; }}
      .cap .p {{ display: inline-block; font-size: {cap_fs}px; padding: 20px 34px 26px;
                 white-space: nowrap; {cap_css} }}
{css}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-width="{W}" data-height="{H}"
         data-duration="{end}">
{body}
    </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
      tl.set("#aroll", {{ scale: 1.02, y: 0 }}, 0);
{js}
      window.__timelines = window.__timelines || {{}};
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""


def _shift(v, dt, key=None):
    if isinstance(v, dict):
        return {k: _shift(x, dt, k) for k, x in v.items()}
    if isinstance(v, list):
        if key == "steps":
            return [[float(a) + dt, b] for a, b in v]
        return [_shift(x, dt, key) for x in v]
    if key in ("start", "end", "cue", "dissolve") and isinstance(v, (int, float)) \
            and not isinstance(v, bool):
        return round(float(v) + dt, 3)
    return v


def _preview_caps(words, hide, end):
    """Plain caption cards (≤3 words, split at pauses) so the bench shows where the caption
    band is and that it really disappears inside the hide windows."""
    groups, cur = [], []
    for w in words:
        wide = _tw(" ".join(x[2] for x in cur + [w]), 70, 800) > 760
        if cur and (len(cur) >= 3 or wide or w[0] - cur[-1][1] > 0.3):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    out = []
    for i, g in enumerate(groups):
        s = g[0][0]
        e = groups[i + 1][0][0] - 0.005 if i + 1 < len(groups) else min(end, g[-1][1] + 0.3)
        segs = [(s, e)]
        for a, b in hide:
            nxt = []
            for x, y in segs:
                if b <= x or a >= y:
                    nxt.append((x, y))
                    continue
                if a > x:
                    nxt.append((x, a))
                if b < y:
                    nxt.append((b, y))
            segs = nxt
        txt = " ".join(_word_html(w[2]) for w in g)
        for k, (x, y) in enumerate(segs):
            if y - x < 0.08:
                continue
            out.append((x, y, txt))
    return out


def preview(a):
    cfg = hfcfg.load(a.config)
    W, H = cfg["project"]["width"], cfg["project"]["height"]
    fps = int(cfg["project"].get("fps", 25))
    proj = os.getcwd()
    if a.entry:
        entry = json.loads(a.entry)
    elif a.media and a.id:
        media = json.load(open(a.media, encoding="utf-8"))
        entry = next((m for m in media.get("moments", []) if m.get("id") == a.id), None)
        if not entry:
            sys.exit(f"no moment {a.id!r} in {a.media}")
    else:
        entry = dict(EXAMPLES[a.type])
    entry.setdefault("type", a.type)
    entry.setdefault("id", "m1")
    aroll = a.aroll or "assets/aroll.mp4"
    if not os.path.exists(aroll):
        sys.exit(f"{aroll} not found — run from the project folder or pass --aroll")
    import outro
    dur = outro.media_duration(aroll)
    t0 = max(0.0, float(entry["start"]) - a.pad)
    t1 = min(dur, float(entry["end"]) + a.pad)
    t0 = round(round(t0 * fps) / fps, 3)
    out = os.path.abspath(a.out or os.path.join("build", "moments_preview", entry["type"]))
    for d in ("assets/vendor", "assets/fonts", "assets/sfx", "brand", "renders"):
        os.makedirs(os.path.join(out, d), exist_ok=True)
    # gsap, fonts, brand
    for base in (proj, hfcfg.SKILL_DIR):
        p = os.path.join(base, "assets/vendor/gsap.min.js")
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(out, "assets/vendor/gsap.min.js"))
            break
    else:
        sys.exit("assets/vendor/gsap.min.js missing — run scripts/setup_assets.py")
    family = cfg["brand"]["font_family"]
    font_css = []
    for fam in (family, "Roboto Slab", "Inter"):
        for fd in (cfg["brand"].get("font_dir", "assets/fonts"),
                   os.path.join(hfcfg.SKILL_DIR, "assets", "fonts")):
            f = os.path.join(fd, fam + ".ttf")
            if os.path.exists(f):
                shutil.copy2(f, os.path.join(out, "assets/fonts", fam + ".ttf"))
                font_css.append(f'      @font-face {{ font-family: "{fam}"; src: url("assets/fonts/'
                                f'{fam.replace(" ", "%20")}.ttf") format("truetype"); '
                                f'font-weight: 100 1000; font-display: block; }}')
                break
    brand = load_brand_json(cfg, proj)
    brand_css = ""
    bcss = cfg["brand"].get("css", "brand/brand.css")
    if bcss and os.path.exists(bcss):
        brand_css = open(bcss, encoding="utf-8").read()
    elif brand:
        brand_css = outro.brand_vars_css(brand.get("colors"))
    if brand:
        for key in ("src", "trimmed", "on_dark", "on_light"):
            p = (brand.get("logo") or {}).get(key)
            if p and os.path.exists(p):
                os.makedirs(os.path.dirname(os.path.join(out, p)), exist_ok=True)
                shutil.copy2(p, os.path.join(out, p))
    # the A-roll window, frame-exact
    dst = os.path.join(out, "assets", "aroll.mp4")
    n = int(round((t1 - t0) * fps))
    r = hfcfg.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.3f}", "-i", aroll,
                   "-frames:v", str(n), "-t", f"{n / fps:.3f}", "-vf", f"fps={fps},format=yuv420p",
                   "-c:v", "libx264", "-crf", "16", "-preset", "fast", "-colorspace", "bt709",
                   "-color_primaries", "bt709", "-color_trc", "bt709", "-c:a", "aac", dst])
    if r.returncode:
        sys.exit(f"could not cut the A-roll window\n{r.stderr[-400:]}")
    end = round(n / fps, 3)
    words = []
    wp = os.path.join(proj, "src", "words.json")
    if os.path.exists(wp):
        for w in json.load(open(wp, encoding="utf-8")):
            s, e_, txt = (w["s"], w["e"], w["w"]) if isinstance(w, dict) else w
            if t0 <= s < t0 + end:
                words.append([round(s - t0, 3), round(min(e_ - t0, end), 3), txt])
    sh = _shift(entry, -t0)
    ctx = make_ctx(cfg, {"aroll": "assets/aroll.mp4"}, end, None, words, root=out)
    ctx["brand"] = brand
    ctx["state_at"] = lambda t: (1.02, 0.0)
    bp = os.path.join(proj, "src", "bounds.json")
    if os.path.exists(bp):
        ctx["bounds"] = [round(x - t0, 3) for x in json.load(open(bp, encoding="utf-8"))["bounds"]
                         if t0 < x < t0 + end]
    ctx["voice_ref"] = outro.voice_reference(dst)
    res = collect([sh], ctx)
    body = [f'      <video id="aroll" class="clip" src="assets/aroll.mp4" data-start="0" '
            f'data-duration="{end}" data-track-index="1" playsinline data-has-audio="true"></video>']
    for el in res["elements"]:
        body.append(f'      <{el["tag"]} id="{el["id"]}" class="clip {el["cls"]}" '
                    f'data-start="{el["start"]}" data-duration="{el["dur"]}" '
                    f'data-track-index="{10 + el["lane"]}"{el.get("extra", "")}>'
                    f'{el.get("inner", "")}</{el["tag"]}>')
    for s in res["sfx"]:
        body.append(f'      <audio id="{s["id"]}" src="{s["src"]}" data-start="{s["start"]}" '
                    f'data-duration="{s["duration"]}" data-track-index="{30 + s["lane"]}" '
                    f'data-volume="{s["volume"]}"></audio>')
    if not a.no_captions:
        for i, (s, e_, txt) in enumerate(_preview_caps(words, res["hide_captions"], end)):
            body.append(f'      <div id="pc{i:03d}" class="clip cap" data-start="{r3(s)}" '
                        f'data-duration="{r3(e_ - s)}" data-track-index="{40 + i % 2}">'
                        f'<span class="p">{txt}</span></div>')
    G = ctx["G"]
    ph = grid.plate_height(cfg["brand"]["caption_size"])
    html = PREVIEW_HTML.format(
        t0=t0, t1=t0 + end, lang=cfg["language"].get("code", "he"), W=W, H=H, family=family,
        brand_css=brand_css, font_css="\n".join(font_css), css=res["css"], end=end,
        body="\n".join(body), js="\n".join(res["timeline"]), gx0=G["safe"][0],
        gw=G["safe_width"], dir=ctx["dir"], cap_top=grid.caption_top(G, ph),
        cap_fs=cfg["brand"]["caption_size"], cap_css=grid.caption_css(cfg))
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(html)
    json.dump({"entry": sh, "shift": -t0, "hide_captions": res["hide_captions"],
               "transitions": res["transitions"], "sfx": res["sfx"], "notes": res["notes"]},
              open(os.path.join(out, "moment_info.json"), "w"), indent=1, ensure_ascii=False)
    print(f"  preview → {out}/index.html  ({entry['type']}, window {t0:.2f}-{t0 + end:.2f}s of "
          f"the A-roll; moment at {sh['start']}-{sh['end']}s)")
    if a.render:
        mp4 = os.path.join(out, "renders", f"{entry['type']}.mp4")
        r = hfcfg.run(["npx", "hyperframes", "render", "--quality", "draft", "--fps", str(fps),
                       "-o", mp4], cwd=out)
        if r.returncode or not os.path.exists(mp4):
            sys.exit(f"render failed:\n{(r.stdout or '')[-1500:]}\n{(r.stderr or '')[-1500:]}")
        print(f"  rendered → {mp4}")
        if a.sheet:
            sheet = os.path.join(out, "renders", f"{entry['type']}_sheet.jpg")
            contact_sheet(mp4, sheet, max(0.0, sh["start"] - 0.3), min(end, sh["end"] + 0.3),
                          a.sheet, os.path.join(out, "assets/fonts", family + ".ttf"))
    return 0


def contact_sheet(mp4, out, a, b, step, fontfile=None, cols=8, w=216):
    """Frames every `step` s over [a, b], labelled with their time, tiled."""
    n = max(1, int(math.floor((b - a) / step)) + 1)
    rows = int(math.ceil(n / cols))
    label = ""
    has_dt = "drawtext" in (hfcfg.run(["ffmpeg", "-hide_banner", "-filters"]).stdout or "")
    if has_dt and fontfile and os.path.exists(fontfile):
        label = (f",drawtext=fontfile='{fontfile}':text='%{{pts\\:flt\\:2}}':x=6:y=6:"
                 f"fontsize=22:fontcolor=yellow:box=1:boxcolor=black@0.5")
    vf = (f"trim=start={a:.3f}:end={b + 0.001:.3f},setpts=PTS-STARTPTS+{a:.3f}/TB,"
          f"fps=1/{step}:start_time={a:.3f},scale={w}:-2{label},tile={cols}x{rows}")
    r = hfcfg.run(["ffmpeg", "-v", "error", "-y", "-i", mp4, "-vf", vf, "-frames:v", "1", out])
    if r.returncode:
        print(f"  ! contact sheet failed: {r.stderr[-300:]}")
        return False
    print(f"  sheet → {out}  ({n} frames from {a:.2f}s every {step}s, left→right, top→bottom"
          f"{'' if label else '; this ffmpeg has no drawtext, so frames are unlabelled'})")
    return True


# ---------------------------------------------------------------------------- cli
def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("cmd", choices=["list", "preview", "selftest"])
    ap.add_argument("type", nargs="?", choices=TYPES)
    ap.add_argument("--entry", help="the moment as JSON (times in A-roll seconds)")
    ap.add_argument("--media", default=None, help="take the moment from media.json …")
    ap.add_argument("--id", default=None, help="… by its id")
    ap.add_argument("--aroll", default=None)
    ap.add_argument("--pad", type=float, default=1.2, help="A-roll seconds around the moment")
    ap.add_argument("--out", default=None)
    ap.add_argument("--render", action="store_true", help="also render a draft mp4")
    ap.add_argument("--sheet", type=float, default=0.0,
                    help="with --render: a contact sheet every N s across the moment")
    ap.add_argument("--no-captions", action="store_true")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "list":
        for t in TYPES:
            print(f"\n{t}")
            for k, v in FIELDS[t].items():
                print(f"  {k:15s} {v}")
            print("  example: " + json.dumps(EXAMPLES[t], ensure_ascii=False))
        print("\n  every entry: id, type, start, end (A-roll seconds). See references/moments.md")
        return 0
    if not a.type:
        sys.exit("preview needs a type: " + ", ".join(TYPES))
    hfcfg.require("ffmpeg", "ffprobe")
    return preview(a)


if __name__ == "__main__":
    sys.exit(main())
