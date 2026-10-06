# Captions and the beat map

---

## The spec

The default is the premium motion-edit card. A user who never sends a note gets this:

- **1-3 words per card, one line, hard swap.** The next card replaces the previous one within
  a frame. No fade, no pop, no scale. (`captions.max_words: 3`.)
- **White, 62 px, weight 500, soft shadow, no box** (`captions.style: "shadow"`,
  `brand.caption_size: 62`, `captions.weight: 500`): `text-shadow: 0 2px 12px rgba(0,0,0,.6)`,
  centred in `left:60px; right:140px` (x 500). It reads on a dark shirt and stays out of the
  way of the headlines and designed moments, which carry the emphasis.
- **On a high-contrast band.** The grid's band is y 1110-1190. `framing_map.py` measures the
  speaker: when that band lands on the chin, the beard or straddles the collar, it moves onto
  the chest (`--apply` writes `captions.center_y` and the reason into config.json). When the
  band is bright (p75 luma > 0.6) white type will not read: switch to `"plate"`.
- **Every word spoken, verbatim, in its INTENDED spelling.** Never drop or paraphrase. Fix
  transcription errors, an AI avatar's mispronunciations and colloquial forms through
  `src/corrections.json` (`scripts/xcheck.py`), never by hand in captions.json.
- **Never end a card on a sticky word** (a preposition, conjunction or bare number), never open
  one on a word that leans back ("הזאת", "מאוד", "מלאכותית"). See `references/hebrew.md`.
- **"AI" in Roboto Slab** (`class="ltr ai"`), at the caption weight + 100: in a sans face the
  capital I reads as a lower-case l.
- **No dashes** in on-screen text (a number range is the only exception; a prefix hyphen as in
  "ב-AI" is spelling, not a dash) and **no emoji**. `preflight_qa.py` fails both.
- **Hidden where the frame belongs to something else:** the hook world, every kinetic headline
  window (the headline IS the caption), designed moments marked `hide_captions`, the outro.

`"plate"` (black type on a translucent white plate, weight 800) stays available for bright or
busy footage, or when a reference uses it:

```css
.cap .p { display:inline-block; color:#0a0a0a; font-size:70px; font-weight:800;
          background:rgba(255,255,255,0.82); border-radius:22px;
          padding:20px 34px 26px; box-shadow:0 10px 34px rgba(0,0,0,0.42); white-space:nowrap; }
```

**A plate must HUG the text.** Wrap the line in an `inline-block` span inside the centred
`.cap` div; never style the full-width div, or you get a band across the frame.

A ~55 s reel lands around **50-60 cards** at 1-3 words.

---

## Generating the cards

**Text source:** the raw transcript you audited, carried onto the cut timeline by
`scripts/map_words.py` (out = segment.start + raw − segment.src_start; each word goes to
the kept segment it overlaps, or the nearest within 0.35 s). Re-transcribe the final A-roll
only to `--verify`: every disagreement is either a transcriber slip on the re-run, or a real
problem (a clipped word, an SFX on a word).

**Splitting:** `captions.py` partitions each sentence optimally (dynamic programming) over a
cost per card. The rules below are the costs; a sticky ending costs 1000, so one only
survives when no split inside the word ceiling avoids it, and the script names it.


Generate them, never hand-write them. `scripts/captions.py` implements the splitter, in order:

1. **Merge the transcriber's split tokens** — cases where one spoken word or one visual unit
   comes back as two (a prefix particle plus a hyphenated Latin word; a brand name split in
   half). Keep the pairs in a per-project `MERGE_NEXT` map.
2. **Hard break after any sentence-ending word** (`.`, `?`, `!`) — a card must never carry the
   tail of one sentence and the head of the next.
3. **Soft break** after a comma, or before a clause opener (keep a small per-language
   `CLAUSE_OPENERS` set), once the card already has ≥2 words.
4. **Word ceiling: `captions.max_words` (3 by default).** Hidden-window edges are hard
   breaks too: a card is either entirely under a headline or entirely outside it.
5. **No 1-word orphans.** Fold back if the previous card has room; otherwise lend it the
   previous card's last word — **3+2 beats 4+1**.
6. **Never split a locked phrase** (a two-token brand name, a compound term).

### Measure every plate's WIDTH at build time

`white-space: nowrap` plus a long card can exceed the frame. Render each caption in the
real brand face with headless Chrome (`getBoundingClientRect`) and fail or downsize anything
wider than the safe zone (**880 px**). See `scripts/fit_captions.py`.

**Do not try to read overflow off a snapshot.** A white sofa or a bright logo trips a pixel
detector and sends you chasing a caption bug that is not there.

---

## The five slots

| beat | top (plate) | note |
|---|---|---|
| hook (matte) | 1092 | plate centred in the Reels caption band y 1110-1190 |
| split (900 px panel) | 780 | at or above the seam, over B-roll. Never on eyes/forehead |
| speaker full-screen | 1092 | caption band, on the chest |
| **full-frame B-roll / graphic** | **930 (centred)** | see below |
| B-roll whose ACTION is dead centre | **520 (lifted)** | see below |

The speaker slots are not typed in by hand. `scripts/beats.py` reads them from
`scripts/grid.py` (band centre minus half the plate height). An analysed reference may move
the band through `captions.center_y`, which `apply_style.py` clamps into the safe zone.
`grid.slot_top()` is the ONE function both the builder and the caption layer call.
Horizontally every plate is centred on **x 500** inside the 880 px safe width, not on the
frame.

**On a full-frame B-roll shot the caption goes in the MIDDLE (~930), never dropped to the
lower third.** A caption that dives to the bottom for one beat and jumps back reads as a bug.
The white plate is legible straight over a graphic, so centring costs nothing. The only
exception is a card you built yourself around a low caption — if in doubt, centre it.

**When the thing the shot exists to prove happens in the middle of the frame, lift the slot to
~520.** The plate makes text-on-text survivable; it does not make a caption sitting on the
click survivable. Pull ruler-overlaid frames from the **encoded** file across the whole
stretch — a handheld shot drifts, so pick the slot that clears the action at **both** ends, not
just at the in-point.

**The plate makes text-on-text survivable.** A caption over a screenshot's own text no longer
reads as sloppy the way stroked white type did — so on a text-dense B-roll shot, prefer keeping
the slot honest over re-cropping the source.

**Keep the slot table in ONE module** (`scripts/beats.py`), imported by both the composition
builder and the caption-layer builder. Two hand-maintained copies **will** drift the moment a
cut point moves, and the drift is invisible until the encode.

---

## ONE beat map — derive the slot from it

Never keep a hand-written `SLOTS = [(start, end, class), …]` table alongside the visual beat
map. They drift, and the symptom lands on the speaker's face: a slot range saying "panel"
(top 780) across a stretch where the beat map actually had them full-screen puts the plate on
their mouth. Nothing is wrong with the slot *values*; the two tables simply disagree.

```python
# scripts/beats.py — the single source of truth
SLOT  = {"hook": 1092, "std": 1092, "ctr": 930, "hi": 520, "panel": 780}   # speaker slots from grid.py
BEATS = [(start, kind, tag), ...]      # kind ∈ SLOT
```

From that one structure, derive:

- `slot_at(t)` → the caption class. No parallel table to forget.
- Gradient panels = merged contiguous `panel` beats.
- The A-roll `y 0` / `y 770` states, emitted per beat.
- **Assert every beat start is a real segment boundary** before building anything.

---

## Timing

`scripts/captions.py` writes every time; never type one.

1. **A card starts on its first word's start** (`captions.lead`, default 0). The first card
   of a cut SEGMENT snaps back to the segment boundary instead: word starts run 0.03-0.15 s
   late, so without the snap the previous card lingers over the new sentence.
2. **It ends at the next card's start** (minus 0.005 s: the clip window is inclusive at both
   ends, so without it both cards paint the boundary frame) **or at the start of the next
   hidden window**, whichever comes first.
3. **Pause trim:** when a pause longer than `captions.pause_trim` (0.6 s) follows its last
   word, the card ends at last-word-end + `captions.pause_tail` (0.3 s). The screen is then
   empty during the silence: a DELIBERATE gap.
4. **No visible card starts inside a hidden window.** Asserted in captions.py, in
   caption_layer.py and in preflight_qa.py. Cards whose words fall inside a window are kept
   in captions.json with `"hidden": true`, so every spoken word stays accounted for.

**Pauses are read from the audio, not the transcript.** Whisper's word stamps are contiguous:
it folds every pause into a neighbouring word (measured on an AI-avatar take: a word stamped
25.72-26.92 s whose voice is 26.56-26.92). captions.py reads the A-roll's voice energy
(100-900 Hz, 10 ms windows) and shrinks each word to its longest voiced run, so the pause
rule fires and the next card arrives on the real onset. `--aroll ""` turns it off.

**Order of operations.** build_index.py needs the cards' words (headline timing) and writes
`build/caption_hide.json`; captions.py needs those windows. So: captions.py → build_index.py
→ captions.py again → caption_layer.py. If you forget the second run, caption_layer.py still
does the right thing (it trims a card to the words spoken before a window and RE-STARTS it
after the window with only the words not yet spoken, never replaying a half card) and says
captions.json is stale; preflight_qa.py fails until you re-run captions.py.

### Gaps: deliberate vs accidental

A blank stretch between two visible cards passes only when every part of it that is not under
a hidden window is a real pause: no word spoken there for more than 0.08 s, the silence longer
than `pause_trim`, and the card before kept its `pause_tail`. Silence right next to a hidden
window also passes (the headline cut, the next card waits for its word). Anything else fails:
a caption blinking off between words, or a spoken word with nothing on screen.
`captions.gap_verdict()` is the one implementation; captions.py and preflight_qa.py share it.

Then **snap every layout beat to a caption start**, so a visual cut and its caption always
change on the same frame. Never the reverse.

---

## RTL and bidi

If the caption language is right-to-left, set `direction: rtl` on `.cap` and read this section
carefully. If it is left-to-right, only the last bullet applies.

**RTL bidi reorders any line that mixes the script with Latin or symbols.** A headline reading
"build a leading AI model" comes out as "leading AI build a model"; "hundreds of billions $"
becomes "$ hundreds of billions". In **graphics** you can rewrite around it:

1. Write **words, not symbols** — "20 dollars a month", never "$20 a month".
2. Give every Latin token **its own block element** — `OpenAI`, `GPT-5`, `$0.20` each on their
   own `<div>`. Isolated, they render correctly.
3. Drop Latin acronyms from headlines where a native word exists.

**In a CAPTION the verbatim rule wins, so you cannot rewrite the Latin away.** Wrap it instead:

```html
<span style="unicode-bidi: isolate; direction: ltr">DM's</span>
```

That renders in the right order without touching a word that was said.

**Always read the rendered snapshot.** The HTML source looks fine either way — bidi is a
render-time reordering, so source inspection tells you nothing.

**RTL stacking:** a number and its unit go in **separate block divs** ("1.5" / "billion
dollars"), never one mixed line. Same for a card title containing a Latin product name.

---

## Where captions live in the build

Captions are composited **outside** the renderer (`scripts/caption_layer.py` →
`assets/captions.webm`, VP9 with alpha) rather than as clips in the composition — see
`references/hyperframes.md` §caption ghosting for why, and for the check that tells you whether
you actually need to pay that cost on a given project.

Bonus: re-timing a caption then costs an overlay pass instead of a full re-render.
