# Sound design

Sound is half of "premium." A technically clean edit with a boring bed still reads as amateur.

The method in one line: **a track generated for THIS story, its drop aligned to the turn
word, shaped and calibrated per section against the raw voice, effects on every designed
transition but never on a word, and a two-stage master to −14 LUFS.**

```
python3 $S/scripts/music.py --init --turn-word "<the phrase the story turns on>"
#   edit music_plan.json: rewrite the section styles for this story
python3 $S/scripts/music.py                 # 2 variants, analysed, the drop aligned → assets/bgm/music.mp3
python3 $S/scripts/bed.py --init            # bed.json from the plan (+ a drop before the turn)
#   add drops before punchlines, lower a target where the story peaks
python3 $S/scripts/bed.py --apply           # → assets/bgm/bed.wav, gaps per section, media.json audio.music
python3 $S/scripts/sfx.py library           # the named SFX set in assets/sfx/
python3 $S/scripts/sfx.py place cues.json --apply   # scaled to the voice, moved off words → audio.sfx
python3 $S/scripts/finish.py                # ... and the master: −14 ± 0.4 LUFS, TP ≈ −1.3
```

Every script reads `ELEVENLABS_API_KEY` from the project's `.env` or the environment. It
never prints it, and it checks the balance before it spends anything. **No key is not an
error.** See [Without an ElevenLabs key](#without-an-elevenlabs-key).

---

## Music

### Generated per video, never stock

`music.py` sends ElevenLabs Music (`POST /v1/music?output_format=mp3_44100_192`,
`model_id: music_v1`) a `composition_plan` whose sections follow the video's emotional arc.
A stock bed never turns where the speaker turns. A track built from the story's own
sections does: tension, then a hit, a lift, a peak, and a resolve.

`music_plan.json` (written by `--init`, then edited per video):

| key | meaning |
|---|---|
| `sections` | `[{name, end, styles, gap_db}]` in **video time**. Each starts where the previous one ends. `--init` maps hook → tension → turn → drive → peak → outro onto this video: the turn word, the end of speech, the outro. **Rewrite the styles for this story.** |
| `variants` | two contrasting global-style presets: `trap` (modern cinematic trap hybrid, 808, builds) and `score` (cinematic motivational score, felt piano). Also `tech` (dark, sleek electronic), or `{"name", "styles"}`. |
| `turn_word` / `turn_time` | where the story turns ("so I decided…", "stop waiting"). The drop goes here. |
| `lead` (0.5) | the first section is this much longer than the story section, so the drop is asked for slightly AFTER the turn and the trim (offset) comes out positive. |
| `tail` (2.0) | extra seconds on the last section. A drop may need up to ~3 s of offset, and without spare length the trimmed track runs out under the outro. |
| `gap_db` | per-section target for `bed.py` (null = the house value). |

Section styles that worked on a 55 s motivational piece: *"tense and restrained, ticking
like waiting, sparse pulse"* → *"tension builds, darker, ends with a short hit"* → *"hope
enters, warm piano and pads, gentle rise"* → *"driving and uplifting, steady momentum,
full groove"* → *"emotional peak, big and free, soaring"* → *"resolves into one final deep
hit and a ringing tail"*. Every preset carries *leaves space for a spoken voice* and
*starts immediately*. The negatives are *vocals, singing, lyrics, choir, rap, vocal chops,
cheesy, EDM drop, aggressive distortion, long silence, fade in from silence*.

- **Two variants, one after the other.** Parallel requests return HTTP 429. The model does
  not put a drop exactly on a section boundary, so a second variant is what makes landing
  it on the turn reliable. Cost is about 830 credits per generated minute: two 55 s
  variants measured 1,512. `--dry-run` prints the requests and the estimate.
- **Credits first.** The run stops before the first request when the balance can't cover
  the batch. ElevenLabs' balance can lag a few minutes behind a generation.

### Analyse, pick, align

For every candidate, `music.py` prints the RMS per 0.5 s (the structure at a glance) and
finds:

- **drops** at 20 ms resolution. A *rise* is the biggest energy step (the next 1.5 s
  against the previous 1.5 s). A *break* is a short stop (0.3-1 s, ≥ 5 dB under both
  sides) and the return after it, which is how a generated track often marks a section
  change. Each one is refined to its sharpest 60 ms jump, the actual hit.
- **intro silence.** The track is rejected if it runs over 2.5 s, and warned about if
  near-silence is left at frame 1 after the trim.
- **dead gaps.** The track is rejected if there is ≥ 1 s of silence inside the body.
- **natural ending**: the last 0.5 s window within 20 dB of the median.

Then it picks the variant whose drop can land on the turn and sets

```
offset = dropTime − (turnWordStart − 0.06)        # 0 ≤ offset ≤ 3 s
```

The track is trimmed by `offset` (in `bed.py`), so the hit lands 60 ms before the turn word:
the ear hears the hit, then the word. *Reference:* the drop at 24.04 s in the track and the
turn line at 23.58 s gave an offset of 0.52 s.

It reports where the natural ending lands against the outro start, which ideally is right
on it, and warns when the music would run out before the composition ends. A logo landing
in silence is a bug: regenerate with a longer `tail`.

`assets/bgm/music.mp3` is the chosen track, **untrimmed**. `assets/bgm/music_report.json`
holds the offset, the drop, the ending and every variant's analysis.

### Choosing and taste

- **Music from frame 1.** A silent hook is boring and it always gets flagged. That is why
  the plan says *starts immediately* and the analysis rejects silent intros.
- **A different track for every video.** A series that reuses one bed sounds like a template.
- **Energetic talking-head reels want rhythm.** A modern trap or groove bed with a real pulse
  beats a cinematic underscore that "fits" and puts the viewer to sleep.
- **Tone: light, confident, never boring.** Not dark, not scary. A generic documentary or
  marimba bed meets every technical rule and still gets rejected.
- **Licensing is the creator's call.** Never bundle or redistribute music with this skill,
  and never suggest ripping a commercial track. ElevenLabs output follows the user's plan
  terms: paid plans include commercial use.

---

## The bed: shaped per section, calibrated against the voice

`bed.py` turns the chosen track into **one file covering the whole composition** (0 → END,
the outro tail included), played at volume 1.0. Every level decision is baked into it and
measured, so no `data-volume` number has to mean something without the track it was tuned
on.

1. Trim the track by `offset`, cut it to END, and add a 12 ms fade-in.
2. Build a gain envelope from keyframes:
   - **Sections** `[name, start, end, target_gap_dB]` get keys at `start + 0.12` and
     `end − 0.12`. **Never put two keys on one timestamp.** Tied keys make `np.interp` ramp
     across the whole section instead of 0.24 s at the boundary, a real bug.
   - **Drops** before punchlines are `[a, b, ratio]`, a 60 ms ramp in to
     `sectionGain × ratio` (0.08-0.15), back at `b`. They are **relative to the containing
     section**: a fixed drop level once *raised* the music under a quiet voice. `--init`
     adds one right before the turn (`turn − 0.36 → turn − 0.10`, ×0.15), so the silence
     sells the hit.
   - **Swells** `[a, b, ratio]` lift to `× ratio` mid-window, for a graphic beat.
   - **Outro lift** (the voice is gone): last gain at O, ×2.0 at O+0.5, ×1.6 at O+2.2, ×0.6
     at END−0.5, 0 at END. O comes from `build/outro.json`, or else from the music report.
     With no outro the bed fades out over the last 0.3 s.
3. Carve an **EQ pocket** for the voice (−5 dB at 1.8 kHz, Q 1; −3 dB at 450 Hz, Q 1). Then
   apply a **sidechain compressor keyed by the voice** (voice −6 dB into the key, threshold
   0.03, ratio 3, attack 15 ms, release 260 ms).
4. **Calibrate**: per section, `gap = RMS_dB(raw voice) − RMS_dB(processed bed)`, then
   `gain *= 10^((gap − target)/20)`, capped at ×6.0, **6 iterations**. The sidechain is
   non-linear, so one pass is not enough. The calibration runs against the **raw** voice,
   so a quiet AI-avatar voice gets a quiet bed and the master lifts both together.

| section (reference) | voice above music |
|---|---|
| hook, setup, story sections | **14 dB** |
| the lift after the turn, the build, the peak | **13 dB** |
| range | 12-16 dB. Lower = louder music. Go lower where the story peaks. |

`bed.py` prints the target, the measured gap and the gain per section, plus the bed's peak.
It also prints the `media.json` entry:

```json
"music": [{"id": "bgm", "src": "assets/bgm/bed.wav", "start": 0, "duration": END,
           "volume": 1.0, "baked": true}]
```

`--apply` writes it. `"baked": true` means the outro lift and every level are already in
the file: play it at 1.0 and never put a second volume automation on it.

### The hook transition and risers

A **long riser** (~1.7 s) ending exactly on a cut, with a flare, a bloom and an impact on
the same frame, is still the device for a hard hook turn. Two rules:

- **Align a riser by its envelope, not its file length.** Most riser files carry trailing
  silence. Measure a 50 ms RMS envelope, find the last bucket above ~−20 dB relative to
  peak, and set `start = cut − that_offset`.
- **Duck the bed under a riser, don't cut it**: a `drops` entry over the riser's last
  0.30 s, ratio ~0.25. A **cut-out** (the bed stops on the landing frame, dead air under a
  reveal line, back on the next cut) is a different device. Use it only when asked for by
  name: a drop with ratio 0 over that window.

---

## SFX

### The library

`sfx.py library` ensures the named set exists in `assets/sfx/`. Every cue is a 48 kHz stereo
wav, lead-in silence trimmed, normalised to −6 dBFS peak, and ≤ 1.3 s long (`logo_sting`
≈ 3.5 s), so a volume number means the same thing for every cue:

`whoosh_impact, soft_whoosh, whoosh_low, swap_pop, pop, click, ding, glass_snap,
riser_short, typing, message, comment_ping, page_flip, portal_suck, logo_sting,
bars, snap, bricks, shatter, clock`

A missing cue comes from these sources, in order:
1. `--from DIR` (repeatable): files the user owns, matched by name (`bars.mp3`, `bars_2.wav`).
2. ElevenLabs sound-generation (`duration_seconds` 1.3, `prompt_influence` 0.6), with
   concrete, short prompts, when a key is set.
3. A synthesised fallback built from sines and noise. It is free and owned by nobody, but
   plainer.

One-shot cues (click, pop, ding…) keep only their first event, because the generator
often returns a series. `assets/sfx/library.json` records where every file came from.

**Custom cues per video:** `sfx.py gen "heavy steel prison cell bars slamming shut,
metallic clang, short" --name bars`. More reference prompts: *"thin strings snapping, quick
twang, short"*, *"light building blocks stacking click, short, satisfying"*, *"metal bars
breaking apart with a bright shimmer, short"*, *"single soft clock tick tock, short"*.

### Placement: an effect on every designed transition

Use one on every widget in, state flip, stamp, bars, strings, bricks, button tap, hook out
and back, and the outro (soft whoosh at O, `portal_suck` at O+1.12, `logo_sting` at
O+1.7). **Not on captions or headlines.** In a fast montage, a click on every cut is the
rhythm.

### Scale to THIS voice

```
k = clamp(10^((voiceMean_dB + 16.1) / 20), 0.03, 1)     # volumedetect mean of the RAW voice
volume = base × k                                       # base 0.10-0.45
```

The presets were tuned on a voice with a −16 dB mean. A quiet avatar voice (−40 dB mean)
gets small effect volumes, and the master lifts everything together.

### Never on a word

An effect over a short word swallows it ("סבתא" became "ספטו"; "הזדמנות" was masked).
`sfx.py place` applies these rules to each cue `{name, t, base_vol, exempt}`:

- If the cue falls inside a word and there is a pause **≥ 0.14 s before** the word, it
  starts at `speechOnset − 0.1`.
- Otherwise, if there is a pause **≥ 0.14 s after** the word, it starts at
  `speechEnd + 0.02`.
- Otherwise its **volume is halved**.
- **One move, never a chain of slides** across several words (that is how a cue drifted onto
  a key word), and never more than 0.6 s.
- **Exempt** cues (bars, shatter, stamps, hook whooshes, outro) stay on their beat. Keep
  them moderate (base ≤ 0.45).

The pauses are **measured on the audio**, not read from the word table. Whisper's word ends
touch the next word's start, so the table shows no gaps in connected speech. A 10 ms
energy mask of the raw voice gives the real pause edges. The word table only says which
word a cue hit. The report lists every move. A non-exempt cue whose attack still lands on
speech is flagged, and the script exits non-zero.

### Craft notes that still hold

- **The reveal cue is a LOW WHOOSH, not a camera click.** Pick by spectral tilt (`lowpass=f=250`
  against `highpass=f=2000` mean), fired 0.10 s before the beat so the body lands on it.
- **A tiered list gets a cue per tier, on every beat** (down / mid / up), normalised to a
  common peak.
- **A logo/brand pop fires on the WORD**, not on the card boundary.
- **Prove it:** re-transcribe the master (`transcribe.py renders/final.mp4 --words
  build/master_words.json`), then `sfx.py check build/master_words.json`. It prints the word
  match and names the cue sitting on every changed word. A halved cue can still swallow a
  short word: in testing, "וגרוק" was heard as "ודרוק" under a halved pop, and the
  voice-only master read correctly. Move that cue to the phrase boundary, or drop it.

---

## Mastering (`finish.py`)

Target: **−14 LUFS integrated, ±0.4; true peak ≈ −1.3 dBTP; AAC 320k.** Reels normalise to
about −14, so a quieter master just plays quieter than the next video.

The render's integrated loudness picks the chain:

| render | chain |
|---|---|
| **quiet / peaky** (≤ −25 LUFS; AI avatars often arrive near −36) | `volume=+20dB` (scaled down for less quiet input), then `acompressor=threshold=0.06:ratio=3:attack=4:release=140:makeup=1`; pre-limiter target starts at −12.8 |
| **normal** (≈ −20 LUFS) | `acompressor=threshold=0.08:ratio=2`, no pre-gain; pre-limiter target starts at −13.4 |

Then: `aresample=192000, volume=G, alimiter=limit=0.84:attack=2:release=60:level=false,
aresample=48000`. That is a 4× oversampled limiter at −1.5 dBFS with auto-level off,
because `level=true` puts the peak straight back.

`finish.py` **measures the encoded master** and moves the pre-limiter target until it lands
within the tolerance, usually in 2 passes, then muxes the measured AAC into the video
untouched. Without the pre-gain and compression the limiter clamps the voice peaks and the
master sticks near −16 LUFS whatever the gain. `finish.py --audio-only mix.wav` masters a
mix on its own, to test without a render.

---

## Without an ElevenLabs key

The skill works without one. Tell the user plainly which path you took:

| need | without a key |
|---|---|
| music | `music.py --file track.mp3`: a track **the user is licensed to use**, with the same analysis, drop alignment, offset and report. Or `music.py --procedural`: a quiet royalty-free synth bed (pad, sub pulse, a hit on the turn, a ringing tail). It is a floor, not a score. Or **no music**: skip `bed.py`, and the reel carries voice and SFX only. |
| bed | identical: `bed.py` works on whatever `music.mp3` is |
| SFX library | `sfx.py library --from DIR` for files the user owns. Anything still missing is synthesised (it says so). |
| custom cue | `sfx.py gen` synthesises a stand-in and says so. For a real one, import a file the user owns. |
| master | identical |

---

## Final audio check

- Master: **−14 ± 0.4 LUFS, true peak ≈ −1.3 dBTP** (`ebur128=peak=true`).
- Per-section bed gaps within 0.5 dB of target (`bed.py` prints them).
- Every non-exempt SFX off speech, or halved by rule (`sfx.py place` report).
- Re-transcription of the master ≥ 97 % word match with `src/words.json`.
- No clipping inherited from the raw. See `references/cutting.md` §7.
- You cannot listen. Report the measured numbers, never "it sounds good".
