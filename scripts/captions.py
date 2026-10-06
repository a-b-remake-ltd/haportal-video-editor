#!/usr/bin/env python3
"""Build the caption cards: ONE line, 1-3 words each, hard swaps, hidden where the frame
belongs to something else.

Rules, in order (references/captions.md):
  1. merge the transcriber's split tokens (a prefix particle + a hyphenated Latin word,
     a brand name split in half) — config → language.merge_next
  2. HARD break after any sentence-ending word: a card must never carry the tail of one
     sentence and the head of the next
  3. HARD break at every hidden-window edge (build/caption_hide.json: the hook world, every
     kinetic headline, designed moments that own the frame, the outro): a card is either
     entirely inside a window (never shown: the headline IS the caption) or entirely outside
  4. soft break after a comma, or before a clause opener, once the card has >= 2 words
  5. word ceiling — config captions.max_words (3 by default)
  6. no 1-word orphans (fold back if there is room, otherwise lend the previous card's
     last word)
  7. never split a locked phrase; avoid spanning a long pause inside one card
  (2-7 are weighed together: each sentence is partitioned optimally — see split_cards)
  8. never END a card on a sticky word — a preposition, conjunction, relative word or a
     bare number belongs to what follows. "תודה לך על מה" / "שעשית בשבילי" reads broken;
     "תודה לך" / "על מה שעשית בשבילי" reads right. Defaults per language below; extend with
     config language.sticky_words. A word that leans BACK ("הזאת", "מאוד") never opens one.
  9. an all-caps Latin acronym with an I in it ("AI", "API") gets class "ai": in heavy
     Hebrew faces a capital I has no serif and "AI" reads as "Al". The build sets that
     span in Roboto Slab.

Timing (the hard-swap card, references/captions.md §timing):
  * a card STARTS on its first word's start (config captions.lead, default 0). The first
    card of a cut SEGMENT snaps back to the segment boundary instead: word starts run
    0.03-0.15 s late, so without the snap the previous card lingers over the new sentence
  * it ENDS at the next card's start (minus 0.005: the clip window is inclusive at both
    ends, and without it both cards paint the boundary frame) or at the start of the next
    hidden window, whichever comes first
  * when a pause longer than captions.pause_trim (0.6 s) follows its last word, it is
    trimmed to last-word-end + captions.pause_tail (0.3 s). The screen is then honestly
    empty during the silence — a DELIBERATE gap, which the gap check accepts. A gap with a
    spoken word in it, or a blink inside a short pause, is still a failure.
  * no visible card may START inside a hidden window (asserted here, in caption_layer.py
    and in preflight_qa.py). A card whose words fall inside a window is written with
    "hidden": true so every spoken word stays accounted for.

Hidden windows come from build/caption_hide.json (written by build_index.py) plus the outro
start from build/outro.json. Run captions.py again after build_index.py whenever a headline
or a moment moved: build_index needs the cards' words, the cards need its windows.

Input   src/words.json   [[start, end, "word"], ...] from the FINAL A-roll transcript
Output  captions.json    [{i, start, dur, text, plain, n, words, end_by[, hidden]}, ...]
"""
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

FRAME = 0.04
MAXW = 3                         # overridden from config captions.max_words in main()
SENT_END = (".", "?", "!", "…")

# Words that must not close a card: they lean on the word after them.
STICKY = {
    "he": {"של", "את", "על", "עם", "אל", "כי", "אם", "או", "אבל", "גם", "רק", "כמו", "בין",
           "לפני", "אחרי", "בלי", "עד", "מול", "אצל", "לגבי", "בשביל", "כדי", "מה", "איך",
           "למה", "מי", "כש", "אשר", "כל", "הכי", "יותר", "פחות", "לא", "אין", "יש", "זאת",
           "ש", "ו", "ה", "ב", "ל", "מ", "כ", "שה", "שב", "של", "עוד", "אפילו", "בגלל",
           "לכן", "אז", "כמה", "איזה", "אילו", "בתוך", "מתוך", "תוך", "דרך", "לפי", "ללא",
           # modal verbs lean on the infinitive after them ("כדאי" / "לשתף" reads broken)
           "כדאי", "צריך", "צריכה", "צריכים", "אפשר", "אסור", "חייב", "חייבת", "חייבים",
           "יכול", "יכולה", "יכולים", "הולך", "הולכת", "הולכים", "רוצה", "רוצים"},
    "en": {"a", "an", "the", "of", "to", "in", "on", "at", "by", "for", "from", "with",
           "and", "or", "but", "that", "which", "who", "if", "so", "as", "than", "into",
           "about", "my", "your", "our", "their", "his", "her", "its", "this", "these",
           "those", "not", "no", "very", "more", "most", "is", "are", "was"},
}


def load_words(path):
    """Accepts either [[s,e,w],...] or a whisper json with segments[].words[]."""
    d = json.load(open(path, encoding="utf-8"))
    if isinstance(d, dict) and "segments" in d:
        out = []
        for s in d["segments"]:
            for w in s.get("words", []):
                out.append([float(w.get("start", 0)), float(w.get("end", 0)),
                            str(w.get("word", "")).strip()])
        return out
    return [[float(a), float(b), str(c).strip()] for a, b, c in d]


def normalise(words, lang):
    """Merge split tokens and fix known transcriber typos."""
    typos = lang.get("typos") or {}
    merge_next = lang.get("merge_next") or {}
    out, i = [], 0
    while i < len(words):
        a, b, w = words[i]
        w = w.strip()
        if i + 1 < len(words):
            nxt = words[i + 1][2].strip()
            # "<particle>" + "-Latin"  →  one visual unit
            if nxt.startswith("-") and len(nxt) > 1:
                out.append([a, words[i + 1][1], w + nxt])
                i += 2
                continue
            if merge_next.get(w) == nxt.rstrip(",.?!"):
                out.append([a, words[i + 1][1], (w + nxt).replace(" ", "")])
                i += 2
                continue
        core = w.rstrip(",.?!…")
        tail = w[len(core):]
        out.append([a, b, typos.get(core, core) + tail])
        i += 1
    return out


def is_end(w):
    return w.rstrip().endswith(SENT_END)


# Words that lean BACK on the word before them: a card should not start with one.
# "הקבוצה" / "הזאת אנחנו" and "משהו מאוד" / "מאוד חשוב" both read broken.
LEAN_BACK = {
    "he": {"הזה", "הזאת", "הזו", "האלה", "האלו", "ההוא", "ההיא", "ההם", "ההן", "מאוד",
           "שלי", "שלך", "שלו", "שלה", "שלנו", "שלכם", "שלכן", "שלהם", "שלהן", "עצמו",
           "עצמה", "עצמם", "בלבד", "אלף", "אלפים", "מיליון", "מיליארד", "אחוז", "שקל",
           "שקלים", "דולר", "מלאכותית"},
    "en": {"'s", "too", "itself", "himself", "herself", "themselves", "percent"},
}

# Words that open a new clause: a soft break goes BEFORE them once a card has 2+ words.
OPENERS = {
    "he": {"אבל", "כי", "אז", "כש", "עכשיו", "כן", "אנחנו", "אני", "אתם", "אתן", "הם",
           "בעצם", "ככה", "לכן", "ואז", "ולכן", "אבל", "במילים"},
    "en": {"but", "because", "so", "and", "then", "now", "we", "i", "you", "which"},
}


MAX_CHARS = 0                     # set in main() from the grid width and caption size


def zone_of(t, windows):
    """Index of the hidden window a word starting at t belongs to, else -1. A word starting
    within one frame BEFORE a window counts as inside it: the headline lands it."""
    for k, (a, b) in enumerate(windows):
        if a - FRAME - 1e-6 <= t < b - 1e-6:
            return k
    return -1


def split_cards(words, lang, bounds=(), windows=()):
    """Split into cards by choosing the BEST partition of each sentence, not greedily.

    A greedy splitter plus fix-up passes (push a sticky word forward, split the long
    card, fold the orphan back) chase each other in circles: "כן, גם" / "אם לא דיברתם"
    kept coming back. So each sentence is partitioned by dynamic programming over a
    cost per card:
      * size: 3 words is ideal, 2 and 4 fine, 1 only for a punctuated interjection
        ("כן,"), never more than MAXW
      * ending on a sticky word: heavy penalty (rule 7)
      * ending on a comma: bonus. A comma inside a card: penalty
      * the next card opening with a clause opener: bonus
      * spanning a cut point (a pause in the speech): penalty, but cheaper than a
        sticky ending — "כן," / "גם אם לא דיברתם" across a pause is the right read
      * splitting a locked phrase: forbidden
      * a pause longer than 0.45 s INSIDE a card: penalty (the card would hang through it)
    Sentence ends and hidden-window edges are hard breaks.
    """
    openers = set(OPENERS.get(lang.get("code", ""), set())) | set(lang.get("clause_openers") or [])
    locked = [tuple(p) for p in (lang.get("locked_phrases") or [])]
    sticky = sticky_set(lang)
    lean = set(LEAN_BACK.get(lang.get("code", ""), set()))

    def core(w):
        return w.rstrip(",.?!…").strip("\"'“”״׳()").lower()

    def locked_pair(a, b):
        return any(len(p) == 2 and core(a) == p[0].lower() and core(b) == p[1].lower()
                   for p in locked)

    # sentences = runs between hard breaks
    runs, cur = [], []
    for w in words:
        if cur and zone_of(w[0], windows) != zone_of(cur[-1][0], windows):
            runs.append(cur)               # a hidden-window edge: hard break
            cur = []
        cur.append(w)
        if is_end(w[2]):
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)

    def cost(run, i, j):                    # noqa: C901                    # card = run[i:j]
        n = j - i
        if n > MAXW:
            return None
        ws = [x[2] for x in run[i:j]]
        if MAX_CHARS and n > 1 and len(" ".join(ws)) > MAX_CHARS:
            return None                     # wider than the safe zone: re-split, don't shrink
        last = ws[-1]
        c = {1: 6.0, 2: 1.0, 3: 0.0, 4: 0.3}.get(n, 0.3)
        if n == 1 and re.search(r"[,.?!…]$", last):
            c = 1.5
        if j < len(run):
            if is_sticky(last, sticky):
                c += 1000.0                 # the rule is strict: only when unavoidable
            if last.endswith(","):
                c -= 0.8
            nxt = core(run[j][2])
            if nxt in openers or (nxt[:1] == "ו" and nxt[1:] in openers):
                c -= 0.5
            if core(run[j][2]) in lean:
                c += 6.0
            if locked_pair(last, run[j][2]):
                return None
        c += 1.5 * sum(1 for w in ws[:-1] if w.endswith(","))
        span = sum(1 for bd in bounds for k in range(i + 1, j)
                   if run[k - 1][0] < bd <= run[k][0] + 0.02)
        c += 4.0 * span
        c += 4.0 * sum(1 for k in range(i + 1, j) if run[k][0] - run[k - 1][1] > 0.45)
        return c

    cards = []
    for run in runs:
        n = len(run)
        best = [0.0] + [None] * n
        back = [0] * (n + 1)
        for j in range(1, n + 1):
            for i in range(max(0, j - MAXW), j):
                if best[i] is None:
                    continue
                c = cost(run, i, j)
                if c is None:
                    continue
                if best[j] is None or best[i] + c < best[j]:
                    best[j], back[j] = best[i] + c, i
        j, parts = n, []
        while j > 0:
            i = back[j]
            parts.append(run[i:j])
            j = i
        cards.extend(reversed(parts))
    return cards


def sticky_set(lang):
    base = set(STICKY.get(lang.get("code", ""), set()))
    return base | set(lang.get("sticky_words") or [])


def is_sticky(w, sticky):
    """A word that may not END a card. Punctuation after it means the phrase closed."""
    if re.search(r"[,.?!…:;]$", w.strip()):
        return False
    core = w.strip().strip("\"'“”״׳()").lower()
    # a number, with or without a Hebrew prefix ("ה-20", "ב-3"), leans on its unit
    return core in sticky or bool(re.fullmatch(r"[\u05d0-\u05ea]{0,3}-?[\d.,]+%?", core))


AI_LIKE = re.compile(r"^[A-Z0-9]*I[A-Z0-9]*$")


def render_text(card, direction):
    """Wrap Latin runs in an isolated LTR span. In a caption the verbatim rule wins, so
    you cannot rewrite the Latin away to dodge bidi reordering — isolate it instead."""
    parts = []
    for _, _, w in card:
        if direction == "rtl" and re.search(r"[A-Za-z]", w):
            m = re.match(r"^([^A-Za-z]*)([A-Za-z][A-Za-z'’0-9.\-]*)(.*)$", w)
            if m:
                pre, lat, post = m.groups()
                cls = "ltr ai" if AI_LIKE.match(lat) else "ltr"
                parts.append(f'{html.escape(pre)}'
                             f'<span class="{cls}">{html.escape(lat)}</span>'
                             f'{html.escape(post)}')
                continue
        parts.append(html.escape(w))
    return " ".join(parts)


def snap(t):
    return round(round(t / FRAME) * FRAME, 3)


def snap_up(t):
    import math
    return round(math.ceil(t / FRAME - 1e-6) * FRAME, 3)


def voice_envelope(path, hop=0.01):
    """Voice energy per `hop` seconds (dBFS) in the 100-900 Hz band, where Hebrew vowels
    live and breath does not (references/cutting.md). stdlib only: ffmpeg → s16le → array."""
    import array
    import math
    r = hfcfg.run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-vn", "-ac", "1",
                   "-ar", "16000", "-af", "highpass=f=100,lowpass=f=900", "-f", "s16le", "-"],
                  text=False)
    if r.returncode or not r.stdout:
        return []
    a = array.array("h")
    a.frombytes(r.stdout[:len(r.stdout) // 2 * 2])
    if sys.byteorder == "big":
        a.byteswap()
    n = int(16000 * hop)
    out = []
    for i in range(0, len(a) - n + 1, n):
        ss = sum(x * x for x in a[i:i + n])
        out.append(10 * math.log10(ss / n + 1e-9) - 90.31)
    return out


def refine_words(words, env, hop=0.01, below=24.0, min_shift=0.06, bridge=0.15, join=0.25):
    """Shrink each word to the voice that is really in it.

    Whisper's word stamps are CONTIGUOUS: it folds every pause into a neighbouring word
    (measured on an AI-avatar take: a word stamped 25.72-26.92 s whose voice is 26.56-26.92,
    the 0.8 s before it silence). A pause rule read off the transcript then never fires and
    the card hangs through every silence. So inside each word the voiced runs are found
    (10 ms windows within `below` dB of the take's speech level, the 90th percentile; dips
    shorter than `bridge` are bridged), the LONGEST run is the word, and runs within `join` s
    of it are kept with it; a short blip across a pause is the neighbour's tail. Edges move
    only by more than `min_shift` (the rest is jitter). Returns (words, n_moved)."""
    if not env:
        return words, 0
    srt = sorted(env)
    thr = srt[int(0.9 * (len(srt) - 1))] - below
    gap_w = int(round(bridge / hop))
    out, moved = [], 0
    for s0, e0, w in words:
        i0, i1 = int(round(s0 / hop)), min(len(env), int(round(e0 / hop)))
        runs, cur, quiet = [], None, 0
        for i in range(i0, i1):
            if env[i] >= thr:
                if cur is None:
                    cur = [i, i + 1]
                else:
                    cur[1] = i + 1
                quiet = 0
            elif cur is not None:
                quiet += 1
                if quiet > gap_w:
                    runs.append(cur)
                    cur, quiet = None, 0
        if cur is not None:
            runs.append(cur)
        s1, e1 = s0, e0
        if runs:
            core = max(runs, key=lambda r: r[1] - r[0])
            a_, b_ = core
            for r in runs:                       # neighbours close to the core belong to it
                if r[1] <= a_ and (a_ - r[1]) * hop < join:
                    a_ = r[0]
            for r in runs:
                if r[0] >= b_ and (r[0] - b_) * hop < join:
                    b_ = r[1]
            if a_ * hop - s0 > min_shift:
                s1 = round(a_ * hop, 3)
            if e0 - b_ * hop > min_shift:
                e1 = round(b_ * hop, 3)
        if e1 - s1 < 0.05:
            s1, e1 = s0, e0
        moved += (s1, e1) != (s0, e0)
        out.append([s1, e1, w])
    return out, moved


def load_hide(path="build/caption_hide.json", outro_path="build/outro.json", end=None):
    """The merged hidden windows [[a, b], ...]: build_index.py's caption_hide.json plus the
    outro (from its start to the end of the composition). [] when neither exists."""
    ws = []
    if path and os.path.exists(path):
        d = json.load(open(path, encoding="utf-8"))
        ws += [[float(a), float(b)] for a, b in (d.get("windows") or []) if float(b) > float(a)]
    if outro_path and os.path.exists(outro_path):
        o = json.load(open(outro_path, encoding="utf-8"))
        if o.get("start") is not None:
            ws.append([float(o["start"]), float(o.get("end") or 1e9) + 10.0])
    ws.sort()
    out = []
    for a, b in ws:
        if out and a <= out[-1][1] + FRAME:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def inside(t, windows, tol=0.011):
    """The window that t lies strictly inside (more than `tol` past its start), else None."""
    for a, b in windows:
        if a + tol < t < b - tol:
            return (a, b)
    return None


def gap_verdict(prev_end, nxt_start, all_words, windows, pause_trim=0.6, pause_tail=0.3):
    """Is the blank stretch [prev_end, nxt_start] between two visible cards deliberate?

    Deliberate = every part of it that is NOT under a hidden window is a real pause: no word
    is spoken there, the silence around it is longer than pause_trim, and the card before
    kept its pause_tail after its last word. Anything else is an accidental blank (a caption
    blinking off between words, or a spoken word with nothing on screen). Returns
    (ok: bool, why: str). Shared with preflight_qa.py."""
    if nxt_start - prev_end <= 0.0055:
        return True, ""
    parts = [[prev_end, nxt_start]]
    for a, b in windows:
        nxt = []
        for u0, u1 in parts:
            if b <= u0 or a >= u1:
                nxt.append([u0, u1])
                continue
            if a > u0:
                nxt.append([u0, a])
            if b < u1:
                nxt.append([b, u1])
        parts = nxt
    for u0, u1 in parts:
        if u1 - u0 <= FRAME + 1e-6:
            continue
        # a word counts as spoken in the blank only by more than 0.08 s: the tail of a word
        # the headline just showed, or the breath-onset of the next, is not a missing caption
        spoken = [w for w in all_words if min(w[1], u1) - max(w[0], u0) > 0.08]
        if spoken:
            return False, (f"{u0:.2f}-{u1:.2f}s blank while '{spoken[0][2]}' is spoken "
                           f"({spoken[0][0]:.2f}s)")
        if any(abs(u0 - b) <= FRAME + 1e-6 or abs(u1 - a) <= FRAME + 1e-6 for a, b in windows):
            continue        # silence next to a headline / hidden window: the card waits for its word
        before = max((w[1] for w in all_words if w[1] <= u0 + FRAME), default=None)
        after = min([w[0] for w in all_words if w[0] >= u1 - FRAME], default=None)
        if before is not None and after is not None and after - before <= pause_trim + 1e-6:
            return False, (f"{u0:.2f}-{u1:.2f}s blank inside a {after - before:.2f}s pause "
                           f"(only pauses > {pause_trim}s may go blank)")
        if before is not None and u0 < before + pause_tail - FRAME - 1e-6 and \
                not any(abs(u0 - a) < FRAME for a, _ in windows):
            return False, (f"card cut {u0 - before:.2f}s after its last word (tail rule: "
                           f"{pause_tail}s)")
    return True, "pause" if parts else "window"


def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("--words", default="src/words.json")
    ap.add_argument("--bounds", default="src/bounds.json")
    ap.add_argument("--hide", default="build/caption_hide.json",
                    help="hidden windows from build_index.py (\"\" to ignore)")
    ap.add_argument("--outro", default="build/outro.json")
    ap.add_argument("--aroll", default="assets/aroll.mp4",
                    help="the A-roll the words are timed on: pauses are read from its voice "
                         "energy (\"\" = trust the transcript's word edges)")
    ap.add_argument("--out", default="captions.json")
    a = ap.parse_args()
    cfg = hfcfg.load(a.config)
    lang = cfg["language"]
    cc = cfg.get("captions", {})
    lead = float(cc.get("lead", 0.0))
    pause_trim = float(cc.get("pause_trim", 0.6))
    pause_tail = float(cc.get("pause_tail", 0.3))
    global MAXW, MAX_CHARS
    MAXW = int(cc.get("max_words", MAXW))
    # Width budget: a card is ~68 px of padding plus ~0.53 x font-size per character in
    # a heavy Hebrew face (measured on Heebo 800; lighter weights are narrower, so this is
    # safe). fit_captions.py still measures the real width in Chrome; this just keeps the
    # splitter from producing a card that will not fit the 880 px safe zone at all.
    import grid
    safe_w = grid.from_config(cfg)["safe_width"]
    MAX_CHARS = int((safe_w - 68) / (cfg["brand"]["caption_size"] * 0.53))

    words = normalise(load_words(a.words), lang)
    if a.aroll and os.path.exists(a.aroll):
        words, moved = refine_words(words, voice_envelope(a.aroll))
        print(f"  word edges read from the voice in {a.aroll}: {moved} of {len(words)} pulled "
              f"in to the voiced part (Whisper folds pauses into words)")
    elif a.aroll:
        print(f"  note: {a.aroll} not found — pauses come from the transcript's word edges, "
              f"which hide most of them (pass --aroll)")
    bounds, end = [], None
    if os.path.exists(a.bounds):
        d = json.load(open(a.bounds, encoding="utf-8"))
        bounds, end = d["bounds"], d["total"]
    if end is None:
        end = max(w[1] for w in words) + pause_tail
    windows = load_hide(a.hide, a.outro, end) if a.hide else []
    if a.hide and not os.path.exists(a.hide):
        print(f"  note: no {a.hide} yet — cards run as if nothing hides them. After "
              f"build_index.py writes it, run captions.py again (then caption_layer.py).")

    cards = split_cards(words, lang, bounds[1:], windows)
    hidden = [zone_of(c[0][0], windows) >= 0 for c in cards]

    # ---- starts. Visible: first word (minus lead); the first card of a segment snaps back
    # to the segment boundary; never inside a hidden window; strictly increasing.
    starts = []
    prev_vis = None
    for k, c in enumerate(cards):
        w0 = c[0][0]
        if hidden[k]:
            starts.append(snap(w0))
            continue
        b = max([x for x in bounds if x <= w0 + 0.20], default=None)
        first_of_segment = b is not None and (prev_vis is None or prev_vis < b - 1e-6)
        t = b if first_of_segment else max(w0 - lead, b if b is not None else 0.0)
        t = snap(t)
        win = next(((x, y) for x, y in windows if x - 1e-6 <= t < y - 1e-6), None)
        if win:
            t = snap_up(win[1])              # the snap point is under a window: start at its end
        if prev_vis is not None and t <= prev_vis:
            t = round(prev_vis + FRAME, 3)
        starts.append(t)
        prev_vis = t

    # ---- ends
    rows = []
    vis_idx = [k for k in range(len(cards)) if not hidden[k]]
    for k, c in enumerate(cards):
        s0 = starts[k]
        last_end = max(w[1] for w in c)
        if hidden[k]:
            win = next(((x, y) for x, y in windows if x - FRAME - 1e-6 <= c[0][0] < y), (s0, last_end))
            nxt = starts[k + 1] if k + 1 < len(cards) else end
            en = min(win[1], nxt) if nxt > s0 else win[1]
            end_by = "hidden"
        else:
            later = [starts[j] for j in vis_idx if j > k]
            nxt = later[0] if later else end
            wst = min([x for x, _ in windows if x > s0 + 1e-6], default=None)
            en, end_by = nxt - 0.005, "next" if later else "end"
            if wst is not None and wst < nxt:
                en, end_by = wst, "window"
            if en - last_end > pause_trim:
                en, end_by = min(en, snap_up(last_end + pause_tail)), "pause"
        row = {"i": k + 1, "start": round(s0, 3), "dur": round(max(en - s0, FRAME), 3),
               "text": render_text(c, lang["direction"]),
               "plain": " ".join(w for _, _, w in c), "n": len(c),
               "words": [[round(x, 3), round(y, 3), w] for x, y, w in c], "end_by": end_by}
        if hidden[k]:
            row["hidden"] = True
        # the optimiser pays 1000 for a sticky ending, so one that survives had no
        # alternative inside the word ceiling (e.g. "או אפילו תוך כדי" / "לבנות")
        if k + 1 < len(cards) and len(c) > 1 and is_sticky(c[-1][2], sticky_set(lang)) \
                and not is_end(c[-1][2]):
            row["sticky_ok"] = True
        rows.append(row)

    json.dump(rows, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    problems = check_rows(rows, words, windows, bounds, cards, lang, pause_trim, pause_tail)

    vis = [r for r in rows if not r.get("hidden")]
    nh = len(rows) - len(vis)
    print(f"{len(rows)} cards ({len(vis)} shown, {nh} hidden under {len(windows)} window(s)), "
          f"{sum(r['n'] for r in rows)} words, max {max(r['n'] for r in rows)} words/card, "
          f"{rows[0]['start']} → {end}")
    for r in rows:
        tag = "  hidden" if r.get("hidden") else (f"  ⟂ {r['end_by']}" if r["end_by"] in ("pause", "window") else "")
        print(f"  c{r['i']:02d} {r['start']:6.2f} +{r['dur']:4.2f} [{r['n']}]  {r['plain']}{tag}")
    forced = [r for r in rows if r.get("sticky_ok")]
    if forced:
        print("  ! unavoidable sticky ending (no split inside the word ceiling avoids it): "
              + "; ".join(f"c{r['i']:02d} '{r['plain']}'" for r in forced))
    if problems:
        print("\n  ✗ " + "\n  ✗ ".join(problems))
        return 1
    print("\n  ✓ 1-%d words, no accidental gaps, no overlaps, no card starts inside a hidden "
          "window, every sentence boundary has a caption change" % MAXW)
    return 0


def check_rows(rows, words, windows, bounds, cards, lang, pause_trim, pause_tail):
    """The caption gates. Returns a list of problems (empty = pass)."""
    problems = []
    over = [r for r in rows if r["n"] > MAXW]
    if over:
        problems.append(f"{len(over)} card(s) over {MAXW} words")
    vis = [r for r in rows if not r.get("hidden")]
    for x, y in zip(vis, vis[1:]):
        e = x["start"] + x["dur"]
        if e - y["start"] > 1e-6:
            problems.append(f"OVERLAP before card {y['i']}")
            continue
        ok, why = gap_verdict(e, y["start"], words, windows, pause_trim, pause_tail)
        if not ok:
            problems.append(f"ACCIDENTAL GAP before card {y['i']}: {why}")
    starts_in = [r for r in vis if inside(r["start"], windows)]
    if starts_in:
        problems.append("CARD STARTS INSIDE A HIDDEN WINDOW: " + "; ".join(
            f"c{r['i']:02d} {r['start']:.2f}s '{r['plain']}'" for r in starts_in[:5]))
    # a boundary that opens a new SENTENCE must change the caption (a stale card would
    # linger over it); a pause inside a sentence may be spanned by a card
    starts_sentence = {round(c[0][0], 2) for k, c in enumerate(cards)
                       if k == 0 or is_end(cards[k - 1][-1][2])}
    missing = [b for b in bounds
               if not any(abs(r["start"] - b) < 1e-6 for r in rows)
               and not inside(b, windows, tol=-FRAME)
               and any(abs(b - t) < 0.25 for t in starts_sentence)]
    if missing:
        problems.append(f"{len(missing)} segment boundary/ies with no caption change: "
                        f"{[round(m, 2) for m in missing][:6]}")
    sticky = sticky_set(lang)
    dangling = [r for r in rows if r["n"] > 1 and is_sticky(r["plain"].split()[-1], sticky)
                and r is not rows[-1] and not r.get("sticky_ok")]
    if dangling:
        problems.append(f"{len(dangling)} card(s) END on a sticky word (re-split by hand): "
                        + "; ".join(f"c{r['i']:02d} '{r['plain']}'" for r in dangling[:5]))
    return problems


if __name__ == "__main__":
    sys.exit(main())
