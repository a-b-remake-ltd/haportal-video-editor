# The animated logo outro ("סגיר")

The signature close: when the speech ends, **the video frame itself becomes part of the
logo**. `scripts/outro.py` rebuilds that idea from any logo, using what
`scripts/brand_from_logo.py` measured (`brand/brand.json`):

- **`gate`** — the default when the logo has a **mark with an opening or a hole** (an arch,
  a gate, a ring, the counter of an "O"): the footage closes into a door shaped like that
  opening, flies into it, the mark draws itself around it, the speaker steps through into a
  brand light, and the words slide out from behind the mark.
- **`portal`** — the default otherwise: the speaker shrinks into a circle around the face
  that flies into the logo (into its hole when it has one).
- `line` and `impact` for brands where neither fits.

**It is opt-in.** It runs only when there is a logo **and** the user said yes. A reel
without a logo, or a user who did not ask for it, gets no outro, and a 16:9 piece never
gets one. Never switch it on by default.

---

## 1. The ask

In the opening conversation, only if a logo was supplied (or `brand/brand.json` exists):

> יש לך לוגו? רוצה סגיר מונפש בסוף הסרטון?

If yes, offer the looks in one message, each in one line. When `brand.json` has
`logo.mark` with an `arch` or `hole` opening, offer `gate` first and recommend it;
otherwise recommend `portal` (and leave `gate` out when there is no mark at all):

> **שער** (מומלץ): הפריים שלך נסגר לדלת בצורת הסמל של הלוגו, עף לתוכו, ואותיות הלוגו יוצאות מאחוריו.
> **פורטל**: הפריים שלך נסגר לעיגול סביב הפנים, עף לתוך הלוגו, והלוגו נבנה סביבו.
> **קו**: קו דק בצבע המותג מעביר דף מימין לשמאל, הלוגו נחשף והסלוגן מוקלד אות אחר אות.
> **אימפקט**: חיתוך חד לצבע המותג, הלוגו נוחת בעוצמה עם הבזק קטן. לסרטונים אנרגטיים.

Then ask for the line(s) under the logo (`gate` shows the tagline only, word by word):

> מה לכתוב מתחת ללוגו? משפט קצר (עד 40 תווים) ושם המשתמש שלך, למשל @name

Both are optional. Do not invent a tagline; an empty one simply leaves it out. Use the
user's exact wording and spelling.

No answer, or "no" → no outro. Write nothing to the config.

---

## 2. Switching it on — the contract

`config.json` (the project default):

```json
"outro": {"enabled": true, "style": "auto", "tagline": "…", "handle": "@name"}
```

`media.json` may override per reel (it wins over the config): `"outro": true`,
`"outro": false`, or an object with any of:

| key | meaning | default |
|---|---|---|
| `style` | `gate`, `portal`, `line`, `impact`, or `auto` | `auto`: `gate` when the logo has a mark with an opening/hole, else `portal` |
| `tagline` | one line under the logo, in the reel's language (RTL aware) | none |
| `handle` | e.g. `@name`, always rendered LTR-isolated | none |
| `start` | composition second the outro begins | A-roll end − 0.2 s |
| `face` | `[x, y]` of the face in composition px, if the measurement is wrong | `build/framing.json`, else measured |
| `background` | `paper` or `dark` (portal/line; impact is always brand primary) | chosen by contrast |
| `center_y` | vertical centre of the lockup (gate: of the logo; the tagline hangs below) | 860 |
| `no_hole` | `true` forces the disc landing even when the logo has a hole | false |

**The mark** (for `gate`). `brand_from_logo.py` splits the logo into connected components on
its alpha and finds the mark — the component unlike the letters: a colour most components do
not share, an **opening** (empty space inside its box reachable from ONE side only — an arch
open at the bottom), an enclosed **hole**, or size; a usable opening/hole wins a tie; the
best round hole (the old `holes` logic) is the fallback. It writes `logo.mark`
(trimmed-logo px) and the split images:

```json
"mark": {"x": 167, "y": 23, "w": 174, "h": 206, "colour": "#368BD9", "how": "colour",
         "opening": {"shape": "arch", "cx": 254.0, "top": 53, "w": 118, "h": 176, "side": "bottom"},
         "parts": {"left": {"x": 0, "y": 63, "w": 150, "h": 223},
                   "right": {"x": 374, "y": 0, "w": 414, "h": 228}, "below": null, "above": null},
         "files": {"mark": "brand/mark.png", "left": "brand/word_left.png", "right": "brand/word_right.png"},
         "fill": true, "content": [0, 0, 788, 286]}
```

`shape` is `arch` (the door flies into the doorway), `hole` (into the counter) or `none` (a
solid symbol: the door lands under it). Every part PNG holds only its own pixels with its
anti-aliased edge; a counter painted white is knocked out. `mark: null` = a plain wordmark,
no gate. Tested shapes: an inline arch ("p∩rtal"), a stacked arch over Hebrew words, a solid
app-icon beside a wordmark, a ring at the end of a word, a two-letter monogram whose O has a
white-filled counter, a badge + ring pair.

It needs `brand/brand.json` (path: `brand.json` in config, else next to `brand.css`) with a
logo file. Without it the build stops and says so; it never draws a fake logo.

`build_index.py` then:
- extends the **composition** END by the outro tail. The A-roll clip still ends at its
  real duration (validate.py asserts that), and only outro clips plus the bed that carries
  into the outro may run past the A-roll; the END guard still holds for everything else;
- emits the outro elements, CSS, timeline and its own SFX through the normal `clip()` path,
  so every one of them is in `build/expected.json`;
- writes `build/outro.json` (`style, start, end, aroll_end, freeze_start, info`).
  `validate.py` reads it for the composition END, `finish.py` reads it to switch the
  caption layer off at the outro start.

Preview any style on its own before the full build:

```bash
python3 scripts/outro.py preview --style gate --render       # → build/outro_preview/
python3 scripts/outro.py preview --render                    # auto: gate or portal
python3 scripts/outro.py plan                                # what the build will emit
python3 scripts/outro.py face --aroll assets/aroll.mp4       # the measured face centre
```

---

## 3. The styles

All times are seconds after the outro start `O`. Everything sits inside the Reels grid:
the lockup is centred on **x 500** (not 540) around **y ≈ 860**, inside the safe zone
x 60-940 / y 220-1520 and inside the 3:4 profile crop. Text uses `var(--brand-font)` and
the configured language direction.

### `gate` — the frame becomes the logo (4.5 s)

The idea, for any brand: **the video frame itself becomes part of the logo.** For a
wordmark whose "o" is an arch (`p∩rtal`) the speaker's frame becomes the arch's doorway;
for a ring it becomes the ring's centre; for a solid badge it lands under the badge.

| t | what happens |
|---|---|
| 0.00 | **camera reset**: `#aroll, #ofreeze` (and `#cam` if a build has one) set to scale 1, x/y 0, rotation 0, `transform-origin` on the door centre — no punch-in, sway or panel offset leaks in. `#og-bg` (the last frame, `blur(46px) saturate(1.15)`, scale 1 → 1.3 over the whole outro, radial dim .35 → 1 from 0.7 over 1 s) fades in under the footage in 0.2 s. The freeze frame holds the picture from one frame before the A-roll ends |
| 0.02 – 0.70 | the footage `clip-path` closes from the full frame into a **door around head and shoulders**, shaped like the mark's opening: `inset(T R B L round R R 0 0)` for an arch (R = half the door width), a circle for a hole, a rounded box for a solid mark. power3.inOut |
| 0.25 | 7 soft light orbs fade and scale in around the door (stagger 0.03) |
| 0.76 – 1.42 | the door flies into the opening (power3.inOut) with blur 0 → 14 → 0; from 0.82 the orbs are sucked into the opening's centre (stagger 0.035, power3.in) and vanish |
| 1.14 – 1.48 | the mark draws itself: its own pixels (`mark.png`) revealed by a stroke mask drawn along its traced centreline — two halves rising from the base to the top (an arch's legs and arc; a ring's two sides). A solid mark wipes in bottom → top. From 1.36 the whole mark fades fully in |
| 1.40 | a radial flash on lock; 1.42 the mark gets a drop-shadow glow (26 px, settling to 12 px) |
| 1.50 – 2.10 | the words slide out **from behind the mark**: left part from x +(its width), right part from x −(its width), a part below drops out of the mark's base, each blur 10 → 0, 0.6 s expo.out, inside overflow-hidden wrappers that end at the mark's edge |
| 1.58 – 2.08 | the speaker fades out inside the opening while a brand-light gradient fills it — he steps through |
| 2.12 + 0.2·i | the tagline lands word by word (`word()`: faint grey blur → colour, 0.22 s); first word weight 700 white, the rest weight 300 in a light tint of `--hl-on-dark` |
| 1.40 → end | the whole lockup pushes 1 → 1.045, pivoting on the opening |
| end − 0.4 | fade to black |

**Geometry** (all derived per video, never hand-placed):

- The lockup: `logo.mark.content` (the union of mark and words, no empty margin) at equal
  area (≤ 660 × 420; a stacked lockup may use 580 px of height), grown until the opening is
  ≥ 84 px wide on screen, centred on the safe centre x 500, logo centre at `center_y`.
- The landing rect: the opening on screen with a 4-5 % overscan on the sides and top, so
  the door's edge slides under the mark's ink (arch: flush with the base).
- The door: centred on the face (`outro.face` → `build/framing.json` → skin-mask
  measurement), width 1.95 × face width clamped to 440-600 px, height = width ÷ the landing
  rect's aspect — so it lands exactly.
- The flight: with `transform-origin` o (the door centre), scale `s = landingWidth /
  doorWidth`, and the door's top-centre p pinned to the landing's top-centre q:
  `t = q − (o + (p − o)·s)`.
- **Grid shift**: if the lockup pokes into the right rail (or past the left margin), the
  lockup moves as one piece and the landing, the orbs' sink target and the words are all
  derived after the shift (reported as `info.grid_shift`).
- Words and mark on the dark background: a part keeps its colours when ≥ 85 % of it reads
  on dark (≥ 60 % for the mark), else it becomes a white silhouette (a navy wordmark does).

**Per-brand adaptation rule.** Keep the idea — the frame becomes part of the logo — and let
the mark decide the shape: an arch/gate opening → an arched door that flies into the
doorway; a ring or counter → a circular door into the hole; a solid symbol → a rounded door
that lands under it and the symbol draws over it. Words beside the mark slide out sideways
from behind it; words under a stacked mark drop out of its base. If `brand_from_logo.py`
found no mark (every component looks like a letter — a plain wordmark), use `portal`.
Override a wrong pick by editing `logo.mark` in brand.json only with a reason in the report.

**Engineering rules this style obeys** (each one broke a render once):
- every class/id is prefixed `og-` (a generic `.tw` once stacked the tagline words);
- clip-path strings keep **every number distinct** on both ends (sub-pixel epsilons): the
  browser normalises `inset(0px 0px 0px 0px round 0px …)` to `inset(0px)`, GSAP then sees a
  different number count and jumps at the end instead of tweening — the door popped in
  closed. The elliptical `a b c d / e f g h` radius form is avoided for the same reason;
- orbs are a solid colour plus a box-shadow glow, not radial gradients (heavy-overlay rule);
  their positions come from a fixed seed in Python, not `Math.random()`;
- per-orb ids and tween times so no two tweens overlap on one property;
- initial hidden states are CSS (`opacity: 0`, `stroke-dashoffset: 100`), never early sets.

**Sound.** The plan carries cue dicts for the sound pipeline (`plan["cues"]`, and
`info.cues` in `build/outro.json`): `{name, t, base_vol, exempt: true, stand_in}` —
`soft_whoosh` at O+0.05, `portal_suck` at O+1.12, `logo_sting` at O+1.7 (base volumes .30 /
.30 / .26, scaled to the voice by the sound step; exempt = deliberate beats, never slid off
words). Until the sound step places them from its library, the outro's own synthesised
stand-ins play (`osfx_page`, `osfx_rush`, `osfx_shimmer`, levelled as in §5); a sound step
that places the cues drops the matching `stand_in` clips.

### `portal` — the circle (4.4 s)

| t | what happens |
|---|---|
| 0.00 | page-turn swish. The end background is already behind the speaker. |
| 0.02 – 0.62 | the frame closes into a circle around the face (`clip-path: circle()`, power3.inOut) |
| 0.62 – 1.24 | the circle flies and shrinks into the landing point (power3.inOut) |
| 0.86 – 1.48 | **vault, beat 1:** the ring of the logo around the hole rotates in on the hole centre (−120° → 0°, scale 1.3 → 1) while a radial mask opens from the hole to ~2.25 × its radius |
| 1.22 | vault thunk — the circle lands in the hole |
| 1.48 – 2.23 | **vault, beat 2:** the mask opens from the ring to the whole mark (the KO "K, then wordmark" beat); logo shimmer |
| 1.75 – 2.15 | the face dissolves out of the hole, leaving the clean mark |
| 1.95 | brand hairline draws under the logo (scaleX 0 → 1) |
| 2.05 / 2.40 | tagline rises, handle fades up |
| 1.24 → end | slow push, scale 1 → 1.035, pivoting on the hole (so the hole never drifts off the face) |
| end − 0.44 | fade to the background colour |

**Hole landing.** `brand.json` lists the logo's enclosed transparent counters, best first.
`holes[0]` is used when its roundness ≥ 0.78, it is at least 40 px in radius on screen
(the logo grows up to the size limits to reach that), and the logo is genuinely
transparent at its centre. The circle radius on screen is the hole radius × 1.05: the logo
sits **above** the face layer, so the 5 % overscan disappears under the ink and the hole's
anti-aliased edge never shows background. The flight keeps the A-roll's transform-origin
(50 % 30 %) and solves `x, y, scale` so the local circle maps exactly onto the hole:
`scale = r_hole / r_circle`, `t = hole − origin − scale·(face − origin)`.

**No usable hole** (none, too small, not round, or filled): the circle lands on the logo
centre, turns into a brand-primary disc, and the disc opens like an iris into the logo.
The disc is an SVG circle whose stroke is the fill (`r = R/2`, `stroke-width = R`); the
tween grows `r` and thins the stroke with the same ease as the logo's radial mask, so the
ring's inner edge and the logo's reveal edge are the same line at every frame.

> **Filled counters.** Many logo files ship with the O's counter painted white. That is
> not a hole, and on a paper background it shows as a white dot. `brand_from_logo.py`
> should knock out enclosed near-white regions; `outro.py` refuses to land a face under
> opaque pixels and falls back to the disc.

### `line` — quiet and premium (3.8 s)

A brand hairline (5 px) crosses the frame in **reading direction** — right → left for
Hebrew — over 0.55 s, and the footage is clipped behind it (`inset()`), drifting 60 px with
it: the page turns from the footage to the background. From 0.40 the logo wipes in, also in
reading direction (`inset(0 0 0 100%)` → `inset(0)`); the hairline under it draws from the
right at 0.85; from 1.05 the tagline types one character at a time (≤ 55 ms each,
grapheme-safe: niqqud stays with its letter); the handle fades up after it. Slow push
1 → 1.03, fade out. Hebrew letters do not join, so per-character spans keep the shaping;
for Arabic, type by word.

### `impact` — energetic (3.4 s)

Hard cut to brand primary at `O` (with a short rising air cue into it). The logo slams in
from as large as the **safe zone** allows (≤ 1.6×) with a 14 px motion blur → 0.97 in
0.26 s (power4.in), then settles to 1.0 with `back.out(3)`. On the landing: a white flash
(0.85 → 0 in 0.32 s), a 0.25 s decaying shake of the lockup (±9 px, explicit steps — no
randomness), the slam hit. Hairline at 0.55, tagline 0.62, handle pops at 0.90 with a soft
pop. Push 1 → 1.04, fade out.

> The slam used to start at 1.6×; a wide logo then crossed the rail for three frames and
> the grid gate failed. The grid rule wins over a bigger slam — the blur sells the speed.

### Background and logo variant

- `gate`: always the blurred, darkened last frame (the footage's own world, gone dark).
- `portal` / `line`: **paper** (`--brand-paper`, the bone look of the original) unless the
  logo's colours fail against it and pass against the dark gradient
  (`--brand-grad-a → --brand-grad-b`, a plain linear gradient: a radial glow bands
  visibly in an 8-bit encode). `background` in the config forces either.
- `impact`: always brand primary.
- The logo is shown in full colour when ≥ 85 % of its opaque pixels stand off the
  background (contrast ≥ 1.45:1 — a mark, not body text; gold on bone measures ~1.8).
  Otherwise `logo.on_light` / `logo.on_dark` from brand.json, else a one-colour
  silhouette generated into `assets/outro/` (an `<img>`, so the grid gate still sees it).
- Text: ink on paper, white on dark, `--brand-on-primary` on primary. The handle uses the
  contrast-checked highlight (`--hl-on-light` / `--hl-on-dark`). The hairline is brand
  primary unless it fails against the background.

---

## 4. The hand-off — no jump

The outro starts while the A-roll is still on screen (default 0.2 s before its end, inside
the 0.45 s closing tail, after the last word):

- `assets/outro_last.png` is the A-roll's **last** frame at full resolution, decoded
  with an explicit bt709 matrix (the renderer shows video as bt709; ffmpeg's png path
  otherwise guesses and the colour shifts on the cut).
- `#ofreeze` has the A-roll's box, `object-fit` and `transform-origin`, and starts in the
  beat map's final A-roll state (`scale 1.02, y 0` for full-screen; `y 770` after a panel —
  `outro.aroll_state()` mirrors `build_index.py`'s rule and must change with it).
- Every outro tween drives `"#aroll, #ofreeze"` **together**, so whichever is on screen is
  in the same place. The video is animated itself — never wrapped in a timed element.
- The freeze appears one frame before the video's end (videos are inclusive of their end
  frame, images/divs exclusive), on top, showing the identical picture.

Measured: on a test A-roll whose last 8 frames are identical, the step from the last
rendered video frame to the first freeze frame differs by a mean of 0.26 / 255 per channel,
the same as the steps between identical video frames (0.17-0.76) — encoder noise, no jump.

---

## 5. Sound

All three cues are synthesised by `outro.py sfx` (no licence, no attribution) into
`audio.sfx_dir`: `outro_page` (page-turn swish), `outro_vault` (low thunk + metal ring +
latch), `outro_shimmer` (soft bell partials + air), `outro_rush`, `outro_slam`, `outro_pop`.

**Levels are measured, never copied** (references/sound.md). The voice reference is the
90th percentile of the A-roll's EBU R128 momentary loudness (the level of spoken words).
Each cue is set by its own peak momentary loudness so it lands:

| cue | under the voice reference |
|---|---|
| vault thunk / impact slam | 3 / 2 dB — the landing, the loudest moment of the outro |
| page swish | 5 dB |
| shimmer, rush, pop | 6 dB |

Under speech the house rule is ~7 dB; in the outro there is no voice to protect and the
cues are the foreground, so they sit closer — but never above the speech level, and an
outro 15 dB under the dialogue is a bug. Trim all of them with `audio.outro_sfx_trim_db`.
Volumes come from `volumedetect`-free loudness on purpose: a decaying cue's *mean* is ~10 dB
below how loud it lands.

**The music bed** reaches the end and resolves. The music clip that plays to the A-roll's
end is carried to the outro's last frame (`media.json` keeps its spoken-part length; the
build extends it). A `data-automation` volume lane (it **replaces** `data-volume` —
verified: lane 0.5 on volume 0.5 measures −6 dB, not −12):

1. holds the spoken-part gain until `O + 0.1`;
2. moves over 0.6 s to the outro gain: the bed's loudness **over the exact source window
   the outro plays** (before the fade) is measured at its 90th percentile — a track that
   crescendoed inside the outro landed 6.5 dB over target when the median was used — and
   the gain is set so the bed sits
   `audio.outro_bed_below_voice` (9 dB) under the voice reference — under the cues.
   Tracks change loudness along their length (the test bed rose 8 dB between intro and
   chorus), so a fixed "+5 dB" can make the outro louder than the speech. Clamped to
   [½ × spoken gain, max(spoken gain, `bgm_after_flare`)];
3. fades to 0 over the last second, reaching silence on the last frame.

If the track runs out before the outro ends, the build says so: pick a later
`media_start` or a longer track. For a musical button rather than a fade, choose the
bed's `media_start` so a phrase ends on the outro's last frame.

---

## 6. QA — before showing it

```bash
python3 scripts/build_index.py                      # prints the outro plan + build/outro.json
python3 scripts/validate.py --expect build/expected.json   # composition END = outro end
python3 scripts/grid.py check index.html --at <O, O+0.2, … every 0.2 s to the end>
npx hyperframes render --quality draft --fps 25 -o renders/proof.mp4
python3 scripts/grid.py overlay renders/proof.mp4 --at <O+1.3, O+2.6, end−0.5>
```

Pull frames every 0.2 s from `O − 0.4` to the end of the **encoded** file and look at them:

- [ ] no jump at the hand-off (`O`, A-roll end − 0.04, A-roll end): same framing, same colour
- [ ] the circle / door surrounds the face (not the forehead or the chin); `face` overrides it
- [ ] gate: the door CLOSES over ~0.7 s (it must not pop in — the clip-path epsilon rule),
      is centred on the face, lands inside the opening with no background sliver at its
      edges, the mark draws around it, the words come out of the mark's edge, the light
      fills the doorway as the speaker fades
- [ ] gate: `logo.mark` picked the symbol a designer would (look at `brand/mark.png` and the
      `word_*.png` files before the build)
- [ ] portal: the circle lands exactly in the hole — no background ring between face and ink
- [ ] the logo is the real file, unclipped, inside the safe zone; nothing in a red zone
- [ ] tagline reads in the right order (RTL for Hebrew), in the brand font, one line
- [ ] the last caption is gone from the outro start (`finish.py` prints "captions off from …")
- [ ] the master is as long as the render (finish.py fails if the overlay truncates it)
- [ ] audio: the vault/slam is clearly audible but not louder than the speech; the bed is
      under the cues and reaches silence on the last frame (`ebur128` momentary over the outro)
