# QA: the loop on real pixels and the real audio

Nothing goes to the user until this loop has run on the **master** and the final checklist
below reads ✓ on every item it can measure. A passing snapshot of the composition is not
proof: the encode, the caption layer, the punch-ins and the mix only exist in the master.

The method behind these numbers is [the house spec](house-spec.md) (§10 the QA loop, §12 the
final checklist). Every number here is a gate in `scripts/preflight_qa.py` where it can be one. When a note
repeats, add a check there; run the negative test when you add one.

---

## Before designing: measure

| Step | Command | Gate |
|---|---|---|
| Two-model transcription | `python3 $S/scripts/xcheck.py raw.mp4` | every disagreement decided (`--strict`); every correction key matched |
| Framing map | `python3 $S/scripts/framing_map.py assets/aroll.mp4 --apply` | LOOK at `build/framing.png` and `build/framing_marked.png`; the numbers match the frames |

**xcheck.py** runs the Hebrew fine-tune (ivrit-ai turbo) and Whisper large-v3-turbo on mlx
(glossary as the initial prompt; faster-whisper large-v3 where mlx is missing), and writes
`src/transcript_diff.md`: every place they disagree, with context and both readings. Decide
each by meaning. Fill `src/corrections.json`:

```json
{"12.34": "intended word",
 "misheard phrase": {"to": "intended phrase", "why": "the avatar mispronounced the script"},
 "_keep": ["d03", "d05"]}
```

Captions show the INTENDED, correctly spelled words (an avatar's mispronunciation, a
colloquial construct written in its standard form); timings stay the audio's. Re-run with
`--apply-only --strict`. Every applied change is listed at the end of the diff file: put
each one in the report. Two engines agreeing does not make a word right: read the whole text
once for words that make no sense in context.

**framing_map.py** writes head top, face centre, chin, chest, hands and free zones in real
px (`build/framing.json`). These decide placement: headlines and captions on the chest
(white on a dark shirt), widgets in a free zone (dark glass on a bright sky), nothing on the
face. If the default caption band sits on the chin or straddles the collar, `--apply` moves
it onto the chest and records why. Say so in the report.

---

## The loop

1. **Build, then the composition gates** (fast, no render):
   ```bash
   python3 $S/scripts/captions.py            # after build_index.py wrote build/caption_hide.json
   python3 $S/scripts/preflight_qa.py . --transcript src/words.json --lint
   ```
   Captions (1-3 words, no accidental gap, none starting inside a hidden window), dashes and
   emoji in every on-screen string, CSS class collisions, heavy overlays (< 40 elements with
   filter blur / radial gradient / clip-path, hidden ones included), missing local assets,
   rotation only on a scale ≥ 1.07, the grid, fonts, lint 0 errors.

2. **Snapshots before rendering:** `npx hyperframes snapshot --at <every designed moment,
   mid-animation and settled, plus every outro step> --no-end`. Fix, rebuild, re-snapshot
   until clean (what to look for: §snapshots).

3. **Render, then master** (`references/delivery.md`, `scripts/finish.py`).

4. **The render gates:**
   ```bash
   python3 $S/scripts/preflight_qa.py . --aroll assets/aroll.mp4 --transcript src/words.json \
           --render renders/final.mp4 --lint --checklist
   ```
   - **Loudness** −14 ± 0.4 LUFS, true peak ≤ −1.0 dBTP (target ≈ −1.3).
   - **Frozen frames:** `freezedetect=n=0.002:d=0.6` reports nothing. If it does, add drift
     or an earlier entrance there, or a punch-in step. Nothing static for > 0.6 s.
   - **Black frames:** `blackdetect=d=0.2:pix_th=0.05` reports nothing. Black usually means a
     heavy-overlay overload or a missing image.
   - **Intelligibility:** the master's speech part is re-transcribed with the same engine and
     glossary and compared word by word, ≥ 97 %. It is compared with `src/words.json` AND
     with the clean A-roll transcribed the same way; the second number is gated, because
     words.json carries corrected spellings the ear does not hear. Every differing word is
     listed with the SFX / music events overlapping it: move the SFX off the word or deepen
     the duck, then re-render. Reasonable final: 99 %.

5. **Transition frames from the master:**
   ```bash
   python3 $S/scripts/qa_frames.py renders/final.mp4     # → build/qa/sheet_*.png
   ```
   Stills just after every element enters, when it has settled, before it leaves, on every
   punch-in step and through the outro, each with the grid drawn on. LOOK at every one.

6. **Fix and repeat.** At most three fix-render passes; report anything left plainly.

### Snapshots: what to look for

- Text clipped by the frame edge, especially during punch-ins (scale shrinks the margins).
- A headline behind the speaker's head, or over the face.
- Words visible before they are spoken (a word span missing CSS `opacity:0`).
- Missing spaces between word spans.
- Widgets overlapping each other or the captions.
- Anything inside the red zones (hidden by the app UI).
- Black corners during a camera rotation.
- Grey-looking glass (too light over a bright sky; use opacity .84).
- Overlapping tagline words in the outro (a class-name collision).
- A caption re-appearing after a headline with words the headline already showed.

---

## The final checklist

`preflight_qa.py --checklist` prints it with ✓ / ✗ / ? decided from the measurements. "?"
means only a human look decides it: look, then decide. Do not report done while any item is
✗ without an explanation in the report.

- [ ] Two-model transcription diffed; caption text corrected to the intended script; changes listed.
- [ ] Framing map written; every element placed inside the grid's safe zone; captions on a high-contrast band.
- [ ] Hook: words, frame flies away, 2-3 literal cards, return through the tint, speaker away ≤ 5 s.
- [ ] 8-12 literal designed moments, 5-7 word-by-word headlines, at least 1 callback, nothing static > 0.6 s.
- [ ] Captions 1-3 words, hard swaps, hidden under headlines, hook and outro; no dashes; no emoji.
- [ ] Punch-ins on phrase boundaries (`plan_punches.py`); rotation only with scale ≥ 1.07.
- [ ] Music generated, 2 variants compared, drop aligned to the turn by offset, sections calibrated, relative drops, outro lift.
- [ ] SFX on every transition, scaled to the voice, never on words (except marked impacts).
- [ ] Outro geometry recomputed for this framing; logo inside the grid; no class collisions.
- [ ] Lint 0 errors; no heavy-overlay overload; all assets present.
- [ ] Snapshots reviewed and clean; master at −14 LUFS; no freezes; no black; transcript match ≥ 97 %.
- [ ] Report: where the file is, what happens on screen line by line, the music choice and why,
      the QA numbers, and every place you corrected the speaker's words or deviated from a rule.

The density items (hook, moment and headline counts, callback) are warnings: the story
decides in the end, but a reel below them reads as static. Say which you chose not to meet
and why.
