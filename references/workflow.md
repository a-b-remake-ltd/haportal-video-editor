# The working method — how a session runs

Audio first, eyes at decision points, nothing touched before the plan is approved, nothing
shown before it has been checked. Adapted from the session method of
[browser-use/video-use](https://github.com/browser-use/video-use) (MIT).

Every session follows these nine steps in order. The pipeline phases in `SKILL.md` are
step 5; everything around them is what makes the result the one the user wanted.

```
<project>/
├── config.json             brand, language, grid profile, outro — per project
├── project.md              memory: one section appended per session (step 9)
├── takes_packed.md         the phrase-level reading view of every source (step 1)
├── src/
│   ├── transcripts/*.json  one per source (cached; never re-transcribed unchanged)
│   ├── transcript_flags.md AI substitutions, doubtful words, merged tokens — to confirm
│   ├── aroll.json, words.json   transcript of the FINAL cut A-roll (captions read these)
│   └── bounds.json         segment boundaries of the cut (cut_aroll.py)
├── verify/                 timeline_view PNGs of sources and of the render
└── renders/
```

Cold start: `python3 scripts/doctor.py` (one second). Anything marked ✗ → run
`python3 scripts/doctor.py --install` and check again. Do not continue past a ✗.

---

## 1. Inventory

1. **Read `project.md` if it exists.** Summarise the last session to the user in ONE Hebrew
   sentence and ask whether to continue from there.
2. **ffprobe every source**: duration, resolution, fps, `color_transfer` (HLG/PQ = HDR, see
   `references/hyperframes.md`), audio track count, and the level (`volumedetect`). A raw
   whose speech sits around −40 dB mean will not cross a −26 dB onset gate — note it now.
3. **Transcribe every source in one call** — the model loads once:
   ```bash
   python3 scripts/transcribe.py raw1.mp4 raw2.mp4 --out-dir src/transcripts
   ```
   Hebrew runs on the free ivrit.ai model (about 20 s per minute of audio on an M1 Max).
   A file that has not changed comes straight from the cache.
4. **Pack:** `python3 scripts/pack_transcript.py` → `takes_packed.md`. This is the primary
   reading view: one line per phrase, `[start-end]` in source seconds, a line per sentence
   and per real pause. Read it whole. Raw json only when a line needs word-level detail.
5. **Read `src/transcript_flags.md`.** Confirm every "AI" substitution against its context
   ("איי" can also be "islands"); look at every `[?]` word.
6. One or two `timeline_view.py` images for a first look at the speaker, the framing and
   the light — not more.

## 2. Pre-scan

One pass over `takes_packed.md`, producing a plain list for yourself:

- repeated lines → **the last take wins** (references/cutting.md)
- false starts and aborted takes, including ones hiding inside a good take
- verbal slips, mis-speaks, a wrong word the user would not want published
- transcription doubts: an `[?]` word whose meaning changes the sentence, a long token
  (two takes merged). For a doubtful Hebrew span get a second opinion:
  `python3 scripts/transcribe.py raw.mp4 --start 41 --end 47 --compare` prints every place
  two engines disagree.

## 3. Converse

Describe what you saw in two or three plain Hebrew sentences (length, how many takes, the
strongest moment). Then ask the questions the MATERIAL raises — a talk with three hooks
asks which one opens; a take with a slip asks whether that sentence can go.

**Always ask these three together, in one short message — they are this skill's
signature:**

> יש סרטון רפרנס שאתה אוהב את הסגנון שלו? אפשר לשלוח קישור או קובץ.
> יש לוגו? נתאים את הצבעים למותג, ואפשר גם סגיר מונפש עם הלוגו בסוף.

When there is a logo, the outro is a CHOICE, not a yes/no: offer the looks in one line each
(`gate` first and recommended when the logo's mark has an opening, else `portal`), then ask
for the line under the logo and the handle — the exact wording is in `references/outro.md`
§1.

and in the same message: what kind of video it is, roughly how long, and where it goes —
an organic reel or a paid ad. A paid ad switches the grid to the stricter Meta profile:
`"grid": {"profile": "ads"}` (references/grid.md).

Where the answers go:

- reference video → `scripts/analyze_reference.py`, then `scripts/apply_style.py`
  (references/reference-analysis.md). Match the editing language, never copy content.
- logo → `scripts/brand_from_logo.py logo.png`; brand colours drive motion, highlights and
  accents. A brand colour is never overwritten by a reference.
- outro → only when there is a logo AND the user picked a look: `config.json` →
  `"outro": {"enabled": true, "style": "gate", "tagline": "…", "handle": "@name"}`
  (`scripts/outro.py`). If the logo already has words under its mark and the tagline repeats
  them, suggest leaving the tagline empty (the build warns).
- no answer / "you decide" → house defaults, and say in one line which ones you used.

When something BLOCKS the work (a missing file, a slip with no clean retake), ask that one
question alone and wait.

## 4. Propose the strategy — then WAIT

Four to eight plain Hebrew sentences: the shape (what opens, what closes), which takes,
the pace of the cut, where B-roll or graphics go and why, the caption look, music and
effects, the outro, the expected length. Example of the register:

> הסרטון ייפתח במשפט על 250 אלף הדולר, כי זה הרגע הכי חזק. אחריו ההסבר על הליווי, בגרסה
> האחרונה שצילמת. אני מקצר את ההפסקות אבל משאיר נשימה בין משפטים, כדי שזה יישמע טבעי.
> בשני מקומות ייכנסו המחשות: טופס פתיחת חשבון, ומפה של שווייץ. כתוביות בצבעי המותג, סגיר
> עם הלוגו בסוף. אורך משוער: 38 שניות. מתאים לך?

**Do not touch the cut until the user approves.** A changed plan is cheap; a re-cut is not.

## 5. Execute

The pipeline in `SKILL.md`, phase by phase, reading each phase's reference first. Two
method rules on top of it:

- **Drill in at ambiguous moments** with `timeline_view.py <source> <start> <end>` — which
  of two takes, where a breath ends, whether a pause is a real pause.
- **Caption text comes from the AUDITED raw transcript, carried onto the cut** with
  `map_words.py` (reads `src/raw_words.json`, writes `src/words.json`). Then transcribe
  `assets/aroll.mp4` (`--out src/aroll.json --words src/aroll_words.json`) and run
  `map_words.py --verify src/aroll.json` (it checks the words.json already written): the
  re-run hears each phrase with less context and is often worse ("ChatGPT" → "ChatGPGPT"),
  so it is a check, not the source.
- **Punch-ins come after the first build.** `plan_punches.py` reads `index.html`,
  `build/scenes.json`, `build/caption_hide.json` and `build/outro.json` and refuses to run
  without them; it blocks the hook, every scene camera move, every headline and the outro
  itself. Key words (a bigger punch on their start) and any extra blocked seconds go in
  `punches.json`: `{"key": ["word", "two words"], "block": [[12.3, 14.3]]}`. Rebuild after
  `--apply`.

## 6. Self-evaluate — BEFORE showing anything

You cannot watch or listen. Make the evidence, look at it, and report numbers — never
say you heard something.

1. **Every cut boundary on the RENDERED file**, ±1.5 s, with the boundary marked. The
   boundaries are in `src/bounds.json`:
   ```bash
   python3 -c "import json,subprocess; b=json.load(open('src/bounds.json'))['bounds'][1:]
   [subprocess.run(['python3','<skill>/scripts/timeline_view.py','renders/final.mp4',
     str(max(t-1.5,0)),str(t+1.5),'--marks',str(t),'--transcript','src/aroll.json',
     '-o',f'verify/cut_{i:02d}.png']) for i,t in enumerate(b)]"
   ```
   Look at each image for: a jump or flash at the cut, a waveform spike at the boundary
   (a pop), speech that starts before the cut line (clipped onset) or dead air after it, a
   caption hidden behind an overlay, an overlay showing the wrong frames.
2. **First 2 s and last 2 s** (does the hook land on frame 1; does the last word finish
   and get its tail).
3. **Grid:** `python3 scripts/grid.py overlay renders/final.mp4 --at <times>` at the hook,
   at every graphic and at the outro — nothing readable in a hidden zone.
4. **Levels, measured:** `ffmpeg -i renders/final.mp4 -af ebur128=peak=true -f null -`
   (integrated LUFS, true peak) plus RMS per section (speech, music only, outro). Effects
   louder than speech, or an outro 15 dB under the dialogue, are bugs.
5. **Words survived the mix:** transcribe the render and compare it with `src/aroll.json`
   word by word — an effect on a short word swallows it.
6. `python3 scripts/preflight_qa.py …` and walk `references/checklist.md`.
7. **Anything publishable** (a launch, a promo, an ad, anything with the user's name on
   it) → spawn ONE critic sub-agent with the rendered file, the plan, the reference video
   if there is one, and this brief:

   > You are a harsh short-form video critic. Watch-by-evidence only: extract frames,
   > run timeline_view.py at cuts, measure loudness. Do not praise. Return: a one-line
   > verdict; problems ranked by how much they hurt retention, each with a timecode and
   > the evidence (frame, number); the five fixes to do first. Hebrew captions: check
   > spelling, line breaks only at phrase boundaries, and that "AI" reads as AI.

Fix → re-render → re-check. **At most three passes.** If something is still wrong after
the third, stop and tell the user exactly what remains and why.

## 7. Present

A short Hebrew message: where the file is (the path on its own line), the length, two or
three decisions worth knowing about, anything still open, and the measured numbers in
plain words ("הקול בעוצמה רגילה לאינסטגרם"). One or two frames if they help.

## 8. Iterate

Notes come back in plain words. Re-plan, re-render only what changed, and
**never re-transcribe an unchanged file** (the cache makes this automatic; do not pass
`--force` out of habit). Fix the CLASS of every note, not just the instance: when a note
could come back on another video, make it a gate in `preflight_qa.py` (with a negative
test), not a line of prose (SKILL.md, "Read this first" §8). Final render only when the
user confirms.

## 9. Persist

Append one section to `project.md` at the end of every session — the next session starts
by reading it:

```markdown
## Session N — YYYY-MM-DD

**Strategy:** one paragraph — the approach the user approved
**Decisions:** take choices, cuts, style/brand/outro choices — each with WHY
**Measured:** length, LUFS / true peak, anything a number settled
**Open items:** what was deferred, what the user still has to send or decide
```

---

## Talking to the user

The users follow a video-editing tutorial; many are not technical.

- **Hebrew, short, plain.** No jargon: say "המחשות" not B-roll, "ייצוא" not render,
  "כתוביות" not captions/SRT, "הסגיר" not outro sequence, "עוצמת הקול" not LUFS.
- **One question at a time when something is blocking.** The step-3 opener is the one
  exception: its questions go together in one message.
- No long dashes in Hebrew text; a comma or a new sentence instead.
- A command, path or English term goes on its own line or in a code block, never inside
  a Hebrew sentence (mixed-direction lines render scrambled in the terminal).
- Never claim to have watched or listened. Say what you measured.
