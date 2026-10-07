#!/usr/bin/env python3
"""Kinetic headlines — the key sentence builds on screen word by word, as it is spoken.

WHY THIS IS A GENERATOR. Premium talking-head reels (agency interview looks such as the Rollin reference) carry
one signature this skill could not make: on the punchiest sentence of a beat a 1-3 line
headline builds in sync with the voice, mixed weights, the keyword in the brand colour, held
until the sentence ends and then hard-cut. In the KO edits every word time was typed by hand
off words.json — and a re-cut stranded all of them. Here the times are DERIVED from
src/words.json, the geometry from the grid, the size from a real measurement in Chrome and
the colours from the brand tokens, so a headline survives every rebuild.

Like scripts/outro.py, this module returns FRAGMENTS (element specs, CSS, timeline lines,
caption-hide windows). build_index.py emits them through its own clip(), so every timed value
lands in build/expected.json and behind the END guard. It never writes index.html.

media.json contract — "headlines": [ ... ], each one:
  {"id": "h1",                      unique (auto h1, h2 ... when missing)
   "text": "הטעות הזאת / *עולה לכם כסף.*",   the MARKUP below — or "lines" (the list form)
   "start": 9.04, "end": 10.8,      optional: found in src/words.json when missing (end =
                                    the next spoken word, i.e. it holds to the sentence end)
   "style": "rollin" | "classic" | "bold",   default: config style.kinetic when it names a style,
                                    else "rollin"
   "on": "footage" | "paper",       paper = ink + --hl-on-light, for a light surface
   "place": "chest" | "upper" | "center",  or "top": px — clamped into the safe zone;
                                    "chest" (default) hangs under the chin from
                                    build/framing.json (framing_map.py), else the face measured
                                    in the window — through the punch-in scale of the window
   "behind": true,                  optional: the stack goes BETWEEN the wall and the person
                                    (A-roll < words < the speaker's matte of this window);
                                    default place "upper"; needs the matte (below)
   "matte": "assets/matte_h1.webm", the matte for "behind" (default assets/matte_<id>.webm);
                                    missing / stale / wrong length → the build stops and prints
                                    the exact ffmpeg + scripts/matte.py commands
   "size": px,                      nominal size of a full line; auto-fit only ever SHRINKS it
   "font": "brand" | "display",     default brand (needs the 300-800 weight range)
   "hide_captions": true,           default true: the caption layer is hidden over the window
   "scrim": "auto" | true | false,  auto: a soft dark scrim when the footage behind is bright
   "times": [t, ...]}               optional per-word override (skips the transcript match)

  "lines" (list form): [[["לא", "thin"], ["במהות", "thin"]], [["פרסונל", "partner"],
                         ["ברנד", "key"]]] — role per word, "<role> sm" marks a small line,
                         an optional third element is that word's land time.

MARKUP (one string, verbatim words only — emphasis by weight and colour, never by rewording)
  " / "        line break (a slash between two digits, as in 24/7, is text)
  *w*          keyword: brand colour (var(--hl-on-dark)), 800
  ^w^          partner: a lighter tint of the keyword colour, 500
  +w+          bold white, 800
  _w_          thin white, 200
  =w=          gradient closer (the emotional word): electric → lavender → pink, 800
  ~w~          a SMALL line (0.6x) — the lead-in ("הרבה שואלים:"); marks the whole line
  plain        the style's default: thin for rollin / ko, bold for bold
  Markers may span words (*פרסונל ברנד*); a lone trailing marker (ברנד*) marks one word.

Word timing: every headline word is matched, in order, to the transcript words inside the
window by normalised text (niqqud, punctuation, maqaf and quotes ignored; the transcriber's
split "ה" + "-AI" is merged first, exactly as captions.py does). A word LANDS at its spoken
start minus the cutting lead (so it has resolved as the sound arrives), snapped to a frame.
A word that cannot be found is spread evenly between its matched neighbours, with a warning
— a missing word usually means the headline is not verbatim.

Usage
  python3 scripts/kinetic.py plan [--media media.json]     # word → time table + geometry
  python3 scripts/kinetic.py parse "לא / *פרסונל* ^ברנד^"     # show how markup parses
  python3 scripts/kinetic.py selftest                        # negative tests, placement
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402
import grid  # noqa: E402

FRAME = 0.04
ROLES = ("thin", "bold", "key", "partner", "grad")
ALIASES = {"t-thin": "thin", "light": "thin", "t-bold": "bold", "white": "bold",
           "t-gold": "key", "gold": "key", "keyword": "key", "kw": "key", "hl": "key",
           "t-ivory": "partner", "tint": "partner"}
# "+" for bold, not "!": a Hebrew sentence may END on "!" and must not turn bold.
MARK = {"*": "key", "^": "partner", "+": "bold", "_": "thin", "=": "grad"}
# "=w=" is the gradient closer (the emotional word): electric blue → lavender → pink.
MARKERS = "*^+_~="
SMALL = 0.6                                   # a "~" line, relative to a full line

# Per-style look. Sizes are px at 1080 wide for a FULL line; auto-fit only shrinks.
#   rollin: STYLE.md §4 — each word enters faint and grey and resolves to its colour in
#           ~0.2 s, no slide, no bounce; framing words thin, line height tight (~1.0).
#   ko:     kit.py word() — opacity/y 14/blur 6 → rest in 0.24 s, line height 1.18.
#   bold:   the Rollin "בא לי / לעקוב / אחריו" stack — all bold white, keyword coloured.
STYLES = {
    # the house look: each word lands from opacity .18 with blur 6 + grayscale to full in
    # 0.22 s power2.out — no slide, no bounce (the premium-reel spec, §4.2)
    "rollin": {"size": 112, "lh": 1.06, "default": "thin", "dur": 0.22,
               "from": '{ opacity: 0.18, filter: "blur(6px) grayscale(1)" }',
               "to": 'opacity: 1, filter: "blur(0px) grayscale(0)"'},
    "classic": {"size": 108, "lh": 1.18, "default": "thin", "dur": 0.24,
               "from": '{ opacity: 0, y: 14, filter: "blur(6px)" }',
               "to": 'opacity: 1, y: 0, filter: "blur(0px)"'},
    "bold":   {"size": 104, "lh": 1.04, "default": "bold", "dur": 0.18,
               "from": '{ opacity: 0, filter: "grayscale(1) blur(0px)" }',
               "to": 'opacity: 1, filter: "grayscale(0) blur(0px)"'},
}
WEIGHT = {"thin": 200, "bold": 800, "key": 800, "partner": 500, "grad": 800}
SIZE_WORDS = {"big": 140, "huge": 170}      # the spec's .big / .huge, at 1080 wide

# Where the stack hangs, as the TOP of the block on a 1920-high frame. "chest" hangs the
# stack under the CHIN as it is on screen in the headline's window: from build/framing.json
# (scripts/framing_map.py, measured over the take, sanity-checked) when it exists, else the
# face measured on the A-roll inside the window (the outro's skin mask) — 980 only when
# neither finds a face. Both go through the A-roll's transform in that window (beat scale x
# the biggest punch-in), because a punch moves the chin down onto a stack placed for scale 1.
# If it cannot fit there it goes "upper" (above the head) when that is clear, else it warns.
PLACES = {"chest": 980, "upper": 300}
CHIN = 1.0           # chin ≈ face centre + 1.0 face widths (skin-mask centre sits 0.62 below
                     # the hairline edge; a face is ~1.35 widths tall, a beard adds ~0.25 that
                     # the skin mask cannot see). 0.74, then 0.85 both left the first line on
                     # a real bearded take (beard bottom measured at centre + 1.0 widths); a
                     # clean chin just gets a little more air
CLEAR = 36           # px of air between the chin and the first line

# Readability on footage. The reference look is white thin type with only a soft shadow —
# fine on a dark shirt, invisible on a white one or a bright wall. The region behind each
# stack is MEASURED on the A-roll; when it is bright a soft dark radial scrim is laid
# behind the type (data-grid="bleed" decoration, no box, no edge).
SCRIM_LUMA = 0.52    # 75th-percentile luma (0-1) of the region above which the scrim goes in

LATIN = re.compile(r"^([^A-Za-z]*)([A-Za-z][A-Za-z'’0-9.\-]*)(.*)$")
AI_LIKE = re.compile(r"^[A-Z0-9]*I[A-Z0-9]*$")
NIQQUD = re.compile(r"[֑-ׇ]")
PUNCT = re.compile(r"[\s.,?!…:;\"'“”„״׳()\[\]{}\-–—־/]")


# ------------------------------------------------------------------ markup
def norm(w):
    """Text used to MATCH a headline word to a spoken word: no niqqud, no punctuation, no
    maqaf/hyphen, no quotes, lower case. "ה-AI", "הAI" and "ה AI" all become "הai"."""
    return PUNCT.sub("", NIQQUD.sub("", str(w))).lower()


def _role(r):
    r = str(r or "").strip().lower()
    return ALIASES.get(r, r)


def parse_markup(text, default="thin"):
    """Markup → [{"small": bool, "words": [{"text", "role"}]}].

    Lines split on " / " (or a newline). A slash between two digits stays text. Markers open
    on a word's start and close on a word's end; one left open runs to the end of the line
    (and is reported), a lone closing marker marks just its own word.
    """
    warn = []
    lines = []
    for raw in re.split(r"\s*\n\s*|\s*(?<![0-9])/(?![0-9])\s*", str(text).strip()):
        if not raw.strip():
            continue
        words, small, open_role = [], False, None
        for tok in raw.split():
            role, closes = open_role, False
            lead = ""
            while tok and tok[0] in MARKERS:
                lead += tok[0]
                tok = tok[1:]
            trail = ""
            while tok and tok[-1] in MARKERS:
                trail = tok[-1] + trail
                tok = tok[:-1]
            if "~" in lead or "~" in trail:
                small = True
            for m in lead.replace("~", ""):
                role = open_role = MARK[m]
            for m in trail.replace("~", ""):
                if open_role is None:
                    role = MARK[m]                     # a lone trailing marker: this word
                closes = True
            if not tok:
                continue
            words.append({"text": tok, "role": role or default})
            if closes:
                open_role = None
        if open_role:
            warn.append(f"marker left open in {raw!r} — it ran to the end of the line")
        if words:
            lines.append({"small": small, "words": words})
    return lines, warn


def parse_lines(spec, default="thin"):
    """The list form: [[[word, role(, t)], ...], ...] — role may carry ' sm'."""
    lines, times = [], []
    for ln in spec:
        words, small = [], False
        for item in ln:
            if isinstance(item, str):
                item = [item, default]
            w, r = item[0], (item[1] if len(item) > 1 else default)
            parts = str(r).replace(",", " ").split()
            if any(p in ("sm", "small") for p in parts):
                small = True
            role = next((_role(p) for p in parts if _role(p) in ROLES), default)
            words.append({"text": str(w), "role": role})
            times.append(float(item[2]) if len(item) > 2 and item[2] is not None else None)
        lines.append({"small": small, "words": words})
    return lines, times


def word_html(text):
    """One word, escaped, with a Latin run isolated in <bdi class="ltr"> — and "AI"-like
    acronyms also class "ai" (in a heavy Hebrew face a capital I has no serif and "AI" reads
    "Al"). Verbatim: the Latin is never rewritten to dodge bidi, it is isolated instead."""
    m = LATIN.match(text)
    if not m:
        return _html.escape(text)
    pre, lat, post = m.groups()
    cls = "ltr ai" if AI_LIKE.match(lat) else "ltr"
    return f'{_html.escape(pre)}<bdi class="{cls}">{_html.escape(lat)}</bdi>{_html.escape(post)}'


# ------------------------------------------------------------------ timing
def snap(t):
    return round(round(t / FRAME) * FRAME, 3)


def load_transcript(cfg, path):
    """src/words.json, with the transcriber's split tokens merged exactly as captions.py
    merges them — so "ה" + "-AI" is ONE word for the matcher, as it is on the caption."""
    if not os.path.exists(path):
        return []
    import captions
    return captions.normalise(captions.load_words(path), cfg["language"])


def _greedy(hw, tw, k0, k1, skip=3):
    """Match headline words hw (normed) in order against transcript words tw[k0:k1].
    Returns [index or None] per headline word. A spoken word may be skipped (up to `skip`
    in a row) — a headline can leave out a filler — but order is never broken. A spoken
    word that only ENDS with the headline word (a prefix particle: "ה-AI" for "AI") counts
    as a weak match, encoded as -(index + 1), and is reported as not verbatim."""
    out, k = [], k0
    for w in hw:
        hit = None
        rng = range(k, min(k1, k + skip + 1))
        for j in rng:
            if tw[j] == w:
                hit = j
                break
        if hit is None and len(w) >= 2:
            for j in rng:
                if tw[j].endswith(w) and len(tw[j]) - len(w) <= 3:
                    hit = -(j + 1)
                    break
        out.append(hit)
        if hit is not None:
            k = (-hit - 1 if hit < 0 else hit) + 1
    return out


def _score(m):
    return sum(1.0 if x >= 0 else 0.5 for x in m if x is not None)


def align(words, trans, start=None, end=None):
    """Find each headline word in the transcript. With a window ("start"/"end") the search
    stays inside it and prefers the match whose first word is nearest "start"; without one
    the whole transcript is searched and a phrase said twice is reported.
    Returns (indices, notes, searched)."""
    notes = []
    hw = [norm(w["text"]) for w in words]
    tw = [norm(t[2]) for t in trans]
    if start is not None:
        lo, hi = float(start) - 0.6, (float(end) + 0.1 if end is not None else 1e9)
        cand = [i for i, t in enumerate(trans) if lo <= t[0] <= hi]
    else:
        cand = list(range(len(trans)))
    if not cand:
        return [None] * len(words), ["no transcript words in the window"], start is None
    k1 = cand[-1] + 1
    best, best_key, full = None, None, set()
    for k in cand:
        m = _greedy(hw, tw, k, k1)
        sc = _score(m)
        first = next((x for x in m if x is not None), None)
        t0 = trans[-first - 1 if first is not None and first < 0 else first][0] \
            if first is not None else 1e9
        key = (sc, -abs(t0 - float(start)) if start is not None else -k)
        if sc == len(words):
            full.add(tuple(m))
        if best_key is None or key > best_key:
            best, best_key = m, key
    if start is None and len(full) > 1:
        notes.append(f"the phrase occurs {len(full)}x in the transcript — took the first; give "
                     f"\"start\" to pick another")
    return best, notes, start is None


def word_times(words, trans, hl, lead, end_cap, bounds=()):
    """Land time per word + the headline window. Returns (times, start, end, table, warns)."""
    warns = []
    start = hl.get("start")
    end = hl.get("end")
    idx, notes, searched = align(words, trans, start, end)
    warns += notes
    table = []
    raw = []
    for w, k in zip(words, idx):
        if k is None:
            raw.append(None)
            table.append((w["text"], None, "NOT FOUND"))
            continue
        j = -k - 1 if k < 0 else k
        raw.append(trans[j][0])
        table.append((w["text"], trans[j][0], trans[j][2] + ("  (not verbatim)" if k < 0 else "")))
    missing = [w["text"] for w, t in zip(words, raw) if t is None]
    if missing:
        warns.append(f"word(s) not found in src/words.json: {missing} — a headline must be "
                     f"the speaker's exact words; spread evenly instead")
    nv = [r[0] for r, k in zip(table, idx) if k is not None and k < 0]
    if nv:
        warns.append(f"matched only as part of a spoken word: {nv} — write the word as spoken "
                     f"(e.g. ה-AI, not AI)")

    matched = [t for t in raw if t is not None]
    if start is None:
        if not matched:
            raise SystemExit(f"headline {hl.get('id')}: no word found in src/words.json and no "
                             f"\"start\" given — give start/end or fix the text")
        # like a caption card: lead the first word, but never back across the cut it
        # sits behind (a headline that opens a segment starts ON the boundary)
        w0 = matched[0]
        cut = max([x for x in bounds if x <= w0 + 0.20], default=0.0)
        start = max(w0 - lead, cut)
    start = snap(max(0.0, float(start)))
    if end is None:
        # Hold to the end of the sentence: the cut lands on the next segment boundary when
        # the cut falls in the pause (the shot change), else where the next caption card
        # starts (next word − lead). Never more than 1.2 s of hold over silence.
        last = max(i for i, k in enumerate(idx) if k is not None) if matched else None
        if last is not None:
            j = -idx[last] - 1 if idx[last] < 0 else idx[last]
            last_end = trans[j][1]
            nxt = trans[j + 1][0] if j + 1 < len(trans) else end_cap
            cut = [x for x in bounds if last_end - 0.02 <= x <= nxt + 0.02]
            # the next caption card starts at the boundary or at its first word − lead
            # (captions.py), so the headline hands the frame back on exactly that frame
            end = min(cut) if cut else nxt - lead
            end = min(end, last_end + 1.2)
        else:
            end = start + 2.0
    end = snap(min(float(end), end_cap))
    if end <= start + FRAME:
        raise SystemExit(f"headline {hl.get('id')}: window {start}-{end} is empty")
    # Captions are hidden under the headline, so a spoken word inside the window that is
    # NOT in the headline is never shown anywhere. Say which, and where the window should end.
    used = {(-k - 1 if k < 0 else k) for k in idx if k is not None}
    lost = [(i, trans[i]) for i in range(len(trans))
            if start + 0.02 <= trans[i][0] < end - 0.02 and i not in used]
    if lost:
        first = lost[0][1]
        warns.append(f"spoken word(s) {[w for _, (_, _, w) in lost]} fall inside the window but "
                     f"are not in the headline — they would never be seen. End it at "
                     f"{first[0] - 0.02:.2f}s (before \"{first[2]}\") or add them to the text")

    # land = spoken start − lead (resolved as the sound arrives), clamped into the window
    times = [None if t is None else max(start, snap(t - lead)) for t in raw]
    if not matched:
        span = (end - start) * 0.7
        times = [snap(start + span * i / max(1, len(words))) for i in range(len(words))]
    else:
        # fill gaps: between matched neighbours, evenly; before the first / after the last,
        # one 0.16 s step at a time
        n = len(times)
        for i in range(n):
            if times[i] is not None:
                continue
            a = next((j for j in range(i - 1, -1, -1) if times[j] is not None), None)
            b = next((j for j in range(i + 1, n) if times[j] is not None), None)
            if a is not None and b is not None:
                times[i] = snap(times[a] + (times[b] - times[a]) * (i - a) / (b - a))
            elif a is not None:
                times[i] = snap(times[a] + 0.16 * (i - a))
            else:
                times[i] = snap(max(start, times[b] - 0.16 * (b - i)))
    for i in range(1, len(times)):                         # never out of reading order
        times[i] = max(times[i], times[i - 1])
    last_ok = end - 0.2
    if times and times[-1] > last_ok:
        warns.append(f"the last word lands {times[-1]:.2f}s, only {end - times[-1]:.2f}s before "
                     f"the cut at {end:.2f}s — it will barely read")
    return times, start, end, table, warns


# ------------------------------------------------------------------ measuring
MEASURE_PAGE = """<!doctype html><meta charset="utf-8"><style>
{faces}
body {{ margin:0; background:#000; }}
.kh {{ position:absolute; right:0; top:0; direction:rtl; text-align:right; white-space:nowrap;
      font-family:"{family}", "Inter", sans-serif; }}
.kl {{ display:block; }} .kl.sm {{ font-size:{small}em; }}
.kw {{ display:inline-block; }}
.ltr {{ unicode-bidi:isolate; direction:ltr; }}
.ai {{ font-family:"Roboto Slab", serif; letter-spacing:.02em; font-weight:inherit; }}
.r-thin {{ font-weight:200; }} .r-partner {{ font-weight:500; }} .r-bold, .r-key, .r-grad {{ font-weight:800; }}
</style><div id="stage"></div><div id="out"></div>
<script>
const H = {heads};
const res = [];
document.fonts.ready.then(() => {{
  for (const h of H) {{
    const d = document.createElement('div');
    d.className = 'kh'; d.style.fontSize = h.size + 'px'; d.style.lineHeight = h.lh;
    d.innerHTML = h.html;
    document.getElementById('stage').appendChild(d);
    const ls = [...d.querySelectorAll('.kl')].map(l => {{
      const r = document.createRange(); r.selectNodeContents(l);
      return Math.ceil(r.getBoundingClientRect().width);
    }});
    res.push({{ id: h.id, lines: ls, height: Math.ceil(d.getBoundingClientRect().height) }});
    d.remove();
  }}
  document.getElementById('out').textContent = JSON.stringify(res);
}});
</script>"""


def _model_width(line, size):
    """Fallback when Chrome is not available: ~0.55 em per char for 800, ~0.48 for 300."""
    w = 0.0
    for x in line["words"]:
        w += len(x["text"]) * (0.48 if x["role"] == "thin" else 0.55) + 0.26
    return w * size * (SMALL if line["small"] else 1.0)


def measure(cfg, heads, family, workdir="build"):
    """Measure every headline's lines and block height in the real face, headless Chrome —
    the same method as fit_captions.py. Returns {id: {"lines": [px], "height": px}}."""
    import fonts
    font_dir = cfg["brand"]["font_dir"]
    fams = [family, "Roboto Slab", "Inter"]
    try:
        if fonts.ensure(fams, font_dir):
            raise RuntimeError("fonts missing")
        faces = fonts.font_faces_css(fams, font_dir,
                                     url_prefix="file://" + os.path.abspath(font_dir) + "/")
        chrome = hfcfg.chrome_path()
    except (Exception, SystemExit) as e:                   # noqa: BLE001
        print(f"  ! kinetic: cannot measure in Chrome ({e}) — using the char-width model")
        return None
    page = MEASURE_PAGE.format(faces=faces, family=family, small=SMALL,
                               heads=json.dumps(heads, ensure_ascii=False))
    os.makedirs(workdir, exist_ok=True)
    tmp = os.path.abspath(os.path.join(workdir, "_kinetic_fit.html"))
    open(tmp, "w", encoding="utf-8").write(page)
    r = subprocess.run([chrome, "--headless", "--disable-gpu", "--no-sandbox",
                        "--allow-file-access-from-files", "--virtual-time-budget=3000",
                        "--dump-dom", "file://" + tmp], capture_output=True, text=True)
    m = re.search(r'id="out">(.*?)</div>', r.stdout, re.S)
    if not m or not m.group(1).strip():
        print("  ! kinetic: Chrome returned no measurement — using the char-width model")
        return None
    return {row["id"]: row for row in json.loads(_html.unescape(m.group(1)))}


# ------------------------------------------------------------------ plan
STYLE_ALIASES = {"ko": "classic"}          # older configs keep working


def default_style(cfg):
    k = (cfg.get("style") or {}).get("kinetic")
    k = STYLE_ALIASES.get(k, k) if isinstance(k, str) else k
    return k if isinstance(k, str) and k in STYLES else "rollin"


def plan(cfg, media, end_cap, beatmap=None, bounds=(), words_path="src/words.json",
         framing_path=None):
    """Everything build_index.py needs for media.json "headlines", or None when there are none.
    framing_path: build/framing.json (scripts/framing_map.py) — default: that file when it
    exists; "chest" placement then uses its measured chin / head / chest.

    Returns {"elements": [{tag,id,cls,start,dur,inner,extra}], "css": str, "js": [str],
             "hide": [[a, b, id]], "table": {id: [(word, t_land, spoken)]}, "warnings": [str]}
    """
    heads = media.get("headlines") or []
    if not heads:
        return None
    W, H = cfg["project"]["width"], cfg["project"]["height"]
    k = H / 1920.0
    G = grid.from_config(cfg)
    gx0, gy0, gx1, gy1 = G["safe"]
    right_x = W - G["headline_right_margin"]
    avail = right_x - gx0
    lead = float(cfg.get("cutting", {}).get("lead", 0.06))
    b = cfg["brand"]
    trans = load_transcript(cfg, words_path)
    warnings = []
    if not trans:
        warnings.append(f"{words_path} not found — every headline is spread evenly; give "
                        f"start/end and check the timing by eye")

    items = []
    for n, hl in enumerate(heads, 1):
        hl = dict(hl)
        hid = str(hl.get("id") or f"h{n}")
        hl["id"] = hid
        style = hl.get("style") or default_style(cfg)
        style = STYLE_ALIASES.get(style, style)
        if style not in STYLES:
            raise SystemExit(f"headline {hid}: style {style!r} — choose {', '.join(STYLES)}")
        S = STYLES[style]
        if hl.get("lines"):
            lines, forced = parse_lines(hl["lines"], S["default"])
            pw = []
        elif hl.get("text"):
            lines, pw = parse_markup(hl["text"], S["default"])
            forced = [None] * sum(len(x["words"]) for x in lines)
        else:
            raise SystemExit(f"headline {hid}: give \"text\" (markup) or \"lines\"")
        warnings += [f"{hid}: {w}" for w in pw]
        words = [w for ln in lines for w in ln["words"]]
        if len(lines) > 3:
            warnings.append(f"{hid}: {len(lines)} lines — the look is 1-3 lines")
        if hl.get("times"):
            forced = [float(t) for t in hl["times"]] + [None] * len(words)
            forced = forced[:len(words)]
        times, st, en, table, ws = word_times(words, trans, hl, lead, end_cap, bounds)
        if any(t is not None for t in forced):
            times = [snap(f) if f is not None else t for f, t in zip(forced, times)]
            if hl.get("start") is None:
                st = min(st, min(times))
        warnings += [f"{hid}: {w}" for w in ws]
        # a number must not end a line with its unit on the next one ("20 / אלף")
        import captions
        lean = set(captions.LEAN_BACK.get(cfg["language"].get("code", ""), set()))
        for a_, b_ in zip(lines, lines[1:]):
            last, first = a_["words"][-1]["text"], b_["words"][0]["text"]
            if re.search(r"\d[\d.,%]*$", last) and (norm(first) in {norm(x) for x in lean}):
                warnings.append(f"{hid}: '{last}' / '{first}' — a number and its unit belong "
                                f"on ONE line")
        items.append({"hl": hl, "id": hid, "style": style, "S": S, "lines": lines,
                      "words": words, "times": times, "start": st, "end": en, "table": table})

    items.sort(key=lambda x: x["start"])
    for a_, b_ in zip(items, items[1:]):
        if b_["start"] < a_["end"] - 1e-6:
            raise SystemExit(f"headlines {a_['id']} ({a_['start']}-{a_['end']}) and {b_['id']} "
                             f"({b_['start']}-{b_['end']}) overlap — never more than one on screen")
    # house density for a ~45-60 s monologue: 5-7 headlines on the punchiest phrases
    # (references/storyboard.md). Far more and they stop meaning anything.
    if len(items) > 8:
        warnings.append(f"{len(items)} headlines — 5-7 per reel; on every sentence they stop "
                        f"meaning anything")

    # ---- size: measure each block at its nominal size, then shrink to the safe width
    fam_key = {}
    for it in items:
        fam = (b.get("display_family") or b["font_family"]) \
            if it["hl"].get("font") == "display" else b["font_family"]
        it["family"] = fam
        sz = it["hl"].get("size") or it["S"]["size"]
        sz = SIZE_WORDS.get(sz, sz) if isinstance(sz, str) else sz
        it["nominal"] = round(float(sz) * W / 1080.0)
        it["inner"] = inner_html(it)
        fam_key.setdefault(fam, []).append(it)
    for fam, its in fam_key.items():
        got = measure(cfg, [{"id": it["id"], "size": it["nominal"], "lh": it["S"]["lh"],
                             "html": it["inner"]} for it in its], fam)
        for it in its:
            m = (got or {}).get(it["id"])
            if m:
                widths, height = m["lines"], m["height"]
            else:
                widths = [_model_width(ln, it["nominal"]) for ln in it["lines"]]
                height = sum(it["nominal"] * (SMALL if ln["small"] else 1) * it["S"]["lh"]
                             for ln in it["lines"])
            widest = max(widths) if widths else 1
            scale = min(1.0, avail / float(widest)) if widest else 1.0
            it["size"] = int(it["nominal"] * scale)
            it["height"] = int(round(height * scale)) + 2
            it["widest"] = int(round(widest * scale))
            if scale < 0.999:
                warnings.append(f"{it['id']}: widest line {int(widest)}px at {it['nominal']}px "
                                f"→ fitted to {it['size']}px (safe width {avail}px)")
            if it["size"] < 64 * W / 1080:
                warnings.append(f"{it['id']}: only {it['size']}px after fitting — break the long "
                                f"line with ' / '")

    # ---- vertical placement, inside the safe zone; off the caption band unless hidden
    plate_h = grid.plate_height(b["caption_size"])
    aroll = media.get("aroll")
    aroll = aroll if aroll and os.path.exists(aroll) else None
    floor = gy1 - round(30 * k)
    fmap, fnotes = load_framing(framing_path, W, H, aroll)
    warnings += fnotes
    for it in items:
        hl = it["hl"]
        hide = hl.get("hide_captions", True) is not False
        it["hide"] = hide
        h = it["height"]
        # the A-roll's transform while this headline is up: beat scale x the largest punch
        sc_, yoff = footage_state(media, beatmap, it["start"], it["end"])
        it["footage_scale"] = sc_
        geo = face_geometry(fmap, aroll, it["start"], it["end"], W, H, sc_, yoff)
        it["face"] = geo
        it["placed_by"] = geo["source"] if geo else "fixed (no credible face)"
        if not geo and aroll and hl.get("place", "chest") == "chest" and hl.get("top") is None:
            warnings.append(f"{it['id']}: no credible face measured in the window — \"chest\" "
                            f"falls back to y {PLACES['chest']}; run `python3 "
                            f"scripts/framing_map.py {aroll}` and LOOK at the frame")
        if hl.get("top") is not None:
            top = float(hl["top"])
        else:
            place = hl.get("place", "upper" if hl.get("behind") else "chest")
            if place == "center":
                top = (gy0 + gy1) / 2 - h / 2
            elif place in PLACES:
                top = PLACES[place] * k
            else:
                raise SystemExit(f"headline {it['id']}: place {place!r} — chest | upper | center, "
                                 f"or give \"top\"")
            if geo and place == "chest":
                chin, crown = geo["chin"], geo["crown"]
                # top = under the chin as it is ON SCREEN in this window (punch-in applied);
                # the measured chest band is the room it has below that
                top = chin + CLEAR * k
                low = floor if not geo.get("chest") else min(floor, max(geo["chest"][1], chin))
                if top + h > floor:
                    up = PLACES["upper"] * k
                    if up + h <= crown - CLEAR * k:
                        warnings.append(f"{it['id']}: no room under the chin (y {int(chin)}) — "
                                        f"placed above the head instead")
                        top = up
                    else:
                        warnings.append(f"{it['id']}: {h}px stack has no clear band (chin y "
                                        f"{int(chin)}, crown y {int(crown)}) — it will touch "
                                        f"the face; use fewer lines or a smaller \"size\"")
                elif top + h > low + 1:
                    warnings.append(f"{it['id']}: the stack (y {int(top)}-{int(top + h)}) runs "
                                    f"past the measured chest band (to y {int(low)}) — onto hands "
                                    f"/ the desk; check the frame, or use fewer lines")
        if top + h > floor:
            top = floor - h
        if not hide:
            cap_top = grid.slot_top(cfg, beatmap, it["start"])
            if top < cap_top + plate_h and top + h > cap_top - round(24 * k):
                new = cap_top - round(24 * k) - h
                warnings.append(f"{it['id']}: captions stay on — lifted the stack from y "
                                f"{int(top)} to {int(new)} to clear the caption plate at {cap_top}")
                top = new
        ceil_ = gy0 + round(10 * k)
        if top < ceil_:
            top = ceil_
            if top + h > floor:
                warnings.append(f"{it['id']}: {h}px tall does not fit the safe zone — fewer lines")
        it["top"] = int(round(top))
        if hl.get("behind"):                 # behind the speaker: overlap is the point
            it["matte"] = behind_gate(it, media, beatmap, aroll, cfg, right_x, warnings)
        elif geo:                            # whatever moved it last, say if it hits the face
            x0, x1 = right_x - it["widest"], right_x
            if it["top"] < geo["chin"] and it["top"] + h > geo["crown"] and \
                    x0 < geo["x1"] and x1 > geo["x0"]:
                warnings.append(f"{it['id']}: the stack (y {it['top']}-{it['top'] + h}, x "
                                f"{int(x0)}-{int(x1)}) overlaps the face box (y "
                                f"{int(geo['crown'])}-{int(geo['chin'])}, x {int(geo['x0'])}-"
                                f"{int(geo['x1'])}, {geo['source']}, footage x{sc_:g}) — move it "
                                f"(\"place\"/\"top\"), use fewer lines, or let it hide the captions")
        sc = hl.get("scrim", "auto")
        if hl.get("on") == "paper":
            sc = False
        if sc == "auto" and aroll:
            rect = (right_x - it["widest"], it["top"], right_x, it["top"] + h)
            lum = region_luma(aroll, (it["start"] + it["end"]) / 2, rect, W, H)
            it["luma"] = lum
            sc = lum is not None and lum > SCRIM_LUMA
            if sc:
                warnings.append(f"{it['id']}: the footage behind the stack is bright (luma "
                                f"{lum:.2f}) — soft scrim added for readability")
        it["scrim"] = sc is True

    # ---- fragments
    els, js, hide = [], [], []
    for it in items:
        hid, S = it["id"], it["S"]
        on = "paper" if it["hl"].get("on") == "paper" else "footage"
        st, en = it["start"], it["end"]
        style_attr = (f' style="right:{W - right_x}px; top:{it["top"]}px; '
                      f'font-size:{it["size"]}px;"')
        els.append({"tag": "div", "id": hid,
                    "cls": f"kh s-{it['style']} on-{on}" + (" f-display" if
                                                           it["hl"].get("font") == "display" else "")
                           + (" scrim" if it.get("scrim") else "")
                           + (" behind" if it.get("matte") else ""),
                    "start": st, "dur": round(en - st, 3), "extra": style_attr,
                    "inner": it["inner"]})
        if it.get("matte"):
            # the speaker's matte of exactly this window, OVER the headline: the words sit
            # between the wall and the person. Videos inclusive, timed divs exclusive (the
            # hook's one-frame rule), and the matte copies the A-roll's every transform.
            els.append({"tag": "video", "id": f"{hid}m", "cls": "kh-matte",
                        "start": st, "dur": round(en - st - FRAME, 3),
                        "extra": f' src="{it["matte"]}" muted playsinline'})
            for t, sc_, _ in footage_steps(media, beatmap, st, en):
                js.append(f'      tl.set("#{hid}m", {{ scale: {sc_:g} }}, {t});')
        js.append(f'      // headline {hid} ({it["style"]}): {" / ".join(" ".join(w["text"] for w in ln["words"]) for ln in it["lines"])}')
        for i, t in enumerate(it["times"]):
            # from opacity 0, not the reference's 0.15: a fromTo rewound past its start
            # renders its FROM state, and a backward seek must show nothing, not a ghost.
            js.append(f'      tl.fromTo("#{hid}w{i}", {S["from"]}, {{ {S["to"]}, '
                      f'duration: {S["dur"]}, ease: "power2.out", immediateRender: false }}, {t});')
        # hard cut with the sentence — no exit animation (and the lint wants the kill)
        js.append(f'      tl.set("#{hid}", {{ opacity: 0 }}, {en});')
        if it["hide"]:
            hide.append([st, en, hid])
    css = CSS.format(sm=SMALL, **{f"lh_{s}": STYLES[s]["lh"] for s in STYLES})
    return {"elements": els, "css": css, "js": js, "hide": hide,
            "table": {it["id"]: {"start": it["start"], "end": it["end"], "size": it["size"],
                                 "top": it["top"], "height": it["height"],
                                 "widest": it["widest"], "words": it["table"],
                                 "placed_by": it.get("placed_by"),
                                 "footage_scale": it.get("footage_scale"),
                                 "times": it["times"]} for it in items},
            "warnings": warnings}


FRAMING_JSON = "build/framing.json"
AROLL_ORIGIN_Y = 0.30   # build_index.py: #aroll { transform-origin: 50% 30% }
FACE_HALF_W = 0.62      # face box half width, in face widths (ears, hair, a beard's sides)


def load_framing(path, W, H, aroll=None):
    """build/framing.json → the numbers "chest" needs, in composition px at scale 1, or None.

    framing_map.py measures ~10 frames across the take; here every number is SANITY-CHECKED
    before it moves a headline, because a misread (a white shirt read as wall, a hand or a
    microphone read as the collar) is worse than no number. A chin outside 0.3-1.6 face widths
    under the face centre is dropped; a chest band that is upside down or above the chin is
    dropped. The chin used is the LOWEST credible one: the p80 of the per-frame chins (the
    head nods) and an anatomical floor, face centre + CHIN face widths (a beard hides the jaw
    from the skin mask, so the measured chin alone sits on the beard)."""
    notes = []
    path = path or FRAMING_JSON
    if not os.path.exists(path):
        return None, notes
    try:
        d = json.load(open(path, encoding="utf-8"))
        fcx, fcy, fw = float(d["face_cx"]), float(d["face_cy"]), float(d["face_w"])
        head = float(d.get("head_top") or fcy - 0.95 * fw)
    except (OSError, ValueError, KeyError, TypeError) as e:
        notes.append(f"{path} unreadable ({e}) — chest placement falls back to measuring the face")
        return None, notes
    if not (fw > 0 and 0 <= head < fcy < H):
        notes.append(f"{path}: implausible face (head {head}, centre {fcy}, width {fw}) — ignored")
        return None, notes
    if aroll and os.path.exists(aroll) and os.path.getmtime(path) < os.path.getmtime(aroll):
        notes.append(f"{path} is older than {aroll} — rerun `python3 scripts/framing_map.py "
                     f"{aroll}`; using it anyway")
    lo, hi = fcy + 0.3 * fw, fcy + 1.6 * fw
    chins = [float(x["chin"]) for x in d.get("samples") or [] if isinstance(x, dict)
             and isinstance(x.get("chin"), (int, float)) and lo <= x["chin"] <= hi]
    bad = [x.get("t") for x in d.get("samples") or [] if isinstance(x, dict)
           and isinstance(x.get("chin"), (int, float)) and not lo <= x["chin"] <= hi]
    if bad:
        notes.append(f"{path}: chin misread in {len(bad)} frame(s) (t {bad}) — those frames "
                     f"ignored; LOOK at build/framing_marked.png")
    measured = None
    if chins:
        chins.sort()
        measured = chins[min(len(chins) - 1, int(round(0.8 * (len(chins) - 1))))]
    elif isinstance(d.get("chin"), (int, float)) and lo <= d["chin"] <= hi:
        measured = float(d["chin"])
    faces = sorted(float(x["face_cy"]) for x in d.get("samples") or []
                   if isinstance(x, dict) and isinstance(x.get("face_cy"), (int, float)))
    fcy80 = faces[int(round(0.8 * (len(faces) - 1)))] if faces else fcy
    chest = d.get("chest")
    if isinstance(chest, list) and len(chest) == 2 and all(isinstance(v, (int, float)) for v in chest):
        if not (chest[0] < chest[1] and (measured or lo) - 0.1 * fw <= chest[0] <= hi + 0.3 * fw):
            notes.append(f"{path}: chest band {chest} is not a band under the chin — ignored "
                         f"(LOOK at build/framing_marked.png)")
            chest = None
    else:
        chest = None
    anat = fcy80 + CHIN * fw
    cap = ""
    if chest and anat > chest[0]:
        anat, cap = float(chest[0]), f", capped at the collar {int(chest[0])}"
    chin = max(anat, measured or 0.0)
    why = (f"measured chin p80 {int(measured)}" if measured and measured >= anat else
           f"face centre p80 {int(fcy80)} + {CHIN} x face width {int(fw)}{cap}"
           + ((f" (measured chin {int(measured)} is higher: the skin mask stops at a beard "
               f"or the jaw shadow)" if not cap else f" (measured chin {int(measured)})")
              if measured else ""))
    return {"cx": fcx, "head": head, "chin": chin, "w": fw, "chest": chest,
            "why": why, "src": path}, notes


def footage_steps(media, beatmap, start, end):
    """[(t, scale, y)] — the A-roll's transform at the window start and at every change
    inside [start, end): the beat map's state (build_index.py: 1.02 on every non-hook beat,
    y 770 on a panel) times the factor of any "punch" moment step (moments.py)."""
    beats = getattr(beatmap, "BEATS", None) if beatmap else None
    try:
        import moments
        state_at = moments.make_state_at(beats)
    except Exception:                                      # noqa: BLE001
        def state_at(t):
            return (1.0, 0.0)
    marks = {float(start)}
    for bs, kind, *_ in (beats or []):
        if start < float(bs) < end:
            marks.add(float(bs))
    punches = []
    for m in media.get("moments") or []:
        if m.get("type") != "punch":
            continue
        try:
            a, b_ = float(m["start"]), float(m["end"])
            steps = (sorted((float(t), float(f)) for t, f in m["steps"]) if m.get("steps")
                     else [(a, float(m.get("scale", 1.1)))])
        except (KeyError, TypeError, ValueError):
            continue
        punches.append((a, b_, steps))
        for t in [t for t, _ in steps] + [b_]:
            if start < t < end:
                marks.add(t)
    out = []
    for t in sorted(marks):
        s, y = state_at(t)
        f = 1.0
        for a, b_, steps in punches:
            if a - 1e-6 <= t < b_ - 1e-6:
                for ts, k_ in steps:
                    if t >= ts - 1e-6:
                        f = k_
        out.append((round(t, 3), round(s * f, 4), y))
    return out


def footage_state(media, beatmap, start, end):
    """(scale, y offset) of the A-roll at its LARGEST during [start, end] — the chin is
    lowest at the biggest punch, and a headline must clear it there too."""
    steps = footage_steps(media, beatmap, start, end)
    s, _, y = max(((sc, -t, y) for t, sc, y in steps), default=(1.0, 0, 0.0))
    return s, y


BEHIND_MAX_COVER = 0.45  # more of the stack than this hidden by the person = unreadable


def behind_commands(aroll, hid, st, en, W, H, matte):
    """The exact commands that produce the matte a "behind" headline needs."""
    import shlex
    span = f"build/span_{hid}.mp4"
    return (f"  mkdir -p build && ffmpeg -v error -y -ss {st:.3f} -i {shlex.quote(aroll)} "
            f"-t {en - st:.3f} -an "
            f"-vf scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H} "
            f"-c:v libx264 -crf 12 -pix_fmt yuv420p {span}\n"
            f"  python3 scripts/matte.py {span} {shlex.quote(matte)}")


def behind_gate(it, media, beatmap, aroll, cfg, right_x, warnings):
    """Checks for "behind": true, and the matte path. Stops the build (with the commands
    that fix it) rather than render words OVER the face or a matte out of sync:
      * the A-roll exists and the window is a full-screen beat (a panel moves the A-roll
        770 px down; the matte would have to follow two transforms);
      * nothing else covers the A-roll in the window (B-roll, a designed moment, the hook —
        the matte would paint the speaker over them), punch-ins are fine (mirrored);
      * the matte exists, is W x H, covers the window (± 2 frames) and is newer than the
        A-roll (a re-cut moves the window: re-matte, as matte.py says).
    Then warns when the speaker hides more than BEHIND_MAX_COVER of the stack."""
    hid, hl = it["id"], it["hl"]
    st, en = it["start"], it["end"]
    W, H = cfg["project"]["width"], cfg["project"]["height"]
    matte = hl.get("matte") or f"assets/matte_{hid}.webm"
    if not aroll:
        raise SystemExit(f"headline {hid}: \"behind\" needs media.json \"aroll\" (the A-roll file)")
    cmds = behind_commands(aroll, hid, st, en, W, H, matte)
    hook = media.get("hook") or {}
    if hook and st < float(hook.get("end", 0)) - 1e-6:
        raise SystemExit(f"headline {hid}: \"behind\" inside the hook (0-{hook['end']}s) — the "
                         f"hook already has its own matte (media.json hook.matte); use that")
    for c in media.get("broll") or []:
        a, d = float(c.get("start", 0)), float(c.get("duration", 0))
        if a < en - 1e-6 and a + d > st + 1e-6:
            raise SystemExit(f"headline {hid}: \"behind\" while B-roll {c.get('id')} is up "
                             f"({a:.2f}-{a + d:.2f}s) — the matte would paint the speaker over it")
    for m in media.get("moments") or []:
        if m.get("type") == "punch":
            continue
        try:
            a, b_ = float(m["start"]), float(m["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if a < en - 1e-6 and b_ > st + 1e-6:
            raise SystemExit(f"headline {hid}: \"behind\" overlaps moment {m.get('id')} "
                             f"({m.get('type')}, {a:.2f}-{b_:.2f}s) — move one of them")
    if any(abs(y) > 1 for _, _, y in footage_steps(media, beatmap, st, en)):
        raise SystemExit(f"headline {hid}: \"behind\" on a panel beat — the A-roll is moved down "
                         f"770 px there; use it on a full-screen beat")
    if not os.path.exists(matte):
        raise SystemExit(f"headline {hid}: \"behind\" needs a person matte of the A-roll window "
                         f"{st:.2f}-{en:.2f}s at {matte}. Make it (torch; ~1-3 min per 10 s on "
                         f"Apple silicon):\n{cmds}\n  then LOOK at the {matte[:-5]}_check.png "
                         f"matte.py writes (hair strands must resolve)")
    try:
        wh = hfcfg.probe(matte, "stream=width,height", stream="v:0")
        mw, mh = (int(v) for v in wh.split(",")[:2])
        md = float(hfcfg.probe(matte, "format=duration") or 0)
    except (ValueError, TypeError, AttributeError):
        raise SystemExit(f"headline {hid}: cannot read {matte} — remake it:\n{cmds}")
    if (mw, mh) != (W, H):
        raise SystemExit(f"headline {hid}: {matte} is {mw}x{mh}, the composition is {W}x{H} — "
                         f"remake it from the cropped span:\n{cmds}")
    if abs(md - (en - st)) > 2 * FRAME + 0.02:
        raise SystemExit(f"headline {hid}: {matte} lasts {md:.2f}s but the window is "
                         f"{en - st:.2f}s ({st:.2f}-{en:.2f}) — the window moved (a re-cut, an "
                         f"edited start/end); re-matte:\n{cmds}")
    if os.path.getmtime(matte) < os.path.getmtime(aroll):
        raise SystemExit(f"headline {hid}: {matte} is older than {aroll} (a re-cut?) — re-matte "
                         f"the window:\n{cmds}")
    cover = matte_cover(matte, (right_x - it["widest"], it["top"], right_x,
                                it["top"] + it["height"]),
                        (en - st) / 2.0, W, H, it.get("footage_scale") or 1.0)
    it["behind_cover"] = cover
    if cover is not None and cover > BEHIND_MAX_COVER:
        warnings.append(f"{hid}: the speaker hides {cover * 100:.0f}% of the \"behind\" stack (mid "
                        f"window) — it will not read; move it (\"place\": \"upper\" / \"top\") so "
                        f"the words pass BEHIND the head, not under the body")
    return matte


def matte_cover(matte, rect, t, W, H, scale=1.0):
    """Share of rect (composition px) where the matte's alpha is solid (> 50 %) at t into the
    matte, with the A-roll's scale about 50% 30% undone. None when it cannot be read. The
    explicit libvpx-vp9 decoder is what keeps the alpha (matte.py's note)."""
    sw, sh = 108, 192
    try:
        r = hfcfg.run(["ffmpeg", "-v", "error", "-c:v", "libvpx-vp9", "-ss", f"{t:.3f}",
                       "-i", matte, "-frames:v", "1", "-vf", f"alphaextract,scale={sw}:{sh}",
                       "-f", "rawvideo", "-pix_fmt", "gray", "-"], text=False)
        buf = r.stdout
    except Exception:                                      # noqa: BLE001
        return None
    if len(buf) < sw * sh:
        return None
    ox, oy = W / 2.0, AROLL_ORIGIN_Y * H
    x0, y0, x1, y1 = rect
    hit = tot = 0
    for j in range(sh):
        for i in range(sw):
            # this matte pixel's position on screen after the scale
            X = ox + ((i + 0.5) * W / sw - ox) * scale
            Y = oy + ((j + 0.5) * H / sh - oy) * scale
            if x0 <= X < x1 and y0 <= Y < y1:
                tot += 1
                hit += buf[j * sw + i] > 128
    return hit / float(tot) if tot else None


def face_geometry(fmap, aroll, start, end, W, H, scale=1.0, yoff=0.0):
    """The face ON SCREEN during [start, end]: {"chin", "crown", "x0", "x1", "chest",
    "source"} in composition px, or None. From build/framing.json when given, else measured
    on the A-roll inside the window (the outro's skin mask). Either way mapped through the
    A-roll's transform (scale about 50% 30%, plus the panel's y) — a 1.12 punch moves a
    chin at y 1100 down by ~70 px, onto the first line of a stack placed for scale 1."""
    oy, ox = AROLL_ORIGIN_Y * H, W / 2.0

    def Y(y):
        return oy + (y - oy) * scale + yoff

    def X(x):
        return ox + (x - ox) * scale
    if fmap:
        cx, fw = fmap["cx"], fmap["w"]
        chest = [Y(fmap["chest"][0]), Y(fmap["chest"][1])] if fmap.get("chest") else None
        return {"chin": Y(fmap["chin"]), "crown": Y(fmap["head"]),
                "x0": X(cx - FACE_HALF_W * fw), "x1": X(cx + FACE_HALF_W * fw),
                "chest": chest, "source": "framing.json: " + fmap["why"]}
    face = face_box(aroll, start, end, W, H)
    if not face:
        return None
    fx, fy, fw = face
    return {"chin": Y(fy + CHIN * fw), "crown": Y(fy - 0.95 * fw),
            "x0": X(fx - FACE_HALF_W * fw), "x1": X(fx + FACE_HALF_W * fw), "chest": None,
            "source": "face measured in the window (no build/framing.json — run "
                      "scripts/framing_map.py for measured chin/chest)"}


def face_box(aroll, start, end, W, H):
    """(x, y, width) of the face in composition px during [start, end] at scale 1, or None.

    Reuses outro.measure_face (skin mask, median of five frames), which samples the 0.95 s
    BEFORE the time it is given — so it is handed the window's end. The beat's transform
    (panel y, punch scale) is applied by face_geometry()."""
    if not aroll:
        return None
    try:
        import outro
        fx, fy, fw, how = outro.measure_face(aroll, max(end, start + 0.95), W, H)
    except Exception:                                      # noqa: BLE001
        return None
    if not fw or not (0.06 * W <= fw <= 0.6 * W and 0 < fy < H):
        return None                          # a misread (a 4K landscape frame, a hand)
    return fx, fy, fw


def region_luma(aroll, t, rect, W, H):
    """75th-percentile luma (0-1) of rect = (x0, y0, x1, y1) on the A-roll frame at t."""
    try:
        import outro
        sw, sh = 108, 192
        buf = outro._raw_rgb(aroll, sw, sh, at=t, pix="gray",
                             extra_vf=f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                                      f"crop={W}:{H},")
    except Exception:                                      # noqa: BLE001
        return None
    if len(buf) < sw * sh:
        return None
    x0, y0, x1, y1 = [int(v) for v in rect]
    xs = range(max(0, x0 * sw // W), min(sw, x1 * sw // W + 1))
    ys = range(max(0, y0 * sh // H), min(sh, y1 * sh // H + 1))
    vals = sorted(buf[y * sw + x] for y in ys for x in xs)
    if not vals:
        return None
    return vals[int(0.75 * (len(vals) - 1))] / 255.0


def inner_html(it):
    """The stack. With tight leading (rollin / bold, line height ~1.0, the reference look)
    each word's font box (ascent + descent ≈ 1.45 em) overlaps the next line's box although
    the glyphs never touch — verified on rendered frames — so those words carry
    data-layout-allow-overlap for `hyperframes check`. KO's 1.18 needs no marker."""
    hid = it["id"]
    tight = it["S"]["lh"] < 1.15
    mark = " data-layout-allow-overlap" if tight else ""
    out, i = [], 0
    for ln in it["lines"]:
        spans = []
        for w in ln["words"]:
            spans.append(f'<span id="{hid}w{i}" class="kw r-{w["role"]}"{mark}>'
                         f'{word_html(w["text"])}</span>')
            i += 1
        out.append(f'<div class="kl{" sm" if ln["small"] else ""}">{" ".join(spans)}</div>')
    return "".join(out)


CSS = """
      /* Kinetic headlines (scripts/kinetic.py). Right-aligned RTL stack hanging from the
         headline margin (x 920), font-size set per headline by the Chrome fit. Colours are
         tokens only: thin/bold white, keyword --hl-on-dark, partner a lighter tint of it. */
      .kh {{ position: absolute; z-index: 45; direction: rtl; text-align: right;
             white-space: nowrap; font-family: var(--brand-font), "Inter", sans-serif;
             --kh-thin: #ffffff; --kh-bold: #ffffff; --kh-key: var(--hl-on-dark);
             --kh-partner: color-mix(in srgb, var(--hl-on-dark) 42%, #ffffff);
             --kh-grad: linear-gradient(90deg, var(--eblue, #1E8BFF), var(--lav, #C9B8FF) 60%,
                                        var(--pink, #FF9ECF));
             --kh-shadow: 0 3px 18px rgba(0,0,0,.45), 0 1px 3px rgba(0,0,0,.30); }}
      .kh.f-display {{ font-family: var(--display-font), var(--brand-font), "Inter", sans-serif; }}
      .kh.on-paper {{ --kh-thin: var(--brand-ink); --kh-bold: var(--brand-ink);
             --kh-key: var(--hl-on-light);
             --kh-partner: color-mix(in srgb, var(--hl-on-light) 72%, var(--brand-paper));
             --kh-shadow: none; }}
      .kh.s-rollin {{ line-height: {lh_rollin}; }}
      .kh.s-classic {{ line-height: {lh_classic}; }}
      .kh.s-bold {{ line-height: {lh_bold}; }}
      .kh .kl {{ display: block; position: relative; z-index: 1; }}   /* above the scrim */
      .kh .kl.sm {{ font-size: {sm}em; }}
      .kh .kw {{ display: inline-block; opacity: 0; text-shadow: var(--kh-shadow); }}
      .kh .r-thin {{ font-weight: 200; color: var(--kh-thin); }}
      .kh .r-bold {{ font-weight: 800; color: var(--kh-bold); }}
      .kh .r-key {{ font-weight: 800; color: var(--kh-key); }}
      .kh .r-partner {{ font-weight: 500; color: var(--kh-partner); }}
      /* the gradient closer: no text-shadow (it would paint THROUGH the clipped background)
         and no CSS filter (the word reveal tweens `filter` and would erase it) */
      .kh .r-grad {{ font-weight: 800; background: var(--kh-grad); color: transparent;
             -webkit-background-clip: text; background-clip: text; text-shadow: none;
             padding-bottom: .06em; }}
      .kh .ai {{ font-weight: inherit; }}
      /* bright footage behind the stack (measured): a soft dark radial scrim, no edge */
      .kh.scrim::before {{ content: ""; position: absolute; z-index: 0; pointer-events: none;
             inset: -70px -90px -80px -140px;
             background: radial-gradient(closest-side, rgba(0,0,0,.58), rgba(0,0,0,.32) 55%,
                                         rgba(0,0,0,0)); }}
      .kh.scrim {{ --kh-shadow: 0 2px 14px rgba(0,0,0,.55), 0 1px 3px rgba(0,0,0,.45); }}
      /* "behind": true — the stack goes UNDER the speaker's matte: A-roll 20 < words 24 <
         matte 26 (same box and transform-origin as #aroll), the Rollin wall-and-person look */
      .kh.behind {{ z-index: 24; }}
      video.kh-matte {{ z-index: 26; transform-origin: 50% 30%; }}
"""


# ------------------------------------------------------------------ selftest
def selftest():
    """Negative tests of the chest placement gates — `kinetic.py selftest`. No video, no
    Chrome: synthetic framing.json files. Exit 1 on any failure."""
    import tempfile
    fails = []

    def expect(cond, what):
        print(f"  {'✓' if cond else '✗'} {what}")
        if not cond:
            fails.append(what)

    W, H = 1080, 1920
    tmp = tempfile.mkdtemp(prefix="kinetic_selftest_")

    def fj(d):
        path = os.path.join(tmp, "framing.json")
        json.dump(d, open(path, "w"))
        return path

    base = {"head_top": 456, "face_cx": 562, "face_cy": 750, "face_w": 348, "chin": 1002,
            "chest": [1110, 1520],
            "samples": [{"t": 1, "face_cy": 760, "chin": 990}, {"t": 2, "face_cy": 770, "chin": 1010},
                        {"t": 3, "face_cy": 750, "chin": 1724}]}
    fm, notes = load_framing(fj(base), W, H)
    expect(fm and fm["chin"] >= 1100, f"a beard: the measured chin (≈1000) is not trusted alone "
                                      f"— chin used {fm and int(fm['chin'])} (≥ 1100)")
    expect(any("misread" in n for n in notes), "a chin of 1724 (a hand read as the collar) is "
                                               "named and ignored")
    bad = dict(base, chest=[1804, 1520])
    fm2, notes2 = load_framing(fj(bad), W, H)
    expect(fm2 and fm2["chest"] is None and any("chest band" in n for n in notes2),
           "an upside-down chest band [1804, 1520] is rejected, loudly")
    fm3, notes3 = load_framing(fj({"face_cx": 1, "face_cy": 5000, "face_w": 0}), W, H)
    expect(fm3 is None and notes3, "an implausible face is refused, not used")
    fm4, _ = load_framing(os.path.join(tmp, "missing.json"), W, H)
    expect(fm4 is None, "no framing.json → None (the face fallback runs)")
    media = {"moments": [{"id": "p", "type": "punch", "start": 5.0, "end": 9.0,
                          "steps": [[5.0, 1.0], [7.0, 1.14]]}]}
    sc, _ = footage_state(media, None, 6.0, 8.0)
    expect(abs(sc - 1.14) < 1e-6, f"a 1.14 punch inside the window is found (scale {sc})")
    sc0, _ = footage_state(media, None, 1.0, 4.0)
    expect(abs(sc0 - 1.0) < 1e-6, "no punch outside its window")
    g1 = face_geometry(fm, None, 6, 8, W, H, 1.0)
    g2 = face_geometry(fm, None, 6, 8, W, H, 1.14)
    expect(g2["chin"] - g1["chin"] > 60, f"the punch moves the chin down on screen "
                                         f"({int(g1['chin'])} → {int(g2['chin'])})")
    print(f"\n  {'ALL PASS' if not fails else f'{len(fails)} FAILED'}")
    return 1 if fails else 0


# ------------------------------------------------------------------ CLI
def main():
    ap = hfcfg.arg_parser(__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["plan", "parse", "selftest"])
    ap.add_argument("text", nargs="?")
    ap.add_argument("--media", default="media.json")
    ap.add_argument("--words", default="src/words.json")
    ap.add_argument("--style", default="rollin")
    ap.add_argument("--framing", default=None, help="framing.json (default build/framing.json)")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "parse":
        lines, warn = parse_markup(a.text or "", STYLES[a.style]["default"])
        for ln in lines:
            print(("  [small] " if ln["small"] else "  ") +
                  "  ".join(f"{w['text']}:{w['role']}" for w in ln["words"]))
        for w in warn:
            print(f"  ! {w}")
        return 0
    cfg = hfcfg.load(a.config)
    media = json.load(open(a.media, encoding="utf-8"))
    end, bounds = 1e9, []
    if os.path.exists("src/bounds.json"):
        d = json.load(open("src/bounds.json", encoding="utf-8"))
        end, bounds = d["total"], d["bounds"]
    beatmap, _ = hfcfg.load_beats()
    p = plan(cfg, media, end, beatmap, bounds, a.words, a.framing)
    if not p:
        print("  no \"headlines\" in media.json")
        return 0
    for hid, t in p["table"].items():
        print(f"\n  {hid}  {t['start']:.2f} → {t['end']:.2f}s   {t['size']}px, top {t['top']}, "
              f"{t['height']}px tall, widest {t['widest']}px, footage x{t['footage_scale']:g}")
        print(f"    placed by: {t['placed_by']}")
        for (w, spoken_t, spoken), land in zip(t["words"], t["times"]):
            st = f"{spoken_t:6.2f}" if spoken_t is not None else "   -  "
            print(f"    {land:6.2f}  ← spoken {st}  {w}   [{spoken}]")
    for w in p["warnings"]:
        print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
