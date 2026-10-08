# Hebrew — the language-specific rules

Every rule below came from a delivered Hebrew video that came back with a note. They apply
when the detected language is Hebrew (`language.code: "he"`). What changes for other
languages and directions is in `references/languages.md`. Read `references/captions.md`
§RTL first. This file adds what Hebrew specifically breaks.

---

## Transcription

- **Vanilla Whisper is weak on Hebrew.** The default transcriber for `he` is the
  Hebrew-tuned `ivrit-ai/whisper-large-v3-turbo-ct2` (`scripts/transcribe.py`, engine
  `auto`). It is free and runs locally. ElevenLabs Scribe is a paid upgrade, never a
  requirement.
- **Give it a glossary.** Brand names, product names, English terms the speaker uses
  (`config.json` → `language.glossary`). Most "typos" are a missing glossary.
- **"AI" comes back as Hebrew nonsense**: "איי", "אי איי", "ליי", "באי ביי", "לאיה". The
  transcriber normalises the common forms to the literal `AI` and lists every
  substitution. Confirm each one against context.
- **Audit before captioning.** Read the whole transcript for phonetic mis-hearings of
  foreign or technical terms and for grammatically broken sentences. You cannot listen.
  Correct from context, then list every inferred correction to the user so they can
  confirm. Low-confidence words are listed in `src/transcript_flags.md`.
- **Doubtful span → second opinion.** Run `transcribe.py --compare` on the span (two
  engines) and take the reading that fits the sentence. Large-v3 beats turbo on hard
  spans.

## Captions

- **A card never ends on a sticky word.** Prepositions, conjunctions, relative words and
  bare numbers lean on what follows: של, את, על, עם, כי, אם, או, אבל, גם, רק, כמו, בלי,
  עד, מה, איך, למה, מי, כל, הכי, יותר, לא, יש, אין... and any digit.
  - Wrong: "תודה לך על מה" / "שעשית בשבילי"
  - Right: "תודה לך" / "על מה שעשית בשבילי"

  `scripts/captions.py` moves the trailing sticky word forward and fails loudly on any
  card it could not fix. Extend the list per project with `language.sticky_words`.
- **A number stays with its unit** ("3 עובדים", "50 אחוז"). Never "50" / "אחוז".
- **Latin inside a Hebrew line is isolated**, or bidi reorders the sentence:
  `<span class="ltr">ChatGPT</span>`. The caption builder does it automatically. In
  designed graphics, give each Latin token its own block element instead.
- **"AI" reads as "Al"** in heavy Hebrew faces such as Heebo 800, because a capital I has
  no serif and looks exactly like a lowercase l. Every all-caps Latin acronym containing
  an I gets `class="ltr ai"` and is set in **Roboto Slab 800** (free, OFL). Check it at
  real resolution on a rendered frame. The bug is invisible in the HTML.
- **Hebrew quotation marks** for reported speech: "...". Use the gershayim ״ only in
  abbreviations (צה״ל), never as quotation marks.
- **No mixed-direction punctuation at line ends.** A question mark belongs at the end of
  the Hebrew line. If it renders at the start, an unisolated Latin token is the cause.

## Cutting Hebrew speech

- **Never cut the end of a word.** Hebrew sentences often end on a soft syllable: a nasal
  ן/ם, a voiceless ה/ת, or a falling intonation. A word-end detector that waits for a
  loud offset clips them. Protect the final word of each phrase by its transcript end
  plus a "soft voice" test: low band strong, high band weak. Breath is the opposite,
  high-band noise with no low band.
- **Breath after a cut is a defect.** Detect speech onset by voice energy in the
  100-900 Hz band, not by overall level. Overall level at −36/−40 dB takes the breath
  as speech. Let a segment start at most 0.15 s before its first word. Extend backward
  up to 0.1 s only for strong sibilant onsets (ש, ס, צ).
- **Lip sync breaks silently at concat.** Re-time every segment
  (`setpts=N/(fps*TB)`, `asetpts=N/SR/TB`) so each starts at PTS 0. Otherwise the concat
  demuxer adds a gap at every join, and those gaps add up to half a second by the end.
  Gate it: zero irregular timestamps, frame count == plan.
- **Verify on the rendered file.** Re-transcribe the final mix, voice plus SFX, and
  compare it with the source transcript word by word. An SFX on a short word swallows it
  ("סבתא" came out as "ספטו"). Move the effect to the word's end, or duck it.

## Typography

- Hebrew display type reads best **large and heavy** at phone size: weights 700-900 for
  captions and keywords, 300 for the framing words in a mixed-weight headline.
- Free Hebrew faces only (`scripts/fonts.py list --hebrew`). Many popular Israeli faces
  are commercial, such as Narkis, Guttman and the Fontbit (Fb*) families. The font gate
  refuses them.
- Hebrew has no capitals: emphasis comes from weight and colour, never from case.

## Talking to the user

Talk to the user in plain, short Hebrew, one question at a time when something is
blocking. Put every Latin term, path and command on its own line or in a code block,
never mid-sentence. Hebrew lines with embedded Latin render scrambled in many
terminals.
