---
name: haportal-video-editor
description: HAPORTAL - VIDEO EDITOR. Turn a raw vertical talking-head take (a real recording or an AI avatar) into a finished premium 9:16 reel for Instagram Reels, TikTok and YouTube Shorts, rendered with HyperFrames. Hebrew-first, any language. Kinetic word-by-word headlines, literal UI "designed moments" invented from each line, a hook where the frame flies into a designed world, hard-swap captions, punch-ins, generated story-driven music, tight sound design, the Reels grid, free fonts only, brand colours from a logo, a logo outro, and a full QA loop on the rendered file. Use for "ערוך לי סרטון", "תערוך את הרילס", "תעשה מזה רילס", "edit my video into a reel". Not for long-form, horizontal or podcast-episode edits, or a plain trim of one clip.
---

# HAPORTAL - VIDEO EDITOR

Turn a raw vertical talking-head take into a finished 1080x1920 reel at the level of a premium
agency edit: every line illustrated by a concrete, witty UI moment, the key phrases building
word by word, a hook that flies into a designed world, captions that swap on the beat, a score
that turns where the story turns, and the brand landing in the outro.

**This is the default, for every user.** Most people who run this skill send one raw video and
nothing else: no reference, no notes, no corrections. They must get the full result anyway. Do
not wait for them to ask for headlines, moments, music or an outro. A reference, a logo or notes
only *adjust* the result.

Built on **HyperFrames** (HTML/CSS/GSAP, one paused timeline, generated, never hand-edited).
`$S` = this skill's folder. Commands run from the **project** folder.

---

## Read this first

1. **Premium restraint.** One idea on screen at a time. Generous negative space. Never busy.
2. **Literal, concrete, witty.** Every designed moment illustrates the exact sentence being said
   with a UI the viewer recognises in under a second (an inbox, a calendar, a task card, an
   approval dialog, a progress bar, a waiting room, a chat). A light comic twist where it fits.
   Never abstract glow, never cringe.
3. **Derive, don't copy.** Examples in these files are examples. For every video, invent the
   moments from *its* lines. Keep the language (rhythm, type, motion, sound, layout).
4. **Callbacks.** Plant a visual early and pay it off later. One or two per video.
5. **Something changes every 2-4 s.** Nothing static longer than 0.6 s (a QA gate checks it).
6. **Sync to words.** Every beat lands within 0.1 s of its word, from word-level timestamps.
7. **Brand at the end.** The body is the message; the brand lands in the outro.
8. **Measure, don't eyeball. Prefer a gate over a rule. Never show anything you have not
   evaluated on the rendered file.**

Re-read this file at the start of every round.

---

## The session

1. **Inventory.** ffprobe the raw (keep its native fps: 25 for most avatar renders, 30 for phone
   footage). If `project.md` exists, summarise the last session in one sentence.
2. **Ask once, briefly, in the user's language**, and continue with defaults if they say
   nothing. The questions are: a reference video? A logo? The outro (only with a logo)? Organic,
   or a paid ad too (`grid.profile: ads`)?
3. **Transcribe, cross-check, correct.** Two engines. Captions show the intended, correctly
   spelled words; timings come from the audio. List every correction for the final report.
4. **Framing map.** Measure the head, face, chin, chest and free zones before designing anything.
5. **Write the storyboard** (`storyboard.md`, method in `references/storyboard.md`). It is a
   table, line by line: time, words, on screen, captions on/off, punch, SFX. Then build it.
6. **Build, render, master, QA** (below). Fix and re-render until clean. At most 3 passes, then
   report what remains.
7. **Report** in the user's language: where the file is, what happens on screen line by line,
   the music choice, the QA numbers, and every correction or deviation.
8. **Persist** the session in `project.md`.

---

## Density and structure (the defaults, ~45-60 s monologue)

- **Hook (0 to ~6 s):** the first words build as a thin headline on the chest, then the frame
  flies into a designed world. 2-3 literal cards illustrate the first sentences (~1.6-1.8 s
  each), each with a big gradient title. The frame returns through a blue tint. The speaker is
  off screen for at most ~5 s.
- **Body:** 8-12 designed moments (sky widgets in the free zone, or full-frame overlays) plus
  5-7 kinetic headlines on the punchiest phrases. Plain captions fill the rest.
- **Callbacks:** at least one, paid off near the end.
- **Punch-ins:** a snap between 1.0 and 1.06-1.14 on phrase boundaries every 2-4 s, bigger on
  key words. Never inside the hook.
- **Ending:** the payoff on the final line, then the logo outro.
- **Captions:** 1-3 words, hard swap, white 62 px weight 500 with a soft shadow, on a
  high-contrast band of the chest. Hidden during the hook world, every headline and the outro.
  No dashes, no emoji in on-screen text.

`preflight_qa.py --checklist` warns when a density target is missed.

---

## The capabilities

| Capability | Reference | Tool |
|---|---|---|
| The storyboard method and the widget catalogue | `references/storyboard.md` | (you) |
| Widget kit, hook world, overlays, per-video scenes | `references/kit.md` | `kit.py`, `scenes.py` |
| Kinetic headlines | `references/kinetic.md` | `kinetic.py` |
| Ready-made moments (paper page turn, stamp, chips, fly, punch…) | `references/moments.md` | `moments.py` |
| Instagram Reels grid and its gate | `references/grid.md` | `grid.py` |
| Free fonts only | `references/fonts.md` | `fonts.py` |
| Brand colours, logo mark and parts from a logo | `references/brand.md` | `brand_from_logo.py` |
| Logo outro (`gate`: the frame becomes part of the logo; `portal`, `line`, `impact`) | `references/outro.md` | `outro.py` |
| Adapting to a reference video | `references/reference-analysis.md` | `analyze_reference.py`, `apply_style.py` |
| Generated music, the calibrated bed, the SFX library and placement, mastering | `references/sound.md` | `music.py`, `bed.py`, `sfx.py`, `finish.py` |
| The QA loop and the final checklist | `references/qa.md` | `preflight_qa.py`, `qa_frames.py` |
| Hebrew transcription, captions and cutting | `references/hebrew.md`, `references/captions.md` | `transcribe.py`, `xcheck.py`, `captions.py` |
| Cutting a real recording | `references/cutting.md` | `cut_aroll.py` |

**Precedence:** the user's explicit request > brand (logo, fonts) > reference > house defaults.
The grid always wins. Without a logo, the house palette (electric blue on deep navy, glass
widgets) is the default.

---

## Setup

```bash
python3 $S/scripts/doctor.py --install     # once per machine: deps, free fonts, SFX set, GSAP
cp $S/config.example.json config.json      # once per project
mkdir -p scripts && cp $S/scripts/beats.py scripts/beats.py
```

Music is generated with ElevenLabs when the project's `.env` holds `ELEVENLABS_API_KEY`
(credits are checked first). Without a key: the user's own licensed track (`music.py --file`),
a quiet procedural bed (`--procedural`), or no music. Say which, plainly.

---

## The pipeline, in order

```bash
# 1. the A-roll
python3 $S/scripts/cut_aroll.py --src raw.mp4 --whole          # an avatar or clean single take
#   (a real recording with retakes: --plan, write chunks.json, then --chunks chunks.json)

# 2. words: two engines, diff, corrections → src/raw_words.json, then onto the A-roll timeline
python3 $S/scripts/xcheck.py raw.mp4                           # writes src/transcript_diff.md
#   fill src/corrections.json (intended spelling), then: xcheck.py raw.mp4 --apply-only
python3 $S/scripts/map_words.py --raw src/raw_words.json       # → src/words.json

# 3. framing + style
python3 $S/scripts/framing_map.py assets/aroll.mp4 --apply     # LOOK at build/framing.png
python3 $S/scripts/brand_from_logo.py logo.png --out brand/    # if there is a logo
python3 $S/scripts/analyze_reference.py ref.mp4 --out style/   # if there is a reference

# 4. the storyboard → storyboard.md, then the build files:
#    scenes.py (the hook world + every designed moment, on the kit)
#    media.json (headlines, any ready-made moments, "outro": {...})
python3 $S/scripts/plan_punches.py --key "word,word" --apply

# 5. captions, build, captions again (hide windows), the caption layer
python3 $S/scripts/captions.py
python3 $S/scripts/build_index.py
python3 $S/scripts/captions.py
python3 $S/scripts/caption_layer.py

# 6. sound
python3 $S/scripts/music.py --init --turn-word "<the turn phrase>"   # then edit music_plan.json
python3 $S/scripts/music.py                                    # 2 variants, pick, align the drop
python3 $S/scripts/bed.py --init && python3 $S/scripts/bed.py --apply
python3 $S/scripts/build_index.py                              # rebuild with the bed

# 7. gates, render, master, QA
python3 $S/scripts/validate.py --expect build/expected.json
python3 $S/scripts/grid.py check index.html
npx hyperframes check
HF_VIDEO_COVERAGE_THRESHOLD=0 npx hyperframes render --quality high --video-bitrate 32M --output renders/render.mp4
python3 $S/scripts/finish.py renders/render.mp4 --out renders/final.mp4
python3 $S/scripts/preflight_qa.py . --aroll assets/aroll.mp4 --transcript src/words.json \
        --render renders/final.mp4 --checklist --lint
python3 $S/scripts/qa_frames.py renders/final.mp4              # LOOK at every sheet
```

---

## Non-negotiables

**Layout** (`references/grid.md`, `references/layout.md`)
- Everything readable inside x 60-940, y 220-1520, centred on x 500. Sky widgets in y 230-600.
  Headlines right-aligned at right 160 on the chest. Bottom cards anchored to y 1520.
- Text sits on high-contrast areas, taken from the framing map.
- Any camera rotation or sway is paired with a scale of at least 1.07.

**Captions** (`references/captions.md`, `references/hebrew.md`)
- Every word, with the intended spelling. 1-3 words, hard swap, never two lines, never ending on
  a sticky word. "AI" set apart so it never reads "Al".
- Hidden under the hook world, every headline and the outro. No caption starts inside a hidden
  window (a gate).

**Motion** (`references/kit.md`)
- Seek-safe: `fromTo` with `immediateRender:false`, hidden states from CSS, no runtime
  randomness, no tweening left/top, no nth-child selectors, no overlapping tweens on one
  property. Fewer than ~40 heavy (blur / radial-gradient / clip-path) elements.
- Widgets enter with drop or pop, live 1-4 s, leave with away. State changes swap stacked
  states. Spinners never stand still. Every world and card drifts slowly.

**Sound** (`references/sound.md`)
- Music generated per video, two variants, the drop on the turn word, sections calibrated
  against the raw voice (12-16 dB under it), drops before punchlines relative to their section,
  an outro lift.
- An effect on every designed transition, scaled to the voice, never on a word (except marked
  impacts). Never on captions or headlines.
- Master at −14 LUFS ±0.4, true peak ≈ −1.3 dBFS.

**Fonts and brand:** free fonts only. Colours only through the brand tokens.

---

## QA (`references/qa.md`)

Before showing anything: the snapshots and contact sheets looked at, frame by frame; loudness;
no frozen frames (`freezedetect` 0.6 s), no black; intelligibility of the master ≥97% against
the words; every transition frame looked at; the final checklist all ✓. If a check fails, fix
the cause and re-render.

---

## Talking to the user

- The user's language, short and plain. Many users are not technical: explain results, not
  mechanics.
- In Hebrew (or any RTL language), every Latin term, path or command goes on its own line.
- Say what you measured ("the music sits 14 dB under the voice"), never "it sounds good". You
  cannot listen; report numbers and suggest one listen.

---

## Credits

Built by Ben Daskalo / HAPORTAL on Omer Yaron's `ai-video-editor` and Browser Use's `video-use`
(both MIT). See `NOTICE.md` and `LICENSE`.
