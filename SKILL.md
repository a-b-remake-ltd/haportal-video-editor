---
name: haportal-video-editor
description: HAPORTAL - VIDEO EDITOR. Build a finished 9:16 reel (Instagram Reels, TikTok, YouTube Shorts) from a raw talking-head take plus B-roll, composed and rendered with HyperFrames. Hebrew-first, any language. It adapts to a reference video the user supplies, places everything on the Instagram Reels grid, uses free fonts only, recolours motion graphics and highlights from a logo, builds kinetic word-by-word headlines and designed moments, and can close on an animated logo outro. Use for "ערוך לי סרטון", "תערוך את הרילס", "תעשה מזה רילס", "edit my video into a reel". Not for long-form, horizontal or podcast-episode edits, or a plain trim of one clip.
---

# HAPORTAL - VIDEO EDITOR

Turn a raw talking-head recording plus B-roll into a finished 1080x1920 reel. Cut it to the
speech, match the look the user points at, lay it out on the Reels grid, caption every word,
colour it in the brand, score it, close it on the logo, and master it.

Built on **HyperFrames**, an HTML/CSS/GSAP video renderer, driven from **Claude Code**. The
composition is an `index.html`. Every clip is an element with `data-start` / `data-duration`,
and all animation runs on one paused GSAP timeline. The composition is **generated** from a
beat map plus a manifest and never hand-edited.

**Scope:** raw footage → finished master. **Not in scope:** scripting, ideation, publishing.

Paths below: `$S` is this skill's folder (e.g. `~/.claude/skills/haportal-video-editor`).
Commands run from the **project** folder, which holds the footage, `config.json` and outputs.

---

## Read this first

This skill is a **quality bar, not a menu.** Every rule here exists because a real editor
rejected the alternative. The most common failure is doing 90% of it. A reel that is "almost
right" reads as low effort to a viewer who cannot say why.

Four habits matter more than any single rule:

1. **Measure, don't eyeball.** Nearly every rule has a number attached. Get the number from
   the file (ffprobe, an RMS scan, a pixel mask, the grid gate), not from a guess or a
   screenshot.
2. **A note names a symptom. Find the systematic cause.** "You cut into the next sentence" is
   frame-quantisation drift. "The rotoscoping is sloppy" is a downsample ratio. Patch the
   instance and the same class of bug comes back next round. Fix the class, then name the root
   cause with the measurement that proves it.
3. **Prefer a gate over a rule.** A rule can be missed. A failing check cannot. When a note
   repeats, encode it in `scripts/preflight_qa.py` or as an `assert` in the build. Run the
   negative test when you add one. A check you have never seen fail is not a check.
4. **Never show anything you have not evaluated yourself.** Self-evaluate the *rendered* file
   at every cut before the user sees a frame (§5).

**Re-read this file at the start of every round**, including revision rounds in a session you
already started. Notes get folded back in between turns, and the copy you loaded an hour ago
may be stale.

---

## What makes this skill HAPORTAL

Seven capabilities sit on top of the core pipeline. Each has a reference file. Read it before
the step that uses it.

| # | Capability | What it does | Reference | Tool |
|---|---|---|---|---|
| 1 | **Adapts to a reference** | Measures a reel the user likes (cut rate, caption treatment and position, music under voice, SFX density, palette), shows you the frames to judge the rest, writes `style/STYLE.md` + `style.json`, and merges it into this edit's config. It copies the *language* of the edit, never its objects. | `references/reference-analysis.md` | `analyze_reference.py`, `apply_style.py` |
| 2 | **Instagram Reels grid** | Nothing readable under the app UI. Safe zone x 60-940, y 220-1520, centre x 500, caption band y 1110-1190. The gate fails the build otherwise. | `references/grid.md` | `grid.py` |
| 3 | **Free fonts only** | A registry of verified OFL/Apache faces with Hebrew coverage. A gate refuses any commercial or unlicensed face, even as a fallback, because users publish commercially. | `references/fonts.md` | `fonts.py` |
| 4 | **Brand colours from the logo** | Extracts colour roles from the logo, contrast-checks them for text on dark and on light, and recolours every card, keyword highlight and animation through CSS tokens. | `references/brand.md` | `brand_from_logo.py` |
| 6 | **Kinetic headlines** | The key sentence of a beat builds word by word as it is spoken: mixed weights, the keyword in the brand colour, right-aligned on the grid. Captions step aside while it is up. | `references/kinetic.md` | `kinetic.py` |
| 7 | **Designed moments** | Reusable, brand-coloured scenes tied to what is said: paper world with the page turn, question card, checklist, stamp, chips, search bar, the frame "fly", punch-ins. 2-4 per reel, word-synced. | `references/moments.md` | `moments.py` |
| 5 | **Animated logo outro** (opt-in) | The signature close: the speaker shrinks into a circle that flies into the logo, which builds around it. Two alternatives: `line` and `impact`. Only with a logo **and** when the user wants it. | `references/kinetic.md` | Kinetic headlines: when, markup, word sync, caption hiding |
| `references/moments.md` | The designed-moments catalogue: what each is for, JSON per type, sound |
| `references/outro.md` | `outro.py` |

**Precedence** when they disagree: the user's explicit request > brand (logo, fonts) >
reference > house defaults. **The grid always wins.** A reference never moves anything under
the Reels UI. Brand colours always beat a reference palette.

---

## Scope: when NOT to use this skill

This builds a **9:16 short-form composition**. A long-form or horizontal edit, a podcast or
YouTube episode, or "just trim this clip" is the wrong tool. Say so and hand off rather than
forcing a reel pipeline onto it.

---

## 0. Setup

### Once per machine: one command

```bash
python3 $S/scripts/doctor.py            # checks everything, prints the exact fix for each miss
python3 $S/scripts/doctor.py --install  # installs what it safely can (venv, deps, fonts, SFX, GSAP)
```

`doctor.py` checks ffmpeg/ffprobe, Chrome, Node, HyperFrames, Python and the transcriber, and
runs `setup_assets.py`. That script creates `$S/assets/` with free fonts (Heebo, Roboto Slab,
Inter), synthesised SFX, procedural lens flares and a local GSAP. Network is needed once.
**Rotoscoping is off by default.** It needs torch and is heavy: `doctor.py --install
--with-roto`. Everything else runs without it.

### Once per project

```bash
cp $S/config.example.json config.json      # language, brand, grid profile, levels
mkdir -p scripts && cp $S/scripts/beats.py scripts/beats.py   # THE beat map — edited per reel
```

`beats.py` ships as a deliberate stub, because the beat map *is* the edit and cannot be
guessed. Every script prefers the project's copy over the skill's, and `build_index.py`
prints which file it loaded and how many beats it found. A stub nobody filled in is
visible, not silent. Everything creator-specific lives in `config.json` and `beats.py`,
never in the skill.

**Work locally.** Copy footage to a local project folder. Preview and render over a network
or external volume are painfully slow, and losing the volume mid-edit loses the edit.

### Known-hostile tooling (these cost real hours to rediscover)

- **`ffmpeg -v error` silently kills `volumedetect` / `silencedetect` / `astats` output.** A
  level probe then returns empty and looks like a missing file.
- **ffmpeg stderr vanishes inside shell `for` loops** in some agent harnesses. Drive
  multi-clip probing from Python (`hfcfg.run`), never a bash loop.
- **Never transcribe through the `mlx_whisper` CLI.** It names output from the first dot in
  the filename, and batching files into one call keeps only the last result. Use
  `scripts/transcribe.py`, which loads the model once, in-process.
- **`npx hyperframes preview` rewrites `index.html` while it runs.** Kill it before building.
- **File access:** "Operation not permitted" on macOS means the host app lost Full Disk
  Access. Re-grant it in System Settings → Privacy & Security, then fully restart.

---

## 1. How a session runs

Full method in `references/workflow.md`. The shape:

1. **Inventory.** ffprobe every source. Transcribe (`transcribe.py`) and pack
   (`pack_transcript.py` → `takes_packed.md`, the primary reading view). If `project.md`
   exists, summarise the last session in one sentence.
2. **Pre-scan** the packed transcript for slips, retakes and Hebrew mis-hearings
   (`references/hebrew.md`).
3. **Converse, in one short message in the user's language** (Hebrew wording below; translate
   it for anyone else). Always offer the three signature options together, plus whatever the
   material raises:
   - "יש סרטון רפרנס שאתה אוהב את הסגנון שלו?" (→ capability 1)
   - "יש לוגו? נתאים את הצבעים למותג" (→ capability 4)
   - "רוצה סגיר מונפש עם הלוגו בסוף?" (→ capability 5, only if there is a logo)
   - "הסרטון אורגני, או שירוץ גם כמודעה ממומנת?" (→ grid profile `reels` / `ads`)
4. **Propose the strategy** in 4-8 plain sentences in the user's language: shape, take choices, look,
   captions, music, outro, length. **Wait for approval before touching the cut.**
5. **Execute** the pipeline (§2).
6. **Self-evaluate**, then present (§5).
7. **Iterate** on notes. Never re-transcribe an unchanged file.
8. **Persist.** Append the session to `project.md`: strategy, decisions and why, open items.

Ask only about what this file cannot decide: music choice, B-roll download approval (with the
exact URLs), the final filename, and taste calls not covered here.

---

## 2. The pipeline

Each phase links to the reference that carries its detail. **Read that file before the
phase**, not after.

| # | Phase | Reference | Gate before moving on |
|---|---|---|---|
| 1 | Transcribe + pack | `references/workflow.md`, `references/hebrew.md` | Transcript audited; every inferred correction listed for the user |
| 2 | Reference (if given) | `references/reference-analysis.md` | `style/STYLE.md` written from the sheets; `apply_style.py` clamps reported |
| 3 | Brand (if logo) | `references/brand.md`, `references/fonts.md` | `brand.css` exists; monochrome logo → accent asked; fonts fetched |
| 4 | Cut the A-roll to the speech | `references/cutting.md` | Zero dead space, no breath after a cut, segments frame-exact |
| 5 | Map the beats | `references/captions.md` §beat map | Every beat start is a real segment boundary |
| 6 | Lay out the frame | `references/layout.md`, `references/grid.md` | `grid.py check` passes |
| 7 | Caption every word | `references/captions.md`, `references/hebrew.md` | Zero gaps or overlaps, one line, no sticky ending, "AI" protected |
| 8 | B-roll, graphics, headlines, moments | `references/graphics.md`, `references/kinetic.md`, `references/moments.md` | Every clip, headline and moment has a reason tied to its sentence; brand tokens only; 2-4 headlines and 2-4 moments, never filler |
| 9 | Score it | `references/sound.md` | Music from frame 1, bed measured in dB under the voice, no SFX on a word |
| 10 | Outro (opt-in) | `references/outro.md` | Seamless hand-off; lands in the logo; inside the grid |
| 11 | Build and render | `references/hyperframes.md` | Validator, grid and font gates pass; the encoded file sampled |
| 12 | Master and deliver | `references/delivery.md` | SDR bt709, 30-35 Mbps, −14 LUFS, true peak ≤ −1 dBTP |

### The commands, in order

```bash
# 1 transcribe + pack (Hebrew → ivrit.ai Whisper, free and local)
python3 $S/scripts/transcribe.py raw/take.mp4 --out src/raw.json --words src/raw_words.json
python3 $S/scripts/pack_transcript.py src/raw.json            # → takes_packed.md
python3 $S/scripts/timeline_view.py raw/take.mp4 12.0 18.5   # drill into a doubtful moment

# 2 reference (optional)
python3 $S/scripts/analyze_reference.py ref.mp4 --out style/  # then READ the sheets, write STYLE.md + style.json
python3 $S/scripts/apply_style.py --style style/style.json

# 3 brand (optional)
python3 $S/scripts/brand_from_logo.py logo.png --out brand/
python3 $S/scripts/fonts.py fetch "Secular One" --dest assets/fonts   # a display face, if the look calls for one

# 4-7 cut, beats, captions
python3 $S/scripts/cut_aroll.py                               # islands → chunks → onsets → segments
python3 $S/scripts/reframe.py                                 # optional per-segment face-centred crop
python3 $S/scripts/map_words.py --raw src/raw_words.json      # the AUDITED raw text, moved onto the cut
python3 $S/scripts/transcribe.py assets/aroll.mp4 --out src/aroll.json --words src/aroll_words.json
python3 $S/scripts/map_words.py --raw src/raw_words.json --verify src/aroll.json   # every disagreement listed
python3 $S/scripts/captions.py                                # optimal split per sentence
python3 $S/scripts/fit_captions.py

# 8-11 build, gates, render
python3 $S/scripts/build_index.py --example > media.json      # first time only
python3 $S/scripts/build_index.py                             # → index.html + build/expected.json
python3 $S/scripts/validate.py --expect build/expected.json
python3 $S/scripts/grid.py check index.html
python3 $S/scripts/fonts.py guard index.html brand/brand.css
python3 $S/scripts/caption_layer.py                           # → assets/captions.webm (alpha)
npx hyperframes render --quality draft --fps 5 --crf 34       # proof render for debugging
HF_VIDEO_COVERAGE_THRESHOLD=0 npx hyperframes render --quality high --video-bitrate 32M
python3 $S/scripts/finish.py                                  # composite captions, pin SDR bt709

# 12 QA before anyone sees it
python3 $S/scripts/preflight_qa.py . --aroll assets/aroll.mp4 \
        --transcript src/words.json --render renders/final.mp4
```

---

## 3. Cut the A-roll: the number one quality gate

**Never cut on Whisper word-starts.** Whisper folds leading silence into a sentence's first
word, up to 1.2 s of dead air. One second of nothing before a sentence is the most frequently
caught mistake in this pipeline. Full method in `references/cutting.md`. The short version:

1. Find the takes in the packed transcript. **The last take of a repeated line wins.**
2. Map structure with `silencedetect`, **not** with Whisper, which smears aborted takes. Then
   re-transcribe **each speech island separately** to see which one is complete.
3. Cut at **speech-chunk** level. Split each chosen take on internal silences and transcribe
   each chunk alone. That exposes a false start hiding *inside* a good take.
4. Find the onset at **speech level (−26 dB)**, and for Hebrew by voice energy in the
   100-900 Hz band. Breath is high-band noise with no low band: it is not speech.
5. Land true speech **exactly one frame (0.04 s) into the segment.** Measure, shift, re-cut,
   re-measure until every segment reads 0.02-0.05 s.
6. **Quantise every segment to whole frames** and re-time each to PTS 0. A non-frame-aligned
   duration encodes longer than requested and every later beat fires early. Concat gaps break
   lip sync. Count packets, not seconds.
7. Derive each gap from the **original** pause: `gap = clamp(0.60 × pause, 0.15, 0.32)`.
8. **The closing segment takes a ~0.45 s tail.** Never clip a word end: Hebrew phrase ends are
   often soft (ן, ם, ה).

**Composition duration = the A-roll's real duration**, measured from the encoded file (plus
the outro tail when there is one), never the sum of planned durations.

---

## 4. Layout, captions, graphics, sound: the non-negotiables

**Layout** (`references/layout.md`, `references/grid.md`)
- **Everything readable inside x 60-940, y 220-1520, centred on x 500.** `grid.py check`
  must pass. This constrains the framing, not just the graphics.
- **The speaker is on screen nearly always.** Two or three short full-frame B-roll stretches
  per reel, maximum.
- **No transition on any B-roll, by default.** B-roll hard-cuts in and sits there. The
  exceptions: the hook flare, a slow push on full-frame A-roll, the outro, and the designed
  transitions a reference or the user opens (`style.transitions_allowed`: page-turn,
  frame-fly...). See `references/layout.md` §transitions.
- **A B-roll run holds** for the whole sentence it illustrates.
- **Reframe with a crop on the original high-res raw**, never a CSS transform.

**Captions** (`references/captions.md`, `references/hebrew.md`)
- **Every word, verbatim.** Fix transcription errors, never paraphrase.
- **Caption text comes from the raw transcript, mapped onto the cut** (`map_words.py`).
  Re-transcribing the finished A-roll is a CHECK only: with less context it hears worse
  ("ChatGPT" came back as "ChatGPGPT" in a real test).
- **Cards are split per sentence by an optimiser** (`captions.py`): size, sticky words,
  words that lean back ("הזאת", "מאוד", "אלף"), commas, clause openers, pauses and the
  plate width (it re-splits rather than shrinking the font).
- **One line, at most `captions.max_words` (4) words. Never two lines.**
- **Never end a card on a sticky word** (של, על, את, כי, מה, a bare number...).
- **"AI" and every acronym with an I** gets `class="ltr ai"`, set in Roboto Slab. In Heebo
  800 it reads "Al".
- House style: black text on a translucent white plate, no stroke. A reference may switch it
  to white text with a soft shadow (`captions.style: "shadow"`).
- **Zero dead space**: one track, each card running to the next start minus 0.005 s.
- **Five slots from one beat map**, imported by both builders. Speaker slots sit in the
  Reels caption band.

**Graphics and brand** (`references/graphics.md`, `references/brand.md`, `references/fonts.md`)
- **Every B-roll clip needs a direct, obvious reason tied to the sentence being said.**
  Concrete and narrative, never abstract filler (no bokeh, no glowing black box).
- **Colours come from the brand tokens** (`var(--brand-primary)`, `var(--hl-on-dark)`...),
  never a hard-coded hex. Keyword highlights on footage use `--hl-on-dark`. On paper or light
  cards use `--hl-on-light`. Both are contrast-checked.
- **Free fonts only.** Name only faces from `fonts.py list`, with `"Inter", sans-serif` as
  the fallback. Never Arial or Helvetica.
- **A hard claim needs its receipt in frame. Never invent a number.**
- **Real logos, downloaded. Never AI stand-ins for identifiable real people.**
- **A reference's objects are examples.** Derive designed moments from *this* script, with a
  light comic wink where it fits.

**Sound** (`references/sound.md`)
- **Music from frame 1**, on the track's actual first beat.
- **Measure the bed in dB under the voice, per section:** hook ≈12, designed moments ≈14,
  body ≈16, unless a reference says otherwise. A different track for every video.
- **SFX calibrated to this speaker's level, ~7 dB under the voice, and never on a word.**
- **Duck the bed under every riser, don't cut it**, unless a cut-out was asked for by name.

---

## 5. Build, render, evaluate, master

`build_index.py` applies the rules that are easiest to forget by hand: the one-frame boundary
rule, alternating track indices, caption classes from `slot_at()`, caption durations of
`next − this − 0.005`, brand tokens, grid-fitted cards and captions, a `tl.set` hard kill at
every animated element's out-point, and the outro when it is enabled. It refuses anything
that runs past the end, and asserts every beat start is a real segment boundary.

Things that will bite (with the rest in `references/hyperframes.md`):

- **A passing snapshot is not proof the encode is correct.** Always sample the encoded mp4.
- **Debug with a draft proof render** (`--quality draft --fps 5 --crf 34`).
- **HDR is the silent killer.** One HLG/bt2020 phone clip makes the whole master milky. Tone-map
  at build time, pin the master, and assert on the probe.
- **A hand-edit to `index.html` is lost on the next build.** Fix `beats.py` or `media.json`.

### Self-evaluation: before the user sees anything

On the **rendered** file, not the sources:

1. `timeline_view.py` at every cut boundary (±1.5 s): look for a jump, a flash, a pop, a
   caption hidden behind an overlay, or a breath after a cut.
2. `grid.py overlay` on 4-6 frames (hook, a card, a B-roll run, the outro) for the contact
   sheet.
3. `preflight_qa.py` on the final render: dead space, drift, caption rules, grid, fonts,
   bitrate, loudness, colour.
4. Re-transcribe the final mix and compare it word by word with the source. A changed word
   is an SFX collision or a clipped word end.
5. For anything publishable, spawn **one critic sub-agent** with the render, the transcript
   and any reference. Brief it to roast, not praise: a verdict, ranked problems with
   timecodes and evidence, and the five fixes to do first.

At most **3 fix-render passes**. If issues remain, report them plainly. You cannot listen to
audio: report the measured numbers, never "it sounds good".

Present contact sheets at every milestone. When notes come back with timecodes, **fix all of
them, then re-scan the whole video for the same class of mistake.** After every round, fold
new notes back into this skill as permanent rules. Where a note can be expressed as a check,
put it in `preflight_qa.py`.

---

## 6. Talking to the user

- **The user's language, short, plain.** No jargon. One question at a time when something is
  blocking. Many users of this skill are not technical: explain results, not mechanics.
- **In Hebrew (or any RTL language): every Latin term, path or command on its own line** or
  in a code block, never mid-sentence. Mixed-direction lines render scrambled in many
  terminals.
- Say what you measured, not what you hope ("המוזיקה 15 דציבל מתחת לקול", not "המוזיקה
  נשמעת טוב").
- List every transcript correction you inferred so the user can confirm it.

---

## References

| File | Covers |
|---|---|
| `references/workflow.md` | The session: inventory, conversation, strategy approval, self-eval, memory |
| `references/reference-analysis.md` | Analysing a reference and adapting the edit to it (+ `references/examples/`) |
| `references/grid.md` | The Instagram Reels grid, the `ads` profile and the gate |
| `references/fonts.md` | Free fonts only: registry, Hebrew pairings, the licence gate |
| `references/brand.md` | Brand colour roles from a logo, contrast rules, how to use the tokens |
| `references/outro.md` | The animated logo outro: the ask, three styles, sound, QA |
| `references/hebrew.md` | Hebrew transcription, captions, cutting and typography |
| `references/cutting.md` | Onset detection, island mapping, chunk-level tightening, frame quantisation |
| `references/layout.md` | Hook designs, matting, split-screen, per-segment crops, punch-ins |
| `references/captions.md` | Caption spec, the five slots, the beat map, RTL/bidi, plate geometry |
| `references/sound.md` | Music selection and alignment, levels against the voice, risers, SFX |
| `references/graphics.md` | B-roll sourcing, screenshot/terminal capture, motion-graphic cards, logos |
| `references/hyperframes.md` | Renderer gotchas, the caption-ghosting bug, HDR handling |
| `references/delivery.md` | Render settings, verification, loudness, file placement |
| `references/checklist.md` | Every mistake to check before every review |
| `reference-composition/index-reference.html` | An annotated composition with the reasoning inline |

---

Built by Ben Daskalo / HAPORTAL on Omer Yaron's `ai-video-editor` and Browser Use's
`video-use` (both MIT). See `NOTICE.md` and `LICENSE`.
