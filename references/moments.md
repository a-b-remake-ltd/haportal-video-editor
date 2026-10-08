# Designed moments

The best talking-head reels are not "talking head plus captions". Each has two to four
**designed moments**: the page turns to paper on the big idea, a viewer's question types
itself onto a comment card, the benefits tick off a checklist as they are named, a stamp slams
on the claim, the search bar types what he is saying, the frame flies away into a brand world
on the hook. They were hand-coded per project (a premium finance series, the Rollin-style
interview, a platform intro). `scripts/moments.py` rebuilds the same choreography from a
`media.json` entry, in the client's brand colours, inside the Reels grid, synced to the words.

---

## 1. When to use which: tie it to what is SAID

A moment exists because of a sentence. Read the transcript, find the line, then pick the
device that makes that line visible. Never the other way round.

| What is being said | Moment | Why it works |
|---|---|---|
| A question (his own rhetorical one, or "people ask me…") | `question` | The comment card makes it a real viewer's question; the words appear as he says them |
| A list of benefits, steps, things included | `checklist` | Each tick lands on the word, the list builds while the face stays on screen |
| A strong claim, a verdict, a price ("₪0", "closed", "free") | `stamp` | One slam = one idea, the most screenshot-able frame of the reel |
| Two to four names or options ("Facebook, Instagram and WhatsApp") | `chips` | Each pill pops on the word that names it |
| "I searched for…", "people google…", "how do I…" | `searchbar` | The query types itself in sync with the speech; the typing IS the caption |
| A section change, the big idea, the thesis, a definition | `paper` | The page turn says "new chapter"; the statement sits alone on the brand's paper |
| The hook, the first big promise | `fly` | The frame leaves into a brand world: the reel announces it is produced |
| Emphasis inside a sentence, the punchline | `punch` | A hard punch-in step is the camera leaning in |

### How many

**Two to four ready-made moments per reel, each with a reason.** They are a SUBSET of the
reel's 8-12 designed moments (SKILL.md, `references/house-spec.md` §3.1): the rest are the
per-video scenes you build on the kit (`references/kit.md`). A 60 s reel might take one `fly`
or `paper` (the structural one), one or two content moments (`question`, `checklist`,
`searchbar`, `stamp`, `chips`) from here, `punch` steps as needed, and 6-9 kit scenes. Never filler: if you cannot name the sentence a moment
serves, delete it. Never two structural moments back to back. The speaker stays on screen
nearly always (`references/layout.md`): `paper`, `fly` and a gradient `searchbar` take him off
screen, so together they should stay under ~25 % of the runtime.

### Adapt, never copy

The reference objects are examples, not a template. KO had a Swiss flag and a bank statement
because KO talks about Swiss banking; Rollin had creator portraits because it talks about
personal brands. **Derive every object and every word on a card from THIS script**, and add a
light comic wink where it fits (a stamp that reads "חינם" on "it's free"; a search
query typed with the speaker's exact clumsy phrasing; a checklist whose last row is the
punchline). Never cringe, never a joke that contradicts the line.

---

## 2. The contract (media.json)

```json
"moments": [
  {"id": "m1", "type": "fly", "start": 2.40, "end": 6.70, ...type fields}
]
```

Every entry: `id` (unique), `type`, `start`, `end` in **A-roll seconds** (the cut, not the raw).
`python3 $S/scripts/moments.py list` prints every field. Start and end on real moments in the
speech: a structural moment (`paper`, `fly`) starts on a **sentence boundary** like any cut.

### paper — the paper world + the page turn

```json
{"id": "m1", "type": "paper", "start": 25.84, "end": 33.90,
 "kicker": "המדריך המלא",
 "lines": "שלושה *צעדים* / *לפני* שמתחילים",
 "watermark_crop": [165, 15, 348, 230]}
```

- The brand hairline sweeps across in reading direction (right to left in Hebrew, left to
  right in LTR languages), the footage
  wipes off **exactly on the line**, with a slight push; the paper page (bone `--brand-paper`,
  hairline frame, the logo watermark slowly turning) is underneath. At `end − turn` the same
  line wipes the footage back on. `turn` defaults to 0.5 s.
- `lines`: `/` breaks a line, `*keyword*` is the brand colour (`--hl-on-light` on paper),
  `_word_` is bold ink, plain words are thin. Auto-sized to the safe width (max 150 px alone,
  100 px with a sheet or question).
- Optional `sheet: {"title", "rows": [...]}` (a white sheet with check rows, up to 6; it rises
  0.4 s before its first row is said, never empty) and `question: {"who", "initials", "text"}`
  (the comment card, on the page).
- `watermark_crop` cuts the **mark** out of a wordmark (logo px of `brand/logo_trim.png`);
  `watermark: false` drops it.
- Hides captions for the whole window. Transition: `page-turn`.

### question — the viewer's comment card

```json
{"id": "m2", "type": "question", "start": 38.30, "end": 42.50,
 "who": "שאלה מהתגובות", "initials": "?",
 "text": "כמה זמן לוקח לראות תוצאות?"}
```

Rises from the bottom like a comment (bottom edge at y 1480, inside the grid), avatar circle
in `--brand-primary`, a brand hairline on top. Each word appears as it is spoken. Hides
captions (the card carries the words). Up to ~3 lines; the font steps down for long text.

### checklist — the bottom card

```json
{"id": "m3", "type": "checklist", "start": 45.60, "end": 49.90,
 "title": "מה תדעו לעשות", "look": "paper",
 "rows": [{"text": "לבנות מערכות", "cue": "לבנות"}, "כלים", "לערוך וידאו"]}
```

Anchored to the grid's bottom line (y 1520) and growing up, at most 300 px, so it ends under
the caption band and never covers the face. 1-4 rows (a longer list is a paper `sheet`). A row
string is cued on its first word; `cue` may be a word or seconds. Captions stay on.
`look: "dark"` uses the brand card gradient.

### stamp — the slam

```json
{"id": "m4", "type": "stamp", "start": 9.60, "end": 10.75,
 "text": "חינם", "color": "brand", "cue": "חינם", "at": [540, 400]}
```

Lands as its cue word BEGINS (0.06 s before the onset, so the slam sound sits in the gap before
the word, never on it; `"land": "end"` lands after the word instead), from as big as the safe
zone allows, with a rotation (`rotate`, default −6), a small decaying shake and a slow push.
Give it ≥ 0.6 s of hold after landing (the build warns). `color`: `brand` (`--hl-on-light` on a paper plate) or `warn` (`--brand-warn`, a signal
red when the brand defines none). `on: "paper"` drops the plate (ink only). Default position
x 540 (the frame centre), y 400: above most heads. **Move it (`at`) to where this frame is free**; never on the
face.

### chips — pills on cue

```json
{"id": "m5", "type": "chips", "start": 24.00, "end": 25.80, "at": "top",
 "items": [{"text": "אינסטגרם", "cue": "באינסטגרם,"}, "טיקטוק",
           {"text": "וואטסאפ", "cue": "ובוואטסאפ", "accent": true}]}
```

2-4 pills in a centred row in reading order (the first item on the right in RTL, on the left
in LTR), each popping on the word that
names it. A Hebrew prefix is matched ("פייסבוק" finds "בפייסבוק"). One `accent` chip at most
(one accent per frame). `at`: `top` (y 250), `low` (y 1250) or a y.

### searchbar — typing in sync

```json
{"id": "m6", "type": "searchbar", "start": 35.30, "end": 38.30,
 "query": "איך מגדילים מכירות באינסטגרם", "world": "gradient"}
```

A dark brand pill with a magnifier; the query types character by character, each word over
the time it is spoken, a caret blinking after. `world: "gradient"` opens on a brand gradient
world that dissolves back to the footage while the pill rises to `y_footage` and shrinks
(Rollin); `footage` types straight over the speaker. Hides captions. Keep the query under
~30 characters; a long one shrinks the type, then wraps.

### fly — the hook move

```json
{"id": "m7", "type": "fly", "start": 2.40, "end": 6.70,
 "lines": "עברנו את / *1,000* לקוחות", "cards": ["אינסטגרם", "טיקטוק", "יוטיוב"]}
```

The footage frame shrinks into a rounded card (0.34 s), flies up out of frame with a motion
blur (0.30 s) into a brand-gradient world, the statement builds there word by word, up to six
cards rack-focus in around it (text or `{"img": "assets/…png"}`); at the end the card drops
back in from the top and grows back to the full frame, landing exactly on the beat map's
state. Needs ≥ 1.6 s; reads best from a full-screen beat. Hides captions. Transition:
`frame-fly`.

### punch — punch-in steps

```json
{"id": "m8", "type": "punch", "start": 7.29, "end": 10.79,
 "steps": [[7.29, 1.12], [9.04, 1.2]]}
```

Hard sets of the A-roll's scale (the factor multiplies the beat map's scale), released at
`end`. Put steps on phrase starts, alternate in/out like a second camera angle (KO: 1.1-1.14).
No transition, no captions change. Full-screen beats only (a panel beat would push the face
into the seam).

---

## 3. Word sync

Every reveal reads `src/words.json` (the caption words, already on the cut). A card's words
are searched **in order** inside the moment's window; a token may match two spoken tokens
("מאפס" = "מ" + "-אפס") or the spoken word with a prefix letter. So:

- **Write card text in the order it is spoken.** An out-of-order word is interpolated, not
  found ("פרק כל יום" against the spoken "כל יום פרק" reveals "כל יום" late).
- Words that are not spoken (a label, a paraphrase) are interpolated between the spoken
  neighbours; the build prints a note when nothing in a card matched.
- A page or world never sits empty: when the first word is spoken more than 0.6 s after it
  arrives, the first line lands on the arrival and the rest stays synced.
- `cue` fields take a spoken word (searched in the window) or seconds.

## 4. Grid, brand, fonts

- Everything readable inside x 60-940, y 220-1520. Everything centred sits on the FRAME
  centre, x 540, in the 800 px lane x 140-940 (`grid.centered_box`, `references/grid.md`):
  chips, the stamp, the checklist and question cards, the search pill, the paper column and
  the fly lines; the fly cards hang symmetrically from the lane's edges. Decoration that is meant
  to bleed (the paper frame, the watermark, the gradient world, the hairline) is marked
  `data-grid="bleed"`. `grid.py check index.html` must pass with the moments in.
- Colours only from the brand tokens: paper/sheets/cards on light use `--brand-paper`,
  `--brand-ink`, `--hl-on-light`; dark surfaces use `--brand-grad-a/b` and `--hl-on-dark`.
  Two derived tokens: `--m-rule` (= `--hl-on-light`, the hairline/tick on light) and `--m-warn`
  (= `--brand-warn`, or a signal red). Tints are `color-mix()` of tokens, never a new hex.
- Fonts: `var(--brand-font)` with `"Inter"` fallback; Latin runs are isolated per token and
  any acronym with an I is set in Roboto Slab (the "AI" → "Al" rule).
- Classes are prefixed `m-` and ids are `<moment id>-…`, so any number of moments coexist.

## 5. How the footage moves (and why it never jumps)

HyperFrames freezes a `<video>` nested in a timed element, so `paper`, `fly` and `punch`
animate `#aroll` **itself**, like the outro does. Each reads the A-roll's state (the beat map's
scale and y, times any punch) at its start and end:

- **page turn**: the clip-path inset is solved in the element's local coordinates so the
  footage edge rides exactly on the hairline (same ease for line, clip and push).
- **fly**: the card returns to the state at `end` (including a beat that starts there).
- every tween is a `fromTo` whose FROM equals what was on screen before it, with
  `immediateRender: false`, and a hard `tl.set` at every out-point (clip-path back to
  `inset(0px 0px 0px 0px)`, filter `none`, opacity 1).
- Two footage moments may never overlap, and none may start inside a **matted hook** (the
  matte sits on top of the A-roll and would not move with it): the build refuses both.

## 6. Captions

`paper`, `fly`, `question` and `searchbar` hide the captions for their window (the moment
carries the words, or there is no speaker to caption). `checklist`, `stamp`, `chips` and
`punch` keep them. `collect()` returns the merged `hide_captions` windows; build_index writes
them to `build/moments.json` for the caption layer.

A caption card that straddles a window edge reappears after the window with words the moment
already showed (seen on the bench: "בבינה מלאכותית. כן," after the search bar typed
"בבינה מלאכותית"). End a hiding moment where a caption card ends, or let the caption layer
drop the part of a card inside the window and start the next card at the window's end.

## 7. Sound

Every cue is levelled against THIS speaker (voice reference = 90th percentile of the A-roll's
momentary loudness, the outro's method) and kept off the words. Word timings cannot find the
pauses (Whisper smears a word's end into the next word's start), so the cue moves to the
nearest **measured** silence within −0.25/+0.35 s: a pause between words, or 0.08 s before a
segment boundary of the cut (`src/bounds.json`, speech lands 0.04 s after each). With no
pause in reach it stays put and is ducked to 55 %, and the build lists it. **So start every
moment that has a cue on a sentence boundary**; a cue in the middle of a sentence gets ducked.

| Moment | Cue | Under the voice |
|---|---|---|
| paper | `outro_page` on each turn | 8 dB |
| fly | `outro_rush` in, `whoosh_high` on the flight, `whoosh_low` on the return | 8 / 7 / 7 dB |
| searchbar | `whoosh_low` as the pill arrives | 8 dB |
| checklist | `whoosh_low` in, a soft `click` per row | 8 / 11 dB |
| question | `pop` as the card rises | 10 dB |
| stamp | `outro_slam` on the landing | 6 dB |
| chips | `pop` per chip, ON its word (the brand-pop exception), quieter | 10 dB +2 |
| punch | none | |

Swell the bed ≈ 14 dB under the voice through a designed moment (`references/sound.md`). Only
5-7 cues per video: with more than two content moments, drop the per-row clicks first.

## 8. Testing a moment alone

```bash
python3 $S/scripts/moments.py preview fly --entry '{"type":"fly","start":2.4,"end":6.7,...}' \
        --render --sheet 0.12
python3 $S/scripts/grid.py check build/moments_preview/fly/index.html
```

The bench cuts the real A-roll around the moment, shifts the words and the entry, adds stand-in
captions (so you can see them disappear), renders a draft and a contact sheet. **Look at the
frames every 0.1-0.2 s through each move**: the hairline on the footage edge, the card landing
back with no jump, words appearing as they are said, RTL order, brand colours, nothing under
the Reels UI.
