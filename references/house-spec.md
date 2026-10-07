# The house spec: the premium motion-edit method

This is the reference the scripts, templates and other references cite as **"spec §N"**
(for example `spec §4.3`, `spec §9.6`). The section numbers are stable: §0 … §12.

It describes the house method for turning a raw vertical talking-head video (a real recording
or an AI avatar, Hebrew-first, 1080x1920) into a finished edit at the level of a premium agency
reel: kinetic word-by-word headlines, literal UI "designed moments" that illustrate each line,
a hook where the frame flies away into a designed world, 1-3 word hard-swap captions, camera
punch-ins, a story-driven music bed, tight sound design, an optional branded logo outro, and a
full QA loop on the rendered file.

Every number below is part of the house style. Where a rule says MUST, it is not optional. The
how-to for each part lives in the topic references (`storyboard.md`, `kit.md`, `kinetic.md`,
`grid.md`, `captions.md`, `outro.md`, `sound.md`, `qa.md`); this file is the method they share.

---

## 0. Principles (the taste layer, read first)

1. **Premium restraint.** Clean, confident, generous negative space. One idea on screen at a time. Never two widgets competing. Never busy.
2. **Literal, concrete, witty.** Every graphic illustrates the exact sentence being spoken, using a UI-native metaphor the viewer recognizes in under a second: an inbox, a calendar, a task card, a progress bar, a waiting room, an approval dialog, a chat bubble. Not abstract glowing shapes. Add a light comic twist where it fits (the approval dialog where the viewer approves themselves, an event that keeps getting postponed, a "בלה בלה בלה" bubble that pops). Never cringe.
3. **Derive, don't copy.** The objects named in this document are examples. For every new video, invent new moments from that video's own lines. Keep the LANGUAGE (rhythm, type, motion, sound, layout rules), not the objects.
4. **Callbacks.** Plant a visual early and pay it off at the end (prison bars slam shut at "the prison we built", and the same bars shatter at "that is exactly why you are free"; puppet strings appear at "someone else moves your life" and snap at "the power passes to you"). One or two callbacks per video make it feel authored.
5. **Something changes every 2-4 seconds.** A widget, a headline, a punch-in, a caption swap. No static stretch longer than ~0.6s anywhere (this is checked automatically in QA).
6. **Sync to words, not to guesses.** Every visual beat lands within 0.1s of the word it illustrates, using word-level timestamps.
7. **Brand at the end, not throughout.** The body is about the message; the brand lands in the outro.

---

## 1. Ingest and analysis

### 1.1 Probe and prepare
- `ffprobe` the source: resolution, fps, duration, audio sample rate. Keep the native fps for render (25 for HeyGen avatars, 30 for phone footage).
- Copy the raw into the project as `assets/raw.mp4`. Never modify the original.
- Extract a 16 kHz mono wav for transcription.
- Measure loudness: `ffmpeg -i raw.mp4 -af ebur128 -f null -` (integrated LUFS) and `volumedetect` (mean dB). AI avatar voices often come in very quiet and peaky (around -36 LUFS, mean -40 dB). This drives SFX scaling (§7) and mastering (§8).
- The outro needs the exact last frame of the A-roll. `outro.py` grabs it itself, at build time, as `assets/outro_last.png`, and stops the build when the file is missing (the renderer silently skips missing images). Nothing to do by hand.

### 1.2 Transcription (MUST cross-check)
- `scripts/xcheck.py` runs both engines and writes the diff (`src/transcript_diff.md`).
- Primary (its timings are kept): the Hebrew fine-tuned model `ivrit-ai/whisper-large-v3-turbo-ct2` via `faster-whisper` (run under `uv run --with faster-whisper --with "av<14"` when it is not installed; the `av<14` pin avoids a `metadata_errors` crash).
- Secondary: `mlx-whisper` with `mlx-community/whisper-large-v3-turbo`, `--language he --word-timestamps True`, plus an `--initial-prompt` glossary of the names the speaker uses: the brand, its products, and the tools mentioned, in both Hebrew and Latin spelling (config `language.glossary`). For example: "<שם המותג>. <שם המוצר>. סוכנים, אוטומציות, וייב קודינג, פרומפט, בינה מלאכותית." Without mlx-whisper it falls back to faster-whisper large-v3. `--primary b` swaps the roles.
- Diff the two. For every disagreement decide by meaning and context. AI avatars sometimes mispronounce the script ("גדלו" for "גידלו", "עוזר" for "עובר"); captions MUST show the intended, correctly spelled Hebrew, while timings come from the audio. Also correct spelling in captions even if the speaker says it colloquially ("לעלות פוסטים" becomes "להעלות פוסטים").
- `src/words.json` is a list of `[start, end, "word"]` (seconds, then the word, with its punctuation), on the A-roll timeline (`map_words.py`). Everything downstream reads from it.
- Report to the user every word that was changed and why.

### 1.3 Framing map (MUST, before designing anything)
- Extract ~10 frames across the video, scale to 270x480, overlay a grid (`drawgrid=w=27:h=48`, so each cell = 108x192 real px), tile them, and look.
- Write down in real pixels: head top, face center, chin, chest (shirt) area, where hands gesture, and the free zones. A typical medium-close framing: head top ≈620, face center ≈(560, 860), chin ≈1120, dark shirt chest 1200-1520, free sky 220-600, hands at the sides below 1300.
- These numbers decide where headlines, captions, and widgets go. Text sits on high-contrast areas (white text on a dark shirt; widgets as dark glass on bright sky).

---

## 2. Layout: the approved Instagram Reels grid (1080x1920)

- **Hidden by UI:** top 0-220 (reel header); bottom 1520-1920 (username, caption, music); right button column x ≥ 940 at y 880-1520.
- **Safe zone for every text and object:** x 60-940, y 220-1520.
- **Centring:** everything centred sits on the FRAME centre, **x 540**: an element up to 800 px wide is centred on 540 (its right edge stays ≤ 940, clear of the rail); only a wider one shifts left, just enough to keep its right edge on 940. One helper, `grid.centered_box(width)`; the 800 px lane is x 140-940 (`left:140px; right:140px`). Centring on the safe zone's middle (x 500) read as off-centre next to a centred speaker. `references/grid.md`.
- **Captions:** default band 1110-1190, centred on x 540 (plate at most 800 px). If that band lands on the speaker's chin or face in this framing, move it down onto the chest (for example 1236-1312) and say so in the report.
- **Kinetic headlines:** right-aligned with a 160px right margin, on the chest (the one exception to centring).
- **Bottom cards:** anchored to the 1520 line and growing upward, max ~300px tall.
- **Sky widgets:** `left:140px; right:140px; top:250px` (800 px, centred), ending by y 600, and above the measured head top − 20 when the framing map has one.
- **Paid ads:** Meta's conservative guide is 14% top, 35% bottom, 6% sides. Apply it only when the video is a paid campaign.
- QA: draw the hidden zones over snapshots and confirm nothing important enters them.
- **16:9 YouTube footage:** the same language applies (word-by-word headlines, literal UI moments, hard-swap captions, music and SFX rules, QA). Skip the Reels grid. If the raw already carries a designed frame (for example a white border with the logo and an inner picture box), every element MUST stay inside the inner box. Render at the source resolution (4K stays 4K). Put text behind the speaker using a person matte (background removal on the footage segment) so headlines sit between the wall and the person. No outro unless the user asks for one.

---

## 3. Storyboard method

Work line by line through the transcript and write a table: time range, the words, what is on screen, captions on/off, punch-in, SFX. Then build it. (The full method and catalogue: `references/storyboard.md`.)

### 3.1 Structure for a ~55s monologue
- **Hook (0 to ~6s):** the first words appear word by word as a thin headline on the chest (0.0 to ~1.0s). Then the whole frame shrinks and flies up into a designed world. Two or three cards illustrate the first sentences, about 1.6-1.8s each. Then the frame returns to the speaker through a blue tint. The speaker is off screen for at most ~5s.
- **Body:** 8-12 designed moments (widgets in the free zone, or full-frame overlays like bars and strings) plus 5-7 kinetic headlines on the punchiest phrases. Plain captions fill the rest. The designed moments are mostly per-video kit scenes (`references/kit.md`); the ready-made moments of `references/moments.md` (paper, fly, stamp, chips…, two to four per reel) count among the 8-12, they do not add to them.
- **Ending:** pay off the callback on the final line, then the outro (if one was asked for).

### 3.2 Worked example (an invented script, ~22s)
A model of density and literalness, not a template. A complete worked example with captions, punches, SFX and the `scenes.py` code is in `references/storyboard.md` §7.

| Time | Line | On screen |
|---|---|---|
| 0.00-1.10 | "Your calendar is lying to you" | Thin, word-by-word stacked headline on the chest ("lying" in light blue, bold) |
| 1.10 | (hook) | Frame shrinks (scale .34, y -900, radius 60, blur 14) and flies up into the dark-blue world |
| 1.15-2.90 | "eight meetings a day" | Card "Today": meeting blocks stack one by one until the day is full, the last one squeezed in; big gradient title "eight meetings" under the card |
| 2.85-4.60 | "forty unread messages" | Card "Inbox" with a spinning refresh icon; the unread counter steps 38 → 39 → 40. Title "unread" |
| 4.55 | (return) | Frame flies back in (scale .4→1.08, blur 16→0, .38s) through a blue screen tint that fades .9→0 |
| 4.80-6.20 | "and the real work waits" | Sky widget: task card "The real work", orange "waiting" pill, a progress bar stuck at 2%. **Plant** |
| 7.00-8.40 | "you call it busy" | Headline "you call it / busy." (gradient on busy), punch-in 1.12 |
| 8.60-10.40 | "I call it hiding" | A "Do not disturb" sign drops in and swings; camera sway (with scale ≥1.07) |
| 10.60-12.80 | "cancel one meeting" | Dialog "Cancel this meeting?"; a hand cursor taps "Cancel", it turns green "1 hour freed ✓" |
| 13.00-15.20 | "and watch what happens" | **Callback:** the task card returns; the pill flips "waiting" → "in progress", the bar runs to 100%; slow push-in |
| 15.40-17.20 | "that is the job" | Headline "that is / the job." huge gradient |
| 17.40-21.90 | (outro) | Logo outro (§6), only if the user asked for one |

### 3.3 Caption rules
- 1-3 words per card. Hard swap: the next card replaces the previous within one frame. No fade, no pop, no scale.
- Each card starts at its first word's start time. It ends at the next card's start or the next hidden window, and is trimmed to the last word's end plus 0.3s if a pause longer than 0.6s follows.
- **Hide captions** during the hook world, during every kinetic headline window (the headline is the caption), and during the outro. Assert in code that no caption starts inside a hidden window.
- Break chunks only at natural phrase boundaries. Never split a name or a construct.
- No dashes anywhere in on-screen copy (number ranges are the only exception). No keyboard emoji in on-screen text.

---

## 4. Visual system (exact tokens)

### 4.1 Colors
The house palette. It is the default when no logo was given; with a logo, the brand colours derived from it replace it (`references/brand.md`).
```
--blue:  #2F9BFF   (bold keyword)
--blue2: #8CC8FF   (light partner word)
--eblue: #1E8BFF   (electric blue, gradients, chips)
--lav:   #C9B8FF   --pink: #FF9ECF  (gradient tail)
--green: #30D158   --red: #FF453A
glass:   background rgba(10,14,24,.84); border 1.5px rgba(255,255,255,.16); radius 36px;
         backdrop-filter blur(22px) saturate(1.3); shadow 0 22px 56px rgba(6,18,36,.35)
world:   radial-gradient(120% 70% at 50% 110%, #1E8BFF 0%, #0B3E86 38%, #061224 72%, #03070F 100%)
card:    #0D1526 body, #141C2E header strip, border 2px rgba(255,255,255,.12), radius 44, shadow 0 40px 100px rgba(0,0,0,.5)
```
Glass opacity .84, not .66: lighter glass over a bright sky reads as dull grey.

### 4.2 Typography (font: Heebo variable, 100-900, bundled locally)
- **Kinetic headline:** right-aligned, `right:160px`, top on the chest (≈1215), line-height 1.06, `white-space:nowrap`, 2 lines usually.
  - Sizes: 112px base, `.big` 140px, `.huge` 170px.
  - Classes: `.t-thin` weight 200 white (framing words); `.t-bold` 800 #2F9BFF (the keyword); `.t-light` 500 #8CC8FF (partner word); `.t-grad` 800 gradient text `linear-gradient(90deg,#1E8BFF,#C9B8FF 60%,#FF9ECF)` with `background-clip:text` (the closer or the emotional word).
  - Each word is its own inline-block span with **CSS `opacity:0`**. It lands on its spoken timestamp: from opacity .18, `blur(6px) grayscale(1)` to opacity 1, no blur, in 0.22s `power2.out`. No slide, no bounce. The headline hard-cuts away at the end of its window, with no exit animation.
- **Captions:** 62px, weight 500, white, `text-shadow: 0 2px 12px rgba(0,0,0,.6)`, centred on x 540 in `left:140px; right:140px`, height 76. On a bright band, the `plate` style (black on a white box; heavier weight allowed).
- **Hook card titles:** 140px `.t-grad`, centred on x 540 at y≈1110.
- **Widget text:** titles 54-56px weight 800; secondary 34-40px at 70-75% white.

### 4.3 Motion vocabulary (GSAP; all seek-safe, see §9)
```js
const X = "expo.out", IN = "power3.in";
const word = (sel, t) => tl.fromTo(sel, { opacity: 0.18, filter: "blur(6px) grayscale(1)" }, { opacity: 1, filter: "blur(0px) grayscale(0)", duration: 0.22, ease: "power2.out", immediateRender: false }, t);
const pop  = (sel, t, d = 0.5) => tl.fromTo(sel, { scale: 0.5, opacity: 0, filter: "blur(10px)" }, { scale: 1, opacity: 1, filter: "blur(0px)", duration: d, ease: X, immediateRender: false }, t);
const drop = (sel, t, d = 0.55) => tl.fromTo(sel, { y: -60, opacity: 0, filter: "blur(14px)" }, { y: 0, opacity: 1, filter: "blur(0px)", duration: d, ease: X, immediateRender: false }, t);
const away = (sel, t) => tl.to(sel, { y: -40, opacity: 0, filter: "blur(12px)", duration: 0.28, ease: IN }, t);
const steps = (sel, list) => { const kids = [...document.querySelectorAll(sel + " > *")]; list.forEach(([t, k]) => kids.forEach((el, i) => tl.set(el, { opacity: i === k ? 1 : 0 }, t))); };  // seek-safe text/state swaps
```
- **Widgets:** enter with `drop` (or slide from the side with blur), live 1-4s, leave with `away`.
- **State changes** ("unassigned"→"you", "open"→"in progress", counters, button labels): stack all states in one grid cell (`display:inline-grid`, children `grid-area:1/1; opacity:0`) and switch with `steps`. Never tween textContent.
- **Stamps:** from scale 2.2-2.4, rotation -14 to -18 to scale 1, rotation -5 to -8, in 0.18s `power4.in`.
- **Strike-through:** a red 9px bar, `transform-origin:100% 50%` (RTL), scaleX 0→1 in 0.22s, then the pill dims to opacity .45.
- **Spinners:** linear rotation for the whole life of the widget. They never stand still.
- **Ambient life:** every full-screen world or card gets a slow drift so nothing freezes: world scale 1→1.12 across the hook, cards scale 1→1.04, small floats.

### 4.4 Hook mechanics (exact)
- **Out:** world `opacity 0→1` over 0.25s, and at the same moment `#cam` goes `{scale:1, y:0, borderRadius:0, blur 0}` → `{scale:.34, y:-900, borderRadius:60, blur(14px)}` in 0.32s `power3.in`; set cam opacity 0 when it lands.
- **Cards:** each enters differently. Card A from below (y 260, scale .86, blur 24 → 0, 0.45s `expo.out`). Card B from the right (x 600, rotationY -18, blur 20). Card C from below (y 300). Each title fades up 0.3s after its card.
- **Back:** cam opacity 1, then `{scale:.4, y:-700, radius 60, blur 16}` → `{scale:<next punch>, y:0, radius 0, blur 0}` in 0.38s `power3.out`. Add a tint layer (`linear-gradient(180deg, rgba(6,18,36,.2), rgba(30,139,255,.55))`, `mix-blend-mode:screen`) fading .9→0 over 0.5s; the world fades out 0.35s later.

---

## 5. Camera: punch-ins

- `#cam` wraps the video, with `transform-origin` on the face (for example 540px 864px).
- Snap scale with `tl.set` on phrase boundaries, alternating between 1.0 and a punch of 1.06-1.14. Use the bigger punches (1.12-1.14) on the key words: the punchline, the turn, the direct "you" line, the closer. Roughly one change every 2-4s, like a two-camera edit.
- Do not schedule punch sets inside the hook window; the hook controls the camera there.
- **Any camera rotation or sway MUST be paired with a scale of ≥1.07.** Otherwise rotating the frame exposes black corners.
- **Shakes:** on impacts (bars, stamps), y ±14 for 0.06s, yoyo, repeat 3.
- **Slow push:** 1.0→1.08 over 0.6s on emotional beats.

---

## 6. The branded outro: "the frame becomes part of the logo"

Opt-in: only when there is a logo **and** the user said yes (never on a 16:9 piece unless asked). The idea, for any brand: **the video frame itself becomes part of the logo.** When the logo's mark has an opening (an arch, a gate, a ring, the counter of a letter), the speaker's frame closes into a door shaped like that opening, flies into it, and the speaker steps through. This is `scripts/outro.py` style `gate`; the per-brand implementation, the other styles and the QA list are in `references/outro.md`. Recompute the geometry for every video's framing and every logo.

**Sequence** (O = outro start, about 0.2-0.3s after the last word ends; total length 4.5s):
0. **At O:** reset the camera: `tl.set("#cam",{transformOrigin:"<door center>", scale:1, x:0, y:0, rotation:0}, O)`, so no punch-in or sway leaks into the outro.
1. **O:** a blurred, darkened copy of the last frame fades in behind (`#endbg`: last.png, blur 46px, saturate 1.15, slow scale 1→1.3, radial dim .35→1 over 1s). The video's last frame is held by a still image inside `#cam` from VDUR-0.05 on.
2. **O+0.02:** `#cam` clip-path closes from the full frame into a **door around the speaker's head and shoulders**, shaped like the mark's opening. For an arch: `inset(T R B L round R R 0 0)` (R = half the door width) in 0.68s `power3.inOut`. Example: `inset(480px 270px 640px 270px round 270px 270px 0 0)` for a face centered at y≈880 (door x 270-810, y 480-1280). Center the door on the face.
3. **O+0.25:** 7 soft light orbs (small solid circles with a box-shadow glow) fade and scale in around the frame (stagger 0.03).
4. **O+0.76:** the door flies into the mark's opening: `#cam` moves by (tx, ty) and scales to s in 0.66s `power3.inOut`, with blur 0→14→0. The orbs get sucked into the opening's center (stagger 0.035, `power3.in`) and vanish.
5. **O+1.14:** the mark draws itself around the door (a stroke reveal, e.g. stroke-dashoffset 54→0, 0.34s). **O+1.4:** a white-blue radial flash on lock; the mark gets a drop-shadow glow.
6. **O+1.58:** the speaker fades out inside the opening while a gradient light fills it (the speaker "steps through").
7. **O+1.5:** the logo's words slide out from behind the mark: a part on the left from x +(its width), a part on the right from x −(its width), each with blur 10→0, 0.6s `expo.out`. They sit in overflow-hidden wrappers that end at the mark's edge, so they appear to emerge from it.
8. **O+2.12/2.32/2.52…:** the tagline, if the user gave one, lands word by word with `word()` (first word weight 700 white; the rest weight 300 in a light brand tint, #8CC8FF in the house palette).
9. The whole logo slowly scales 1→1.045. Fade to black over the last 0.4s.

**Geometry** (1080x1920 frame; derived per video, never hand-placed):
- The lockup is the logo itself (`brand/brand.json`, measured by `scripts/brand_from_logo.py`), sized and centred on the frame centre x 540 by `outro.py` (the centring rule above; a lockup wider than 800 px shifts left to clear the rail). It yields the **opening on screen**: its width `w_open` and its top-centre `q`.
- The door: centred on the face, with top `T`; door width `w_door` (≈1.95 × face width, 440-600px).
- Scale: `s = w_open / w_door` (e.g. a 92px opening and a 540px door → 0.17).
- Translation: with transform-origin `o = (ox, oy)` (the door centre) and the door's top-centre `p = (doorCenterX, T)`, pin p onto q:
  `t = q − (o + (p − o)·s)`.
  Worked numbers: o=(540,880), T=480, s=0.17 → p maps to (540, 812); with q=(433.3, 788.2), t = (−106.7, −23.8).
- **Always centred:** the lockup (mark, wordmark parts, tagline, handle) sits on the frame centre x 540, every text line centred in the x 140-940 lane. A lockup too wide to stay inside x 140-940 after its slow push is SCALED down, never shifted; the push pivots on x 540 and the door's flight follows it, so the door still lands on the mark.
- **CSS class names MUST NOT collide.** A generic particle class `.tw` once collided with the tagline's `.tw` and stacked the tagline words on top of each other. Prefix scene-specific classes.

---

## 7. Sound

### 7.1 Music (generated per video, never stock)
- Generate with ElevenLabs Music (`POST https://api.elevenlabs.io/v1/music?output_format=mp3_44100_192`, `model_id: music_v1`) using a `composition_plan` whose sections follow the video's emotional arc. Read the key from the environment (`ELEVENLABS_API_KEY` in the project's `.env`); never hardcode it, never print it. Check credits first (`GET /v1/user/subscription`) and warn the user before starting if there are not enough. Without a key, follow the no-key path in `references/sound.md` ("Without an ElevenLabs key").
- A plan for a 55s motivational piece plus outro (total ≈60s):
```json
{"positive_global_styles": ["instrumental modern cinematic trap hybrid","deep 808 sub bass","ticking clock percussion that builds","hopeful synth pads","inspiring and powerful","leaves space for a spoken Hebrew voice","starts immediately"],
 "negative_global_styles": ["vocals","singing","lyrics","choir","rap","vocal chops","cheesy","aggressive distortion","long silence","fade in from silence"],
 "sections": [
  {"section_name":"Part 1","duration_ms":10000,"positive_local_styles":["tense and restrained, ticking like waiting, sparse pulse"],"negative_local_styles":["silence"],"lines":[]},
  {"section_name":"Part 2","duration_ms":7500,"positive_local_styles":["tension builds, darker, ends with a short hit"],"negative_local_styles":["silence"],"lines":[]},
  {"section_name":"Part 3","duration_ms":9500,"positive_local_styles":["hope enters, warm piano and pads, gentle rise"],"negative_local_styles":["silence"],"lines":[]},
  {"section_name":"Part 4","duration_ms":17500,"positive_local_styles":["driving and uplifting, steady momentum, full groove"],"negative_local_styles":["silence"],"lines":[]},
  {"section_name":"Part 5","duration_ms":10500,"positive_local_styles":["emotional peak, big and free, soaring"],"negative_local_styles":["silence"],"lines":[]},
  {"section_name":"Part 6","duration_ms":5000,"positive_local_styles":["resolves into one final deep hit and a ringing tail"],"negative_local_styles":["silence"],"lines":[]}]}
```
- **Generate 2 variants** with different global styles (for example one cinematic motivational score, one cinematic trap hybrid). Requests run sequentially, since parallel calls hit 429.
- **Analyze each:** print RMS per 0.5s to find the structure, then find the main drop at 20ms resolution.
  - Pick the variant whose biggest structural change can land on the story's turning point.
  - Set `offset = dropTime − (turnWordStart − 0.06)` and trim the track's start by it. Example: a drop at 24.04s in the track and a turn word starting at 23.58s give offset 0.52s.
  - Check where the track's natural ending then falls: ideally right at the outro start.
  - Reject tracks with long silent intros or gaps.

### 7.2 Bed calibration (per section, against the raw voice)
A script (`scripts/bed.py`) that:
1. Trims the track by `offset`, cuts it to END, and adds a 12ms fade at the start.
2. Builds a gain envelope from keyframes:
   - **Sections:** each section `[name, start, end, target_gap_dB]` gets keys at `start+0.12` and `end−0.12`. **Never put two keys at the same timestamp**: tied keys make `np.interp` ramp across the whole section (a real bug).
   - **Drops before punchlines:** `[a, b, ratio]` with a 60ms ramp in, level `sectionGain × ratio` (0.08-0.15), and back at b. Drops MUST be relative to the containing section; fixed levels raised the music on quiet voices.
   - **Outro lift** (the voice is silent): last gain at O, ×2.0 at O+0.5, ×1.6 at O+2.2, ×0.6 at END−0.5, 0 at END.
3. Applies an EQ pocket for the voice (−5 dB at 1.8 kHz Q1, −3 dB at 450 Hz Q1) plus a sidechain compressor keyed by the voice (voice −6 dB into the key, threshold 0.03, ratio 3, attack 15, release 260).
4. Measures, per section, `gap = RMS_dB(voice) − RMS_dB(processed music)` and updates `gain *= 10^((gap − target)/20)` (cap 6.0). Iterate 6 times.

Targets (voice above music): a typical quiet AI-avatar monologue sits at 14 dB in most sections and 13 dB where the story peaks (the build, the final stretch). Typical range is 12-16 dB. Use lower numbers (louder music) where the story peaks. Drops are short (≈0.2-0.26s) and sit just before the line that sets up the turn, just before the turn itself (ratio .15), before the key "you" claim, and before the final word.

### 7.3 SFX
- **Library (short wavs, 48 kHz stereo):** whoosh_impact, soft_whoosh, whoosh_low, swap_pop, pop, click, ding, glass_snap, riser_short, typing, message, comment_ping, page_flip, portal_suck, logo_sting. If any are missing, generate them with the sound-generation endpoint below. Keep each under ~1.3s (logo_sting ~3-4s), then convert to 48 kHz stereo wav.
- **Custom per video:** generate with ElevenLabs sound-generation (`duration_seconds: 1.3`, `prompt_influence: 0.6`). Example prompts: "heavy steel prison cell bars slamming shut, metallic clang, short"; "thin strings snapping, quick twang, short"; "light building blocks stacking click, short, satisfying"; "metal bars breaking apart with a bright shimmer, short"; "single soft clock tick tock, short".
- **Placement:** an effect on every designed transition (widget in, state flip, stamp, bars, strings, bricks, button tap, hook out and back, outro: soft whoosh at O, portal_suck at O+1.12, logo_sting at O+1.7). Do not put effects on captions or headlines.
- **Scale to the voice:** `k = clamp(10^((voiceMean_dB + 16.1)/20), 0.03, 1)`. The presets were tuned for a voice with a −16 dB mean; a quiet avatar voice gets small effect volumes, and the master lifts everything together. Base volumes 0.1-0.45 times k.
- **Effects MUST NOT sit on words.** An effect over a short word swallows it (a real failure: a short word was heard as a different word; another time a key word was masked). For each effect that falls inside a word:
  - If the gap before that word is ≥0.14s, move the effect to `wordStart − 0.1`.
  - Otherwise, if the gap after the word is ≥0.14s, move it to `wordEnd + 0.02`.
  - Otherwise, halve its volume.
  - **Never chain-slide across several words.** That is exactly how an effect drifted forward onto a key word.
  - Mark deliberate impacts that must stay on the beat (bars, shatter, stamps, hook whooshes, outro) as exempt, and keep them moderate.

---

## 8. Mastering (target: −14 LUFS integrated, true peak ≈ −1.3 dBFS, AAC 320k)

**A typical quiet, peaky AI-avatar voice** (around −36 LUFS):
```bash
ffmpeg -i renders/render.mp4 -vn -af "volume=20dB,acompressor=threshold=0.06:ratio=3:attack=4:release=140:makeup=1" -c:a pcm_s24le c.wav
I=$(ffmpeg -i c.wav -af ebur128 -f null - 2>&1 | grep -A3 Summary | grep 'I:' | awk '{print $2}')
G=$(python3 -c "print(round(-12.8-($I),2))")
ffmpeg -i renders/render.mp4 -i c.wav -map 0:v -map 1:a -c:v copy \
  -af "aresample=192000,volume=${G}dB,alimiter=limit=0.84:attack=2:release=60:level=false,aresample=48000" \
  -c:a aac -b:a 320k -movflags +faststart master.mp4
```
- Without the 20 dB pre-gain and compression, the limiter clamps the voice peaks and the result sticks at −15.9 LUFS.
- For a normal, hotter voice (≈ −20 LUFS), use a gentle chain instead (compressor threshold 0.08, ratio 2, no pre-gain, pre-limiter target −13.4).
- Always measure the master. If it is not within −14 ± 0.4, adjust the pre-limiter target and redo.

---

## 9. HyperFrames engineering rules (MUST, each one cost a failed render)

The skill renders with HyperFrames. A port to another renderer maps each rule to that renderer; the seek-safety, determinism and overlay rules apply to any frame-by-frame HTML capture.

1. **Structure:**
   - Root `<div id="root" data-composition-id="main" data-start="0" data-duration="END" data-width="1080" data-height="1920">`.
   - Every timed element has `class="clip"`, `data-start` and `data-duration`.
   - The video keeps its own audio: `data-has-audio="true"`, no `muted`. Music and SFX are `<audio>` clips with `data-volume`.
2. **One paused GSAP timeline** registered as `window.__timelines["main"]`.
3. **Seek-safety:**
   - Any tween that is not the first thing affecting an element is a `fromTo` with `immediateRender:false`.
   - Initial hidden states come from CSS (`opacity:0` on words, widgets, pills, bricks), not from an early `set`.
   - Reset-at-zero patterns are fine: `tl.set(path,{strokeDashoffset:100},0)` before a later draw tween.
4. **Don't:**
   - Tween `left`/`top`/`letterSpacing` (use x/y/scale).
   - Use `:nth-child`/`:nth-of-type` selectors in tweens (give elements ids).
   - Overlap two tweens on the same property of the same element (lint flags it; shift start times).
5. **Determinism:** no `Math.random()` at runtime. Generate random positions in the Python builder with a fixed seed and write them as literal CSS/JS.
6. **Heavy overlays:** keep fewer than ~40 elements carrying `filter:blur`, radial-gradient or clip-path in CSS, counting hidden ones. Above that, the capture renders solid black for the first half of the video. Particles use solid colors plus box-shadow glow, not radial gradients.
7. **Assets:** fonts, GSAP and logos are all local. The lint error `missing_local_asset` is fatal: fix it, never ignore it.
8. **Build with a generator:** a Python builder (`scripts/build_index.py`) writes `index.html`, computes caption durations from `words.json`, places and slides SFX, measures the voice, and emits the timeline. Keep everything reproducible: re-running the build plus render must rebuild the exact same video.
9. **Lint after every build:** 0 errors. Read the warnings (overlaps and missing assets matter; nested-structure and file-size warnings can be ignored).
10. **Render:** `HF_VIDEO_COVERAGE_THRESHOLD=0 npx hyperframes render --quality high --fps <project.fps> --video-bitrate 32M --output renders/render.mp4` (exactly as in SKILL.md; `--fps` always, or the render falls back to 30), then `scripts/finish.py renders/render.mp4 --out renders/final.mp4` masters it.

---

## 10. QA loop (MUST, on real pixels and the real audio)

The commands and gates are in `references/qa.md`.

1. **Snapshots** before rendering: `npx hyperframes snapshot --at <every designed moment, mid-animation and settled, plus every outro step> --no-end`. Open the contact sheets and look at every frame. Check:
   - Text clipped by the frame edge, especially during punch-ins (scale shrinks the margins).
   - Headlines hidden behind the speaker's head, or overlapping the face.
   - Words visible before they are spoken (a missing CSS `opacity:0`).
   - Missing spaces between word spans.
   - Widgets overlapping each other or the captions.
   - Anything inside the grid's hidden zones.
   - Black corners during camera rotation.
   - Grey-looking glass.
   - Overlapping tagline words in the outro.

   Fix, rebuild, re-snapshot until clean.
2. **Render, then master.**
3. **Loudness:** `ebur128=peak=true`. Integrated loudness (I) must be −14 ± 0.4 and the peak about −1.3.
4. **Frozen frames:** `freezedetect=n=0.002:d=0.6` MUST report nothing. If it does, add drift or an earlier entrance there.
5. **Black frames:** `blackdetect=d=0.2:pix_th=0.05` must report nothing.
6. **Intelligibility:** re-transcribe the master's speech part with the same model and glossary, then diff against `words.json` (SequenceMatcher on normalized words). Target ≥97% word match. For every differing word, check whether an SFX or a music hit sits on it; move the SFX or deepen the duck, then re-render. A clean mix typically reaches about 99.3%.
7. **Transition frames:** extract stills at every transition (hook out, cards, return, bars, strings snap, final burst, each outro step) from the master and look at them.
8. **Report** to the user, in the user's language (Hebrew by default):
   - Where the file is.
   - A short list of what happens on screen, line by line.
   - The music choice and why.
   - The QA numbers.
   - Every place where the speaker's words were corrected or a rule was deviated from, and why.

---

## 11. Deliverable conventions
- Output next to the source (or in the configured export folder, `references/delivery.md`) with a clear name in the user's language that says it is the edited version (for example `<source name> - edited.mp4`). Never overwrite the original. When replacing a previous delivery, archive the old one; never delete outputs.
- Keep the project folder (the build files, `media.json`, `scenes.py`, the bed files, `words.json`, `renders/`) so any fix is a rebuild, not a redo.

---

## 12. Final checklist (run through it before reporting done)
- [ ] Two-model transcription diffed; caption text corrected to the intended script; changes listed.
- [ ] Framing map written; every element placed inside the grid's safe zone; captions on a high-contrast band.
- [ ] Hook: words, frame flies away, 2-3 literal cards, return through the tint, speaker away ≤5s.
- [ ] 8-12 literal designed moments, 5-7 word-by-word headlines, at least 1 callback, nothing static >0.6s.
- [ ] Captions 1-3 words, hard swaps, hidden under headlines, hook and outro; no dashes; no emoji.
- [ ] Punch-ins on phrase boundaries; rotation only with scale ≥1.07.
- [ ] Music generated, 2 variants compared, drop aligned to the turn by offset, sections calibrated, relative drops, outro lift.
- [ ] SFX on every transition, scaled to the voice, never on words (except marked impacts).
- [ ] Outro (when asked for): geometry recomputed for this framing and this logo; logo inside the grid; no class collisions.
- [ ] Lint 0 errors; no heavy-overlay overload; all assets present.
- [ ] Snapshots reviewed and clean; master at −14 LUFS; no freezes; no black; transcript match ≥97%.
- [ ] Report in the user's language with location, beats, music, QA numbers, deviations.
