# EXAMPLE OUTPUT: a finished `style/STYLE.md`

> **This is an example of the output FORMAT, not a style to apply.** It is the analysis of one
> real reference (an agency interview reel by Rollin Video Productions, Hebrew, personal brand),
> written the way `references/reference-analysis.md` asks for. The objects in it (a search bar,
> app tiles, a phone) belonged to THAT video's script. A new edit keeps the language and
> invents its own objects from its own lines.
>
> The [M] numbers were measured by hand at first and later reproduced by
> `analyze_reference.py` (script values in *italics* where they differ).

---

# Rollin Reel style: the "agency interview reel" look

Reference: one reel, 72 s, 720x1280 @25 fps, Hebrew, personal-brand interview.
Sheets read: hook, shots, caption band, cut strips, 1 s sheet, grid overlays.

[M] = measured from the file. [E] = observed by eye.

## 1. Overall feel
- Premium agency piece, not a creator reel. Cinematic interview footage plus a few "designed
  moments". Clean, confident, generous negative space. Never busy.
- Graphics are UI-native metaphors for what is said, literal and concrete, never abstract filler.
- Two-camera interview feel (wide / medium / close) with digital punch-ins between angles.

## 2. Rhythm [M]
- 29 cuts in 72 s, 12.1 cuts per 30 s. Shot length: mean 2.4 s, median 1.6 s, min 0.5 s,
  max 6.8 s. *(script: identical)*
- Cuts land on phrase boundaries. Angle alternates wide ↔ close; same-angle cuts become punch-ins.
- First word at 0.00 s. No intro, no music build.
- Speech pace [M]: ~3.1 words/s while talking.

## 3. Captions [M]
- 1 to 3 words, centred horizontally, centre at **65.5 % of frame height** (on the chest)
  *(script: 65.4 %, high confidence)*. Cap height about 2.2–2.5 % of frame height; width
  30–52 % of frame *(script median 36 %)*.
- [E] Mostly one line, but several cards break to two lines ("שהאנשים האלה / שמחפשים"). The
  house one-line rule overrides this when applying.
- White (#FFFFFF), medium-weight Hebrew sans, very soft shadow, no box, no stroke.
- **Hard swap**: a card replaces the previous one within a single frame. No fade, no pop, no scale.
- Reported speech in Hebrew quotes: "איך להגיע להיות", "בפרסונל ברנד?".
- Captions are hidden while a full-screen designed moment is on screen.
- Grid [M]: a plate centred at 65.5 % (y 1257 on 1920) reaches into the bottom-card zone
  (y 1220–1520). `apply_style.py` clamps it to y 1168, just above that zone.

## 4. Kinetic headline type (the signature) [E, timings M]
- On the key sentence, a 2–3 line headline builds **word by word, in sync with speech** (each
  word lands as it is said, ~0.1–0.2 s per word), placed over the chest/side, right-aligned stack.
- Each word enters as faint translucent gray and resolves to its final colour over ~0.2 s (no
  slide, no bounce).
- Mixed weights and colours: framing words thin white; the keyword bold saturated blue with a
  lighter blue partner; the closer in a pale blue gradient.
- Large: about 6–7 % of frame width per glyph height (≈70 px on 1080 w). Line height ≈1.0.
- Holds until the sentence ends, then **hard-cuts away with the shot change** (no exit animation).

## 5. Hook (0–3.6 s) [M]
- 0.00–0.40: words appear one by one, thin white, centred on the chest.
- 0.40–0.72: the whole frame shrinks into a rounded card and **flies up with heavy motion blur**
  into a dark-navy → electric-blue vertical gradient world (whoosh + impact SFX).
- 0.72–1.25: a floating collage of portraits at different depths with **rack-focus blur →
  sharp**, and a huge two-line gradient keyword in the middle (electric blue → lavender → pink).
- 1.28–2.2: same collage dimmed, a thin white glowing phrase building word by word.
  *(script: the 1.28 change is a dim/dissolve the detector counts as the first cut)*
- Then hard cut back to the interview; the caption-headline continues.

## 6. Designed moments (devices, with that video's examples)
1. **A UI that builds in sync with the speech** (3.6–6.4 s). *There:* a search pill whose
   query types character by character as it is spoken. Exit: scales down and slides while the
   scene cross-dissolves through a blue tint back into the footage.
2. **A full-screen "proof" page in 3D perspective with a slow dolly** (16.5–20 s). *There:* a
   dark-mode results page; the spoken definition gets a **highlighter sweep** word by word.
3. **Glossy tiles that state an idea as a game mechanic** (40–42.5 s). *There:* a "level 1"
   tile with a padlock that unlocks, joined by a glowing dotted arc drawn across the speaker's
   shoulders; tiles enter with blur and a slight 3D tilt, camera punches in.
4. **The footage becomes a phone / feed card** (62.2–64.5 s). *There:* the frame shrinks into a
   rounded card on near-white, a comment bar types a viewer's reaction, an avatar pops with a
   repost badge, then the card zooms back to full frame.
5. **Outro sting** (66–71 s): logo animation on charcoal → white with a low boom.

## 7. Sound [M, measured on a Demucs vocals / music split]
- **There IS a music bed under the whole piece.** Music stem ≈12–14 dB under the voice through
  the body *(script: 11.5 dB at rest; by thirds 9.8 / 13.2 / 9.6)*.
- Character: deep, sub-bass-heavy modern electronic/ambient bed *(script: 83 % of the music
  energy below 80 Hz)*, slow pulse; supportive, never melodic-busy.
- Dynamics follow the story: the bed swells in the hook *(script: +14 dB at 2.5–5.5 s)*,
  drops on emphasis beats *(11–12.5 s, and a near-silent drop at 34.5–38 s right before the
  punchline)*, swells around designed moments *(41–42 s, 62–63.5 s)* and resolves into the sting.
- SFX are tight and placed on designed transitions: whoosh + impact into the graphic world,
  hits on hook beats, on the results page, on the tiles, on the phone zoom; low boom on the logo.
  *(script: every one of them is in the transient list; the raw count of 19/min also includes
  music hits, the real SFX rate is ~8/min)*
- Between words the voice track is clean (noise-gated); the room is never heard.
- Lesson: never judge "no music" from pause levels. Split stems and measure the music stem.

## 8. Look
- Bright daylight exterior, shallow depth of field, creamy background, saturated sky, warm skin,
  teal/navy wardrobe. Slight cinematic contrast.
- Graphic palette: electric blue #1E8BFF / deep navy #061224 gradients, white, pale
  lavender-pink accents; coral #F58A7E and dark green #2F5D50 for tiles; dark-mode UI gray
  #202124. *(script accent cluster: #006BB4, the mid-tone of the blue gradient)*
- With a client logo, these give way to the brand colours. The roles (one hero accent, one
  partner, one dark base) carry over; the hex values do not.

## 9. How to apply

**The objects in §6 are examples, not a template.** Keep the LANGUAGE (rhythm, captions,
word-by-word headlines, motion quality, sound design, premium restraint) and invent NEW designed
moments that literally illustrate the new video's own lines. Before every element ask: does
this serve this sentence of this video? Add a light, witty, slightly comic idea where it fits (a
visual pun, an exaggerated UI, a playful reaction), never cringe.

- Vertical 1080x1920. Keep the speaker's real voice; gate between words; a sub-bass modern bed
  ≈12 dB under the voice with story dynamics (swell in the hook, a drop before the punchline)
  and SFX on every designed transition.
- Cut the talking head on phrases to a median ~1.6 s (alternate full / punched-in crops if
  single camera).
- Captions: shadow style, 1–3 words, one line, hard swaps, centre as low as the grid allows.
- 1 hook moment + 3–4 designed moments that illustrate the new lines + 2–3 word-by-word kinetic
  headlines on the punchiest sentences.
- Motion vocabulary: frame-shrink with motion blur, rack-focus blur → sharp, tinted
  cross-dissolve, 3D perspective camera moves on UI, highlighter sweep, typewriter text.
  Seek-safe GSAP only.
- The ending is decided by the content (a logo sting only with a logo and if the user wants it).
- Verify on the render: caption position/size, cut statistics, SFX only on transitions,
  silence between words.

The resulting `style/style.json`:

```json
{
  "meta": {"references": ["rollin-reel"]},
  "captions": {"style": "shadow", "max_words": 3, "weight": 500, "center_y": 1257},
  "brand": {"caption_size": 58},
  "style": {
    "target_cuts_per_30s": 12.1, "median_shot_s": 1.6, "broll_share": 0.22,
    "headline": "word-by-word kinetic on the key sentence, mixed weights, keyword in the accent colour, hard-cut exit",
    "transitions": "hard cuts on phrases; frame-shrink with motion blur into designed moments; tinted cross-dissolve out",
    "hook_notes": "first words land one by one on the chest, then the frame flies into a graphic world by 0.5 s",
    "sfx_per_min": 8,
    "notes": ["captions hard-swap, no animation", "hide captions during full-screen designed moments"],
    "palette": ["#1E8BFF", "#061224", "#F58A7E", "#2F5D50"]
  },
  "audio": {"music_db_under_voice": 12,
            "music_character": "sub-bass modern ambient bed, slow pulse; swells in the hook, drops before the punchline"}
}
```

`apply_style.py` then reports: `captions.center_y: 1257 → 1168 (CLAMPED, the grid beats the
reference)`, and with a client logo: `palette kept for reference only, brand colours win`.
