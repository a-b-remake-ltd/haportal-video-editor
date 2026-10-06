#!/usr/bin/env python3
"""Build the caption cards: ONE line, 3-4 words each, zero gaps.

Rules, in order (references/captions.md):
  1. merge the transcriber's split tokens (a prefix particle + a hyphenated Latin word,
     a brand name split in half) — config → language.merge_next
  2. HARD break after any sentence-ending word: a card must never carry the tail of one
     sentence and the head of the next
  3. soft break after a comma, or before a clause opener, once the card has >= 2 words
  4. word ceiling — config captions.max_words (4 by default; a reference may lower it)
  5. no 1-word orphans (fold back if there is room, otherwise lend the previous card's
     last word — 3+2 beats 4+1)
  6. never split a locked phrase
  (1-6 and 7 are weighed together: each sentence is partitioned optimally — see
   split_cards)
  7. never END a card on a sticky word — a preposition, conjunction, relative word or a
     bare number belongs to what follows. "תודה לך על מה" / "שעשית בשבילי" reads broken;
     "תודה לך" / "על מה שעשית בשבילי" reads right. The trailing sticky word moves to the
     start of the next card. Defaults per language below; extend with
     config language.sticky_words.
  8. an all-caps Latin acronym with an I in it ("AI", "API") gets class "ai": in heavy
     Hebrew faces a capital I has no serif and "AI" reads as "Al". The build sets that
     span in Roboto Slab 800.

Timing:
  * the FIRST card of each sentence starts at the SEGMENT BOUNDARY, not at the
    transcriber's first-word stamp (word starts run 0.03-0.15 s late, so the previous
    card otherwise lingers over the new sentence)
  * mid-sentence cards get a small LEAD, clamped to the boundary
  * duration = next_start - this_start - 0.005
    (the clip window is inclusive at both ends: without the 0.005 both cards render on
    the boundary frame and you get one frame of stacked text. 0.005 can never open a
    gap, because frame times are 0.04 s apart.)

Input   src/words.json   [[start, end, "word"], ...] from the FINAL A-roll transcript
Output  captions.json    [{i, start, dur, text, plain, n}, ...]
"""
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

FRAME = 0.04
MAXW = 4                         # overridden from config captions.max_words in main()
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
           "שקלים", "דולר"},
    "en": {"'s", "too", "itself", "himself", "herself", "themselves", "percent"},
}

# Words that open a new clause: a soft break goes BEFORE them once a card has 2+ words.
OPENERS = {
    "he": {"אבל", "כי", "אז", "כש", "עכשיו", "כן", "אנחנו", "אני", "אתם", "אתן", "הם",
           "בעצם", "ככה", "לכן", "ואז", "ולכן", "אבל", "במילים"},
    "en": {"but", "because", "so", "and", "then", "now", "we", "i", "you", "which"},
}


MAX_CHARS = 0                     # set in main() from the grid width and caption size


def split_cards(words, lang, bounds=()):
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
    Sentence ends stay hard breaks.
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
                c += 1000.0                 # Ben's rule is strict: only when unavoidable
            if last.endswith(","):
                c -= 0.8
            if core(run[j][2]) in openers:
                c -= 0.5
            if core(run[j][2]) in lean:
                c += 6.0
            if locked_pair(last, run[j][2]):
                return None
        c += 1.5 * sum(1 for w in ws[:-1] if w.endswith(","))
        span = sum(1 for bd in bounds for k in range(i + 1, j)
                   if run[k - 1][0] < bd <= run[k][0] + 0.02)
        c += 4.0 * span
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


def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("--words", default="src/words.json")
    ap.add_argument("--bounds", default="src/bounds.json")
    ap.add_argument("--out", default="captions.json")
    a = ap.parse_args()
    cfg = hfcfg.load(a.config)
    lang = cfg["language"]
    lead = cfg["cutting"]["lead"]
    global MAXW, MAX_CHARS
    MAXW = int(cfg.get("captions", {}).get("max_words", MAXW))
    # Width budget: a plate is ~68 px of padding plus ~0.53 x font-size per character in
    # a heavy Hebrew face (measured on Heebo 800). fit_captions.py still measures the
    # real width in Chrome; this just keeps the splitter from producing a card that will
    # not fit the 880 px safe zone in the first place.
    import grid
    safe_w = grid.from_config(cfg)["safe_width"]
    MAX_CHARS = int((safe_w - 68) / (cfg["brand"]["caption_size"] * 0.53))

    words = normalise(load_words(a.words), lang)
    bounds, end = [], None
    if os.path.exists(a.bounds):
        d = json.load(open(a.bounds, encoding="utf-8"))
        bounds, end = d["bounds"], d["total"]

    cards = split_cards(words, lang, bounds[1:])

    # First card of a sentence snaps to its SEGMENT BOUNDARY; mid-sentence cards get a
    # lead, clamped to that boundary. Whisper reports word starts 0.03-0.15 s late, so
    # without the snap the previous card lingers over the new sentence.
    starts = []
    for c in cards:
        w0 = c[0][0]
        b = max([x for x in bounds if x <= w0 + 0.20], default=None)
        # this card is the first of its segment when the PREVIOUS card began before b
        first_of_segment = b is not None and (not starts or starts[-1] < b - 1e-6)
        t = b if first_of_segment else max(w0 - lead, b if b is not None else 0.0)
        starts.append(snap(t))
    for i in range(1, len(starts)):
        if starts[i] <= starts[i - 1]:
            starts[i] = round(starts[i - 1] + FRAME, 3)

    rows = []
    for i, c in enumerate(cards):
        s = starts[i]
        nxt = starts[i + 1] if i + 1 < len(starts) else end
        rows.append({"i": i + 1, "start": round(s, 3),
                     "dur": round(max(nxt - s - 0.005, FRAME), 3),
                     "text": render_text(c, lang["direction"]),
                     "plain": " ".join(w for _, _, w in c), "n": len(c)})
        # the optimiser pays 1000 for a sticky ending, so one that survives had no
        # alternative inside the word ceiling (e.g. "או אפילו תוך כדי" / "לבנות")
        if i + 1 < len(cards) and len(c) > 1 and is_sticky(c[-1][2], sticky_set(lang)) \
                and not is_end(c[-1][2]):
            rows[-1]["sticky_ok"] = True

    json.dump(rows, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ------------------------------------------------------------- assertions
    problems = []
    over = [r for r in rows if r["n"] > MAXW]
    if over:
        problems.append(f"{len(over)} card(s) over {MAXW} words")
    for i in range(len(rows) - 1):
        gap = rows[i + 1]["start"] - (rows[i]["start"] + rows[i]["dur"])
        if gap > 0.0055:
            problems.append(f"GAP of {gap:.3f}s before card {rows[i+1]['i']} — blank frames")
        if gap < -1e-6:
            problems.append(f"OVERLAP before card {rows[i+1]['i']}")
    # a boundary that opens a new SENTENCE must change the caption (a stale card would
    # linger over it); a pause inside a sentence may be spanned by a card
    starts_sentence = {round(c[0][0], 2) for k, c in enumerate(cards)
                       if k == 0 or is_end(cards[k - 1][-1][2])}
    missing = [b for b in bounds
               if not any(abs(r["start"] - b) < 1e-6 for r in rows)
               and any(abs(b - t) < 0.25 for t in starts_sentence)]
    if missing:
        problems.append(f"{len(missing)} segment boundary/ies with no caption change: "
                        f"{[round(m,2) for m in missing][:6]}")
    sticky = sticky_set(lang)
    dangling = [r for r in rows if r["n"] > 1 and is_sticky(r["plain"].split()[-1], sticky)
                and r is not rows[-1] and not r.get("sticky_ok")]
    forced = [r for r in rows if r.get("sticky_ok")]
    if forced:
        print("  ! unavoidable sticky ending (no split inside the word ceiling avoids it): "
              + "; ".join(f"c{r['i']:02d} '{r['plain']}'" for r in forced))
    if dangling:
        problems.append(f"{len(dangling)} card(s) END on a sticky word (re-split by hand): "
                        + "; ".join(f"c{r['i']:02d} '{r['plain']}'" for r in dangling[:5]))

    print(f"{len(rows)} cards, {sum(r['n'] for r in rows)} words, "
          f"max {max(r['n'] for r in rows)} words/card, {rows[0]['start']} → {end}")
    for r in rows:
        print(f"  c{r['i']:02d} {r['start']:6.2f} +{r['dur']:4.2f} [{r['n']}]  {r['plain']}")
    if problems:
        print("\n  ✗ " + "\n  ✗ ".join(problems))
        return 1
    print("\n  ✓ zero gaps, zero overlaps, every sentence boundary has a caption change")
    return 0


if __name__ == "__main__":
    sys.exit(main())
