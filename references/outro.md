# The animated logo outro ("סגיר")

The signature close: when the speech ends, the speaker shrinks into a
circle around the face, the circle flies into the logo and lands exactly in its hole, and
the logo builds around it. `scripts/outro.py` rebuilds that choreography from any logo,
using what `scripts/brand_from_logo.py` measured (`brand/brand.json`). Two alternatives
exist for brands where it does not fit.

**It is opt-in.** It runs only when there is a logo **and** the user said yes. A reel
without a logo, or a user who did not ask for it, gets no outro, and a 16:9 piece never
gets one. Never switch it on by default.

---

## 1. The ask

In the opening conversation, only if a logo was supplied (or `brand/brand.json` exists):

> יש לך לוגו? רוצה סגיר מונפש בסוף הסרטון?

If yes, offer the three looks in one message, each in one line, and recommend `portal`:

> **פורטל** (מומלץ): הפריים שלך נסגר לעיגול סביב הפנים, עף לתוך הלוגו, והלוגו נבנה סביבו.
> **קו**: קו דק בצבע המותג מעביר דף מימין לשמאל, הלוגו נחשף והסלוגן מוקלד אות אחר אות.
> **אימפקט**: חיתוך חד לצבע המותג, הלוגו נוחת בעוצמה עם הבזק קטן. לסרטונים אנרגטיים.

Then ask for the two lines under the logo:

> מה לכתוב מתחת ללוגו? משפט קצר (עד 40 תווים) ושם המשתמש שלך, למשל @name

Both are optional. Do not invent a tagline; an empty one simply leaves it out. Use the
user's exact wording and spelling.

No answer, or "no" → no outro. Write nothing to the config.

---

## 2. Switching it on — the contract

`config.json` (the project default):

```json
"outro": {"enabled": true, "style": "portal", "tagline": "…", "handle": "@name"}
```

`media.json` may override per reel (it wins over the config): `"outro": true`,
`"outro": false`, or an object with any of:

| key | meaning | default |
|---|---|---|
| `style` | `portal`, `line` or `impact` | `portal` |
| `tagline` | one line under the logo, in the reel's language (RTL aware) | none |
| `handle` | e.g. `@name`, always rendered LTR-isolated | none |
| `start` | composition second the outro begins | A-roll end − 0.2 s |
| `face` | `[x, y]` of the face in composition px, if the measurement is wrong | measured |
| `background` | `paper` or `dark` (portal/line; impact is always brand primary) | chosen by contrast |
| `center_y` | vertical centre of the lockup | 860 |
| `no_hole` | `true` forces the disc landing even when the logo has a hole | false |

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
python3 scripts/outro.py preview --style portal --render     # → build/outro_preview/
python3 scripts/outro.py plan                                # what the build will emit
python3 scripts/outro.py face --aroll assets/aroll.mp4       # the measured face centre
```

---

## 3. The three styles

All times are seconds after the outro start `O`. Everything sits inside the Reels grid:
the lockup is centred on **x 500** (not 540) around **y ≈ 860**, inside the safe zone
x 60-940 / y 220-1520 and inside the 3:4 profile crop. Text uses `var(--brand-font)` and
the configured language direction.

### `portal` — the signature (4.4 s)

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
- [ ] the circle surrounds the face (not the forehead or the chin); `face` overrides it
- [ ] portal: the circle lands exactly in the hole — no background ring between face and ink
- [ ] the logo is the real file, unclipped, inside the safe zone; nothing in a red zone
- [ ] tagline reads in the right order (RTL for Hebrew), in the brand font, one line
- [ ] the last caption is gone from the outro start (`finish.py` prints "captions off from …")
- [ ] the master is as long as the render (finish.py fails if the overlay truncates it)
- [ ] audio: the vault/slam is clearly audible but not louder than the speech; the bed is
      under the cues and reaches silence on the last frame (`ebur128` momentary over the outro)
