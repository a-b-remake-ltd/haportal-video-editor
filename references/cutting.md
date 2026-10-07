# Cutting the A-roll

The cut is the quality gate. Everything downstream inherits its timing, so a millisecond of
drift here becomes a visible mistake forty seconds later.

---

## 1. Find the takes

Transcribe the raw once with word timestamps:

```bash
uvx --from mlx-whisper mlx_whisper raw.wav \
  --model mlx-community/whisper-large-v3-turbo \
  --language <lang> --word-timestamps True --output-format json
```

**The last take of a repeated line wins.** People re-record until they like it; the keeper is
almost always the final attempt.

**Traps in this step:**

- **Back-to-back repeated sentences get merged into one long "word."** Any lone token spanning
  2 s+ is a merge — re-transcribe that slice in isolation to find the hidden repeat.
- **Whisper's word alignment is unreliable wherever there are aborted takes.** It will smear
  one sentence across a 10 s span that actually holds a false start *plus* the real take.
  Trust `silencedetect`, not Whisper, for structure:
  1. map the speech islands,
  2. re-transcribe **each island separately** (`-ss` / `-to` on the island bounds),
  3. only that tells you which island is the complete take.
- **Cross-check at −40 dB.** A quietly-spoken take can read as silence at −33 dB and vanish
  from the map entirely.
- **The island level follows the voice.** −33 dB was tuned on a voice peaking around
  −20 dB. On a take 18 dB quieter it splits every sentence at its softest syllables
  (measured: 16 islands became 44, and every split is a join that can stutter or repeat a
  word). `cut_aroll.py --plan` therefore defaults to `min(−33, voice − 13)`, held 12 dB over
  the room's RMS floor (silencedetect reads peaks). `--noise` overrides it.

---

## 2. Tighten to speech chunks, not takes

Cutting at take level is not tight enough — the standing note is always "the cuts are a bit
too slow."

1. Split every chosen take on internal silences (`silencedetect noise=-30dB:d=0.20`) and
   **transcribe each chunk in isolation.** Run this audit on **every** chosen take, not just
   the suspicious ones. This is the only thing that exposes a false start hiding inside an
   otherwise-good take — e.g. a take that opens with a half-sentence, restarts, and finishes
   clean. The aborted half is invisible in a take-level transcript and glaringly obvious on
   screen.
2. Concat chunks, not takes.
3. **Derive the edited gap from the original pause** so the speaker's rhythm survives:

   ```
   gap  = clamp(0.60 × original_pause, 0.15, 0.32)
   lead = 0.06
   tail = max(gap − lead, 0.09)
   ```

   Across a topic/category boundary use a flat **0.26**. This is still aggressive — it removes
   ~7% of a typical raw — without running phrases together. **Do not crush every gap to the
   floor:** a 0.06 s total gap makes the speaker sound like they are interrupting themselves.
4. **The closing segment takes `tail = 0.45`**, not the body tail. Without it the final word
   reads as clipped even though every syllable is present — what is missing is the decay plus
   a beat of air for the CTA to land on.
5. Target: `silencedetect -33dB:d=0.20` on the finished A-roll returns **zero** hits.

### A word clipped at its own segment head is fixed by MERGING, not by nudging the in-point

The −26 dB gate fires on a loud voiced consonant and drops the quieter fricative in front of
it, so the transcript comes back **missing a leading letter** — that is the tell. Nudging
`src_start` earlier just re-runs the same gate on the same audio.

Merge the chunk into its predecessor instead: the audio becomes continuous across the join,
mid-segment audio is never gated, and the natural pause survives — which is exactly what
"sounds like a complete sentence" means. List it in `config.json → cutting.merge`
(`[["c05","c06"]]`) beside `cutting.drop`; the merged chunk takes the first's `onset_raw` and
the second's `offset_raw`.

**Chunks whose speech touches are merged automatically.** When chunk *i*'s speech runs into
chunk *i+1*'s runway, there is no pause between them: they are one utterance that the plan
split. Clamping one against the other would cut a word in half, so `cut_aroll.py` merges
them and says so.

**A quiet first consonant (ל / ו / ת on a soft take) gets its own lead.** The gate fires on
the vowel after it. Give that chunk `"lead": 0.10` in `chunks.json` (seconds of runway before
the measured onset, instead of `cutting.speech_lead`). Sibilant openers (ש ס צ ז ח) get
`cutting.soft_onset_lead` automatically from the raw transcript.

**Re-check any B-roll sitting on that beat** — merging lengthens the segment, so its clip has
to be rebuilt longer.

---

## 3. Find the onset at SPEECH level

`silencedetect` at −33/−40 dB stops at breath and room tone, which sit **40–150 ms before the
first consonant.** Cut there and there is an audible beat of nothing before the sentence.

Measure the first 10 ms window above **−26 dB** instead:

```bash
ffmpeg -y -t 1.2 -i SEG -af "asetnsamples=480,astats=metadata=1:reset=1,\
ametadata=print:key=lavfi.astats.Overall.RMS_level:file=/tmp/on.txt" -f null /dev/null
```

Take the first `pts_time` whose `RMS_level > -26`. (Note: **do not pass `-v error`** — it
suppresses the astats output entirely.)

### The gate follows the speaker, and never sinks into the room

`cut_aroll.py` measures every 10 ms RMS window of the raw: the 90th percentile is the
voice, the 10th percentile is the room between words. The gate is

```
gate = max( min(−26, voice − 12),  room + 8 )
```

A quiet raw lowers the gate so onsets are found at all; the `room + 8` floor stops it
sinking to where a word's decaying tail, a breath or the room itself still reads as speech.
Below that, the offset search runs to the next island, the segment's tail plays the next
chunk's first syllable, and that syllable plays **again** when the next segment starts
(measured on a quiet, noisy take: six words heard twice). The line `voice … room … → onset
gate …` says which limit applied.

### Bound the search window on BOTH sides

Searching a flat `start − 0.6 s` catches the **previous** chunk's tail, and the offset search
catches the **next** chunk's head — silently gluing neighbouring sentences together. For
chunk *i*:

```
lo = max(start_i − 0.6, end_{i−1} + 0.005)
hi = min(end_i + 0.5, start_{i+1} − 0.005)
```

**Add an explicit floor just after any DROPPED false start.** A discarded take is not in the
chunk list, so nothing else stops the search locking onto its tail.

> A shift that comes back as exactly `−0.600` — your search-window edge — is always this bug,
> never a real onset.

---

## 4. Land the speech one frame in

Cut so true speech lands **exactly 0.04 s (one frame) into the segment**: enough runway that a
plosive is not clipped, too little to read as dead air. The target is the segment's own lead
(`cutting.speech_lead`, the soft-onset lead, or the chunk's `"lead"`), and each segment is
accepted within −25/+15 ms of **its** target.

Iterate: measure, shift the start by `(speech_onset − lead)`, re-cut, re-measure. The
measurement is taken on the segment's PCM, which has no codec priming, so pass 0 normally
lands exactly (measured: 0 ms residual on all 16 segments of a phone take) and only the audio
is re-cut; video is encoded once, after the audio has settled.

- `end = last_word_end + tail`
- Fades: **0.02 s in**, ≤ **0.055 s out**. A 0.06 s fade-in eats the first consonant when
  speech starts at 0.04 s. The fade-out must start well after the last speech sample:
  `fade_out ≤ tail − 0.035`, always.
- **No segment may reach into the next one's source audio.** A tail that does is clamped to
  the last whole frame before the next segment starts, and reported.

### Build the A-roll's audio from PCM, encode AAC once

**Never concatenate per-segment AAC with the concat demuxer (`-c copy`).** Every AAC encode
prepends ~1024 samples of priming and pads its last frame; a stream copy carries that into
every join. The voice then runs ~21–30 ms later at each cut: **+460 ms behind the lips by the
end of a 16-segment, 63 s phone take**, while the video frame count is still perfect. Packet
counting cannot see it.

What `cut_aroll.py` does instead:

1. each segment's audio → PCM WAV with **exactly** `frames × 0.04 × 48000` samples
   (`apad` + `atrim=end_sample`), so a PCM concat is sample-exact;
2. each segment's picture → a **video-only** mp4 (`segments/_video/`). The concat demuxer
   starts every file at its earliest packet, and an AAC track's first packet sits at −21 ms:
   concatenating clips that carry audio puts the whole picture 21 ms late;
3. video: concat demuxer, `-c copy`, with a `duration` line per file; audio: the PCM concat;
   one mux, **one AAC encode**.

`segments/<name>.mp4` (picture + its own audio) is still written for anything that wants a
single segment.

To re-time a fade without re-timing the edit, keep the stored `src_start` / `src_end` and only
re-encode — durations stay identical, so no caption needs touching.

---

## 5. Quantise every segment to whole frames

**This is the cause of the single most-repeated note in this pipeline.**

A non-frame-aligned duration encodes *longer* than requested (25 fps rounds up), so the
planned cumulative boundaries drift later and later than the real concat — 0.27 s by the end
of a 59 s cut. Every layout cut then fires **before** its sentence actually starts, which
reads on screen as *"you cut into the next sentence but the old one is still up."*

```python
nframes = math.ceil(dur / 0.04)
# encode with:  -frames:v nframes  -t nframes*0.04
```

Build the timeline from those exact values, then **prove it**: ffprobe every segment,
accumulate, and assert real == planned to under 1 ms before touching the composition.

### Count PACKETS, not seconds

```bash
ffprobe -v error -select_streams v:0 -count_packets \
        -show_entries stream=nb_read_packets -of csv=p=0 seg.mp4
```

`sum(segment frames) == concat frames` and `cum_frames × 0.04 == planned_start` for every
segment means the cut is frame-exact. Anything else is drift.

Do this instead of trusting `format=duration`, which reports the AAC tail (~20 ms past the
last video frame) and makes a perfect concat look 0.02 s long.

### Then prove the SOUND is in sync (gates)

Frame-exact picture says nothing about the audio. `cut_aroll.py` fails the cut, and
`preflight_qa.py --aroll` fails the project, unless:

- **the audio stream is as long as the video, to within one frame** (decoded samples, not
  `format=duration`);
- **every boundary is within 10 ms of the raw:** a 0.5 s window of the A-roll's audio just
  after each segment start is cross-correlated against the same window of the raw
  (`src/bounds.json` records `src`, and each segment's `src_start`); the offset is measured
  against the video stream's start, which is what a player syncs to;
- **no two segments share source audio**, and with the raw transcript, no word plays twice.

`python3 $S/scripts/cut_aroll.py --src raw.mp4 --verify assets/aroll.mp4` re-runs the sync
proof alone. A drifting A-roll reads as a growing positive offset (+27, +48, +75 … ms).

### Do NOT "verify" boundaries by onset-detecting from `start − 0.25 s`

Segments are butt-joined with only ~0.03 s of tail, so that window is still inside the
*previous* sentence, and every boundary falsely reads as ~0.25 s of drift. Search from the
boundary **forward** (`-ss start -t 0.6`); a healthy segment answers 0.03–0.07 s.

---

## 6. Verify

```bash
ffmpeg -i aroll.mp4 -af "silencedetect=noise=-33dB:d=0.3" -f null -
```

Only natural mid-sentence breaths may survive. Then carry the raw words onto the cut
(`map_words.py`) — captions must be built in the final timebase, never the raw's.

**One frame rate end to end.** A cut A-roll is 25 fps (a `--whole` take keeps its native
rate). `config.json → project.fps` must say the same, and the render must be given it
(`hyperframes render --fps <project.fps>`; without the flag it falls back to 30). Preflight
fails when the master, the config and the A-roll disagree.

**Composition duration = the A-roll's real duration.** Setting it short silently chops the last
word. Check that the last speech sample sits inside the composition, and leave ~0.6 s of tail
after the final syllable or the ending reads as cut off even when every word is present.

---

## 7. Audio level of the raw

Raw recordings sometimes arrive **already clipped at 0.0 dBFS.** The render inherits it and
preflight fails on it. Check the A-roll's peak before rendering and fix with a plain
`volume=-2dB` on the audio (`-c:v copy`, durations unchanged → no caption needs touching).

**Do not use `alimiter`** — its auto-makeup gain pushes the peak straight back to 0.0 dB and
raises the mean.
