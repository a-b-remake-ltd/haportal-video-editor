# Reference analysis — adapt the edit to a video the user likes

The user hands you a reel they like (a creator, an agency piece) or their own previous videos
("my style"). You measure what can be measured, LOOK at the frames for the rest, write a style
document, and turn it into concrete config values for this edit.

**Style ≠ template.** Copy the editing LANGUAGE: rhythm, caption treatment, kinetic type,
motion quality, sound design. Every object, word and animation in the new edit comes from the
new video's own script. The objects in a reference (a search bar, a phone, app tiles) show a
*device*. They are never a shopping list.

---

## When to ask

- **Offer it, never require it.** At intake, once: *"יש לך סרטון שאתה אוהב את הסגנון שלו, או
  סרטונים קודמים שלך שתרצה שאמשיך באותו קו? אם לא, אני עובד בסגנון הבית."* No reference = house
  style. Never block the edit waiting for one.
- One reference is enough. Two or three of the same creator are better. More than three adds
  noise, not signal.
- A link is not a file. Ask for the file (a screen recording is fine). Never rip a platform video
  yourself.

---

## What to run

```bash
python3 scripts/analyze_reference.py ref.mp4 [ref2.mp4 ...] --out style/ --transcribe
```

About 30–60 s per minute of reference (Demucs is ~20 s of that; the stems are cached in
`style/<ref>/stems/`, so a re-run is fast). Flags: `--no-stems` skips the split, and then music
is **not measured**, not "absent". `--transcript words.json` reuses a transcript.
`--threshold` / `--min-gap` tune the cut detector. `--strips N` sets how many cuts get a strip.

Output, per reference in `style/<ref>/`: `analysis.json`, `REPORT.md` (the skeleton),
the sheets, `audio_series.json` (voice / music dB per 0.5 s). Plus `style/style.draft.json`,
and `style/combined.json` when there are several references.

### How far to trust each number

| Measure | Method | Trust | On the Rollin reference (doc vs script) |
|---|---|---|---|
| Cuts, shot lengths | scdet score ≥ 8, merged within 0.30 s | High for hard cuts; dissolves land in `soft` | 29 cuts / 12.1 per 30 s / median 1.6 s: **exact** |
| Music under voice | Demucs stems, median (voice − music) over speech, swells excluded | High | doc 12–14 dB; script 11.5 at rest, by thirds 9.8 / **13.2** / 9.6 (the doc read the middle) |
| Swells / drops | music stem vs its own median, 1.5 s smoothing | High | hook swell, the 34–38 s drop before the punchline, 41 s and 62 s swells: all found |
| SFX / transients | onsets on the no-vocals stem, periodic runs removed | **Upper bound**: strong music hits count too | every SFX the doc lists is in the list, plus music hits |
| Caption centre | rows where bright, edge-dense pixels keep CHANGING | High when confidence is high; read `caption_band.png` anyway | doc 65.5 %; script 65.4 % |
| Caption size / plate guess | glyph-band FWHM, white fill in the band | Rough: it under-reads ascenders | confirm by eye |
| Palette | dominant: palettegen ranked by pixel share; accents: hue clusters of bright saturated pixels | Starting point only; skin and sky get in | graphic blue found; read the exact hex off the designed frames |
| Words / s | `scripts/transcribe.py` on the vocals stem | High | 3.1 words/s while speaking |

---

## Look at the images, in this order

You can read PNGs. **A number you did not look behind is a guess.** `REPORT.md §0` lists the
files. Read every one:

1. `hook_0-3s.png`: the first 3 s every 0.25 s. Write §5 one line per beat. What stops the scroll
   on frame 1?
2. `sheet_shots.png`: one mid-frame per shot, labelled `#index start (dur)`. Mark each shot A
   (speaker), B (B-roll) or G (graphic / designed moment) in the §2 table, then compute
   `broll_share`. Shots flagged "looks different" are your designed-moment candidates.
3. `caption_band.png` (+ `_2`, `_3`): the caption band over time, magenta ticks every 5 % of
   height. Words per card, one line or two, weight, colour, plate / shadow / stroke, how a card
   swaps, what happens during graphics.
4. `strips/cut_XX.png`: 7 consecutive frames around each early cut. Hard cut, dissolve, flash,
   push, whip, frame-shrink? On a phrase boundary?
5. `sheet_1s.png`: one frame per second. Overall feel, kinetic headlines, every designed moment
   with its time range.
6. `grid_N.png`: the Reels grid over key frames. Red is hidden by the app; yellow is the house
   caption band. Does the reference put anything readable under the UI? If so, the grid
   overrides it: note it and plan to clamp.

If a sheet is unreadable (too dark, too small), pull the frames you need with ffmpeg at full
size. Never fill a TODO from memory of "what reels like this usually do".

---

## Write `style/STYLE.md`

One file per edit, merging all references. Use the structure of
`references/examples/rollin-style-example.md` (a worked example of the OUTPUT, not a style to
copy):

1. Overall feel [E]
2. Rhythm [M]
3. Captions [M position, E look]
4. Kinetic / headline type [E, timings M]
5. Hook second by second [M + E]
6. Designed moments [E], each written as a **device** + the line it illustrated
7. Sound [M on stems]
8. Look [M palette + E]
9. How to apply: the concrete values, and what will be invented fresh

Rules:

- Tag every line **[M]** (from `analysis.json`), **[M~]** (heuristic you confirmed by eye) or
  **[E]** (eye). A number without a tag is not allowed.
- Copy numbers from `analysis.json`. Never round them toward what you expected.
- Describe what you SEE, including what contradicts the summary. Rollin's captions are "1–3
  words, one line" in the doc, but `caption_band.png` shows several two-line cards. Write that
  down, then apply the house one-line rule anyway.
- §6 names the device, then the example: "UI metaphor that types the spoken query
  (there: a search bar)", never just "search bar at 3.6 s".

---

## Write `style/style.json`

Start from `style/style.draft.json`. Measured fields are filled in; everything [E] is `null`
and listed in `_todo`. Fill every field, delete `_todo` and `_about`, save as
`style/style.json`. `apply_style.py` refuses a file with open TODOs.

| Key | From | Notes |
|---|---|---|
| `captions.style` | §3 | `"plate"` (black on translucent white, house) or `"shadow"` (white, soft shadow, no box). Stroke or outline references map to `"shadow"`. |
| `captions.max_words` | §3 | 1–4. Count the words per card on the band sheet. |
| `captions.weight` | §3 | 300 thin … 900 black |
| `captions.center_y` | §3, `center_y_1920` | px on a 1920-high frame. The script clamps it to the grid. |
| `brand.caption_size` | §3 | px at 1080 wide. From the glyph height: shadow ≈ glyph / 0.62, plate ≈ glyph − 46. Check it against the frame. |
| `style.target_cuts_per_30s`, `style.median_shot_s` | §2 | measured |
| `style.broll_share` | §2 table | (B + G time) / duration, clamped to 0.5 |
| `style.headline` | §4 | free text: "word-by-word kinetic, mixed weights, keyword in brand colour" |
| `style.transitions` | §2/§6 strips | free text |
| `style.hook_notes` | §5 | free text, as a device, not objects |
| `style.sfx_per_min` | §7 | your count of the real SFX (the measured number is an upper bound) |
| `style.notes` | anything | list of short rules the build should honour |
| `style.palette` | §8 | hex list, verified on the graphic frames |
| `audio.music_db_under_voice` | §7, `music_db_under_voice_rest` | positive dB |
| `audio.music_character` | §7 | genre, pulse, melodic or bed, how it moves with the story |

Then:

```bash
python3 scripts/apply_style.py --style style/style.json --dry-run    # read every line
python3 scripts/apply_style.py --style style/style.json [--lock captions.style]
```

It prints every change, every **CLAMPED** value with the reason, every value it **kept**
because something higher wins, and every key it **refused**. Tell the user in one line about
anything clamped or refused ("the reference keeps captions lower, under the Reels UI. I kept
them inside the safe zone").

---

## Adapt, never copy

The reference shows HOW a moment is built. The new script decides WHAT is in it.

> Reference: on "people look for a trick to become a personal brand", a dark search bar types the
> query while the speaker talks.
> New script: *"I spent three weeks building a spreadsheet nobody opened."*
>
> **Bad:** a search bar typing "spreadsheet". It is the reference's object pasted onto a line it
> does not illustrate.
> **Good:** the same device (a UI that builds in sync with the words, then shrinks back into the
> footage), carrying THIS line: a spreadsheet filling cell by cell to "three weeks", then a
> "Seen by 0" read-receipt popping on "nobody opened". The comic wink is the read-receipt.

Before every element: *does this serve this sentence of this video?* Add a light, witty touch
where it fits (a visual pun, an exaggerated UI, a deadpan counter). Never cringe, never a meme
sound. The ending comes from the content too: a logo sting only if the brand has a logo and the
user wants it (`references/outro.md`).

---

## Sound: never conclude "no music" from the pauses

A bed 12–15 dB under the voice is inaudible in a loudness scan of the pauses. It ducks, gets
gated, or sits under room tone. Split first, then measure the music stem on its own:

```bash
uvx --from demucs --with soundfile demucs --two-stems=vocals -o <dir> <audio>
```

`analyze_reference.py` does this by default. With `--no-stems`, or if the split failed, the
report says **NOT measured**. Treat that as unknown, rerun with stems, and never write "no
music" in STYLE.md.

---

## Several references

- `style/combined.json`: medians of every numeric field, plus `disagree` (spread > 30 % of the
  median, or different labels). The draft is built from the medians.
- Resolve every disagreement by eye and say in STYLE.md which reference wins **per field**.
  Never average incompatible choices: a plate style and a shadow style do not make a
  half-plate. Pick one.
- If they disagree on everything, they are not one style. Ask the user which reel is closest
  to what they want.

## "My own previous videos" as the reference

- This is the strongest reference: it is what the user's audience already knows. Weight it
  above a creator they admire.
- Copy their recurring choices (caption look, pace, music character), not their accidents. A
  caption that drifted under the Reels UI or a 1.2 s dead gap is a defect to fix, not a style.
  Say so once, kindly.
- Their own logo and fonts are already the brand (`references/brand.md`). A palette read off
  their videos never replaces colours extracted from their logo.

---

## Precedence

| Conflict | Wins | How |
|---|---|---|
| User asked for X explicitly vs reference | **User** | `--lock key` (stored in `style.locked`) |
| Brand (logo colours, brand fonts) vs reference palette / type | **Brand** | brand keys are refused, only `style.palette` is written |
| Value set by hand in `config.json` vs reference | **Hand value** | kept, reported. `--force` overrides it, but never a locked key. |
| Reference vs house default | **Reference** | written by `apply_style.py` |
| Anything vs the grid | **Grid** | `captions.center_y` clamped into y 220–1220 (safe zone minus the bottom-card zone) |
| No logo | reference palette | `style.palette` may drive motion accents. `brand.accent*` is never touched. |

Order: explicit user request > brand (logo, fonts) > reference > house defaults. The grid sits
above all four.

---

## What a reference may NOT change

- **Verbatim captions.** Every word spoken, typos fixed, nothing paraphrased. Still one line per
  card, at most 4 words.
- **The grid.** Nothing readable under the Reels UI, whatever the reference does.
- **Fonts.** Free faces only (`references/fonts.md`). Name the reference's face in STYLE.md and
  pick the closest free one. Never install or embed the commercial original.
- **Numbers.** No invented figures in graphics, even if the reference shows a big stat.
- **People.** No AI stand-ins for identifiable real people, even if the reference has a
  collage of real faces. Use real, licensed or user-supplied images, or a device without faces.
- **Brand colours.** A reference palette never repaints a client's logo colours.

---

## Verify on the render

Run the analyzer on your own draft render and compare it with `style.json`: cuts per 30 s,
median shot, caption centre (the clamped value), music under voice at rest. A gap of more than
~20 % is a note you have to answer before showing the cut.

```bash
python3 scripts/analyze_reference.py renders/draft.mp4 --out build/selfcheck/
```
