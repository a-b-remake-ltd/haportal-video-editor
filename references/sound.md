# Sound design

Sound is half of "premium." A technically clean edit with a boring bed still reads as amateur.

The method in one line: **a track generated for THIS story, its drop aligned to the turn
word, shaped and calibrated per section against the raw voice, effects on every designed
transition but never on a word, and a two-stage master to −14 LUFS.**

```
python3 $S/scripts/music.py --init --turn-word "<the phrase the story turns on>"
#   edit music_plan.json: rewrite the section styles for this story
python3 $S/scripts/music.py                 # 2 variants, analysed, the drop aligned → assets/bgm/music.mp3
#   exit 3 = TURN GATE failed: regenerate ONCE with --force --stronger-turn, never loop
python3 $S/scripts/bed.py --init            # bed.json from the plan (+ a drop before the turn)
#   add drops before punchlines, lower a target where the story peaks
python3 $S/scripts/bed.py --apply           # → assets/bgm/bed.wav, gaps per section, media.json audio.music
python3 $S/scripts/sfx.py library           # the named SFX set; with a key it UPGRADES synth stand-ins
python3 $S/scripts/build_index.py           # rebuild AFTER the library: clip durations come from the files
python3 $S/scripts/sfx.py place cues.json --apply   # scaled to the voice, moved off words → audio.sfx
python3 $S/scripts/finish.py                # ... and the master: −14 ± 0.4 LUFS, TP ≈ −1.3
python3 $S/scripts/music.py --credits       # minutes later: what the generations really cost
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
| `turn_word` / `turn_time` | where the story turns ("so I decided…", "stop waiting"). The drop goes here. A section must START on it. |
| `turn_structure` (true) | adds the concrete build → stop → drop instructions around the turn (see [Prompting the turn](#prompting-the-turn)). Keep it on. |
| `turn_strength` (1) | 2 = the emphatic version, what `--stronger-turn` sends for the one retry after a weak turn. |
| `lead` (0.5) | the first section is this much longer than the story section, so the drop is asked for slightly AFTER the turn and the trim (offset) comes out positive. |
| `tail` (3.5, the minimum) | a separate **Ring out** section requested after END. After a late drop's trim the track still sounds ≥ 2 s past the last frame. |
| `gap_db` | per-section target for `bed.py` (null = the house value). |

Section styles that worked on a 55 s motivational piece: *"tense and restrained, ticking
like waiting, sparse pulse"* → *"tension builds, darker"* → *"hope and confidence, warm
piano chords over the full beat"* → *"driving and uplifting, steady momentum, full
groove"* → *"emotional peak, big and free, soaring"* → *"one final deep hit, then the
last chord rings"*. Every preset carries *leaves space for a spoken voice* and *starts
immediately*. The negatives are *vocals, singing, lyrics, choir, rap, vocal chops, cheesy,
festival EDM, dubstep wobble, aggressive distortion, long silence, fade in from silence*.
**Never "EDM drop"**: as a global negative it vetoes the one drop the plan needs, and
`music.py` strips it from older plans.

### Prompting the turn

Moods give the model nothing to place. A from-zero test asked for *"THE DROP: the full
beat enters right at the start of this section"* and got its only real lift 16 s early.
What works is **structure in concrete musical terms, in the sections on both sides of the
turn**. `composition()` adds it to whatever styles the editor wrote:

| section | added `positive_local_styles` | added negatives |
|---|---|---|
| the one BEFORE the turn, named "… - filtered build, ends on a stop" | *sparse and filtered: low-pass filtered drums, no kick drum, no sub bass* · *builds tension towards the end of this section* · *ends with a sudden stop: a hard cut to near silence in its last half second* | full drums, kick drum, drop, climax (and **not** "silence", which would veto the stop) |
| the TURN section, named "… - DROP on the first beat" | first: *begins with a sudden full drop on the very first beat: kick drum, 808 sub bass and the full beat enter all at once* · *loud and full from its first second* | gradual build, slow fade in, soft intro, riser, quiet start |
| the last story section | *the music keeps sounding to the very end of this section, no early fade-out* | |
| **Ring out** (≥ 3.5 s, after END) | *the final chord keeps ringing: sustained, a slow natural decay, no new elements* | abrupt ending, sudden silence |

Write the editor's own styles the same way: instruments, filters, what enters and what
stops (*"claps and sub bass enter"*, *"only a filtered pad"*), never just a feeling.
`--stronger-turn` (the one retry) makes the build *"only a filtered pad and a soft
ticking pulse, no drums at all"* ending in *"half a second of complete silence"*, and the
drop *"the hardest-hitting, loudest moment of the whole track"*.

**The model places a section change only roughly.** Measured on one round: the score
variant put its drive change 0.2 s from the boundary, the trap variant built the exact
build → stop → +15.6 dB drop that was asked for, 5.5 s late. That is why there are two
variants, and why a generated track may be trimmed by up to 8 s (below).

- **Two variants, one after the other.** Parallel requests return HTTP 429. The model does
  not put a drop exactly on a section boundary, so a second variant is what makes landing
  it on the turn reliable. Cost is about **820 credits per generated minute**: two 55 s
  variants measured 1,512, two 64 s variants 1,770. `--dry-run` prints the requests, what
  the credit check will require (at a safe 1,000/min) and the expected spend.
- **Credits first, the real cost after.** The run stops before the first request when the
  balance can't cover the batch. Afterwards it prints `credits spent: ≈N (estimate from
  the generated length)`. **The balance endpoint lags minutes behind a generation**: read
  straight away it showed no change at all ("credits used: 0" on every run while ~1,700
  went per round), so the measured change is printed only once it is visible, otherwise
  it says the balance lags. Every run is logged in `build/credits_ledger.json`;
  `music.py --credits` (or `audiokit.py credits`) reads the balance later and prints the
  measured spend against the estimates. Report both numbers to the user.

### Analyse, pick, align

For every candidate, `music.py` prints the RMS per 0.5 s (the structure at a glance) and
finds:

- **candidate changes** at 20 ms resolution. A *rise* is a big energy step (the next 1.5 s
  against the previous 1.5 s). A *break* is a short stop (0.3-1 s, ≥ 5 dB under both
  sides) and the return after it, which is how a generated track often marks a section
  change. Each one is refined to its sharpest 60 ms jump in [t − 0.5, t + 1.0] s, the
  actual hit (under a riser the 1.5 s window peaks before the hit).
- **a score for every candidate**, because the biggest local rise is not the drop. In the
  from-zero test the old detector called a +4.5 dB rise in the middle of a plateau "the
  drop" while the real +13.8 dB lift sat 16 s earlier:

  ```
  lift_db  = level of the 2 s after − the 2 s before          magnitude
  pre_db   = the 2 s before, against the body's level          contrast: a real drop follows
                                                               a quieter / sparser stretch
  jump_db  = the 60 ms step at the hit                         suddenness
  strength = lift + 0.5 × min(12, max(0, −pre)) + 0.2 × min(15, jump)
  ```

  `strongest` is the best change in the whole track, wherever it sits.
- **intro silence.** The track is rejected if it runs over 2.5 s, and warned about if
  near-silence is left at frame 1 after the trim.
- **dead gaps.** The track is rejected if there is ≥ 1 s of silence inside the body.
- **natural ending**: the last 0.5 s window within 20 dB of the median.

Then it picks the variant whose strongest change can land on the turn (score = strength,
minus 2 per second away from where the plan asked for it beyond 1 s, minus 1.5 per second
the bed would have to extend the ending) and sets

```
offset = dropTime − (turnWordStart − 0.06)        # 0 ≤ offset ≤ 8 s (generated), any (--file)
```

The track is trimmed by `offset` (in `bed.py`), so the hit lands 60 ms before the turn word:
the ear hears the hit, then the word. *Reference:* the drop at 24.04 s in the track and the
turn line at 23.58 s gave an offset of 0.52 s.

**The turn gate.** It prints how much the change chosen for the turn lifts, and the
strongest change of every variant with where it would land:

```
turn: the change chosen for the turn lifts +3.2 dB (at 24.68s in 'trap', lands 23.44s)
strongest change in 'trap': +14.0 dB at 9.04s in the track — would land at 7.80s in the video
✗ WEAK TURN: the change on the turn is only +3.2 dB (gate ≥ 6 dB)
```

Below **6 dB** the run exits 3 with a loud block and the one command to try:
`music.py --force --stronger-turn` (≈ the cost of one round, printed). **Never loop.** If
the stronger round is weak too, keep the best result, let `bed.py`'s drop before the turn
sell the hit, and tell the user. `music.mp3` is written either way. In the same test the
new plan took the turn from +3.2 dB to **+15.6 dB** in one round.

It reports where the natural ending lands against the outro start, which ideally is right
on it. When the music would audibly end before the last frame it says so: `bed.py` extends
it (below), and a smaller offset or a longer ring-out avoids it.

`assets/bgm/music.mp3` is the chosen track, **untrimmed**. `assets/bgm/music_report.json`
holds the offset, the drop, the turn gate, the ending and every variant's analysis.
`music.py selftest` runs the negative tests (the plateau case above, the plan structure,
the spend report) with no key.

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

1. Trim the track by `offset`, cut it to END, and add a 12 ms fade-in. **Never silence
   at the end**: when the track's audible end (last 50 ms frame within 20 dB of its median)
   is more than 0.25 s before END, it is extended with a natural tail and the report says
   so (`build/bed_report.json` → `tail`):
   - **loop** (the default): the stretch before the track's ending (its final hit / ring,
     where the level first falls 6 dB under the median) is lengthened by repeating its
     last **bar** (4 beats, from the autocorrelation of the onset envelope; 2 s with no
     clear pulse) with equal-power crossfades. Then the track's OWN ending plays, landing
     just after END, so the final hit is kept and the outro lift has music to lift.
   - **reverb**: the fallback with no steady bar: a synthetic hall wash of the last 1.5 s,
     6 dB under the median, RT60 = max(4 s, 3 × the span).
   - `"extend_tail": false` in bed.json pads silence instead, flagged ✗.
   After the calibration a **gate** measures the bed over [END − 1, END − 0.5]: more than
   30 dB under the body is `✗ SILENT before the last frame` and exit 1. A from-zero test
   shipped a track that ran out 1.1 s early; the logo landed in silence.
   `bed.py selftest` covers both modes, a long-enough track and the off switch.
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

`sfx.py library` ensures the named set in the project's `assets/sfx/`, and **with a key it
upgrades synthesised stand-ins** to generated cues. Every cue is a 48 kHz stereo wav,
lead-in silence trimmed, normalised to −6 dBFS peak, and ≤ 1.3 s long (`logo_sting`
≈ 3.5 s), so a volume number means the same thing for every cue:

`whoosh_impact, soft_whoosh, whoosh_low, swap_pop, pop, click, ding, glass_snap,
riser_short, typing, message, comment_ping, page_flip, portal_suck, logo_sting,
bars, snap, bricks, shatter, clock`

A cue comes from these sources, in order:
1. `--from DIR` (repeatable): files the user owns, matched by name (`bars.mp3`, `bars_2.wav`).
2. ElevenLabs sound-generation (`duration_seconds` 1.3, `prompt_influence` 0.6), with
   concrete, short prompts, when a key is set. Credits are checked first (at a safe
   40/s); the spend is reported like the music's: the estimate (measured ≈ 11 credits per
   second: 9 cues × 1.3 s cost 126), then the measured change once the balance shows it.
3. A synthesised fallback built from sines and noise. It is free and owned by nobody, but
   plainer.

One-shot cues (click, pop, ding…) keep only their first event, because the generator
often returns a series.

**Provenance and the upgrade.** `doctor.py --install` synthesises the set at install time,
when there is no key yet, and a project copies those files in. `library` used to fill only
MISSING cues, so a user who added a key later kept the stand-ins forever. Now
`assets/sfx/library.json` records every cue's kind (**synth / elevenlabs / user**), prompt
and sha256, and:

- a copied file with no record is recognised by its hash against the skill's own set and
  inherits its kind; a file nobody recorded, or one replaced by hand, is **unknown** and is
  never overwritten;
- with a key, `sfx.py library` regenerates every **synth** cue of the named set (and
  `--from DIR` replaces stand-ins with the user's files); `--keep-synth` fills only missing
  cues; `--force` regenerates everything;
- `sfx.py status` prints where every cue came from; `doctor.py` says *"N named cues are
  synthesised stand-ins — run sfx.py library with a key to upgrade"* for the skill's set
  and, run from a project folder, for the project's.

**Pipeline order.** The build reads cues from the project's `assets/sfx/` first (the skill's
set is only copied in when one is missing), and it measures each clip's duration from the
file. So run `sfx.py library` **before** `build_index.py`; after an upgrade, rebuild.
`library` lists the cues `index.html` plays with the old durations.

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
  match and names the cue sitting on every changed word. The cues come from **`index.html`'s
  sfx audio clips** (what was rendered, including every scene-path cue that never went
  through `sfx.py place`), else `build/sfx_placed.json`, else `build/scenes.json`;
  `--placed FILE` picks one. With no list at all it says so and checks the words alone. A halved cue can still swallow a
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

Then: `aresample=192000, volume=G, alimiter=limit=<ceiling>:attack=2:release=60:level=false,
aresample=48000`, a 4× oversampled limiter with auto-level off (`level=true` puts the peak
straight back). The ceiling starts 0.2 dB under `render.target_peak_db` (house −1.5, never
above −1.0) and is lowered by the measured overshoot + 0.1 dB until the TRUE peak of the
ENCODED AAC is under the target — AAC encoding adds overs a sample-peak limiter cannot see.
`finish.py --check renders/final.mp4` re-measures a master on its own and fails it if either
target is missed.

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
| SFX library | `sfx.py library --from DIR` for files the user owns. Anything still missing is synthesised (it says so) and recorded as `synth`, so adding a key later and re-running `sfx.py library` upgrades it. |
| custom cue | `sfx.py gen` synthesises a stand-in and says so. For a real one, import a file the user owns. |
| master | identical |

---

## Final audio check

- Master: **−14 ± 0.4 LUFS, true peak ≈ −1.3 dBTP** (`ebur128=peak=true`).
- Turn gate: the change landing on the turn lifts **≥ 6 dB** (`music.py`), or the user was
  told it is weak after the one allowed retry.
- Per-section bed gaps within 0.5 dB of target (`bed.py` prints them), and
  `✓ music to the end` (any tail extension reported).
- Credits: the estimate and, minutes later, the measured spend (`music.py --credits`).
- SFX provenance: `sfx.py status` — with a key, no named cue left as a synth stand-in.
- Every non-exempt SFX off speech, or halved by rule (`sfx.py place` report).
- Re-transcription of the master ≥ 97 % word match with `src/words.json`.
- No clipping inherited from the raw. See `references/cutting.md` §7.
- You cannot listen. Report the measured numbers, never "it sounds good".
