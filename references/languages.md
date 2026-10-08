# Languages — detection, direction, and what changes per language

The skill works in any language with no config edits. `config.example.json` ships
`language.code: "auto"` and `direction: "auto"`. The first pipeline step resolves both
from the take. Hebrew keeps its own rules (`references/hebrew.md`). Every other language
gets the same edit, laid out in its own reading direction.

---

## 1. Detection (the first step)

```bash
python3 $S/scripts/detect_language.py raw.mp4 --apply    # explicit
python3 $S/scripts/xcheck.py raw.mp4                     # …or implicit: it runs the above
                                                         # when the code is still "auto"
```

- **How it works:** Whisper's own language ID (faster-whisper `detect_language`) runs on
  the first ~30 s of *speech*. Silero VAD drops the silence and music first. The model is
  the best one already downloaded (large-v3 → large-v3-turbo → medium → small), else
  `small`, so a Hebrew user never pulls 3 GB just to be told "Hebrew". The result is
  cached by the file's fingerprint.
- **What it writes** into `config.json`: `language.code`, `language.direction` (`rtl` for
  he / ar / fa / ur / yi / ps / sd…, `ltr` otherwise) and `language.detected` (p, top 3,
  seconds of speech, model). Quote it in the report.
- **The gate:** it never guesses silently. If p < 0.70, or there is under 4 s of speech,
  it writes nothing, prints the top 3 and exits 2. `xcheck.py` and `transcribe.py` then
  stop too. **Ask the user** which language it is, then run:
  `detect_language.py --set <code>`.
- **Override:** `detect_language.py --set en` (any code; `pt-BR` works). You can also write
  `"language": {"code": "en"}` by hand. An explicit `direction` wins over the derived one.
- **`hfcfg.load()` always resolves the direction.** No script ever sees `"auto"` there.
  `build_index.py` and `captions.py` refuse to run while the code is still `"auto"`
  (`hfcfg.require_language`). Without that refusal, an undetected take would silently get
  the wrong caption rules and the wrong layout.

## 2. Transcription follows the language

| Language | Engine A (primary) | Engine B (cross-check) |
|---|---|---|
| Hebrew | ivrit-ai large-v3-turbo (faster-whisper) | mlx large-v3-turbo, else faster large-v3 |
| any other | mlx large-v3 (Apple Silicon) | faster-whisper large-v3 |
| any other, no mlx | faster-whisper large-v3 | faster-whisper large-v3-turbo |

ivrit-ai is a Hebrew fine-tune. On another language it reads the speech as Hebrew, so it
is refused there, even when asked for by name. The glossary becomes Whisper's initial
prompt. It only keeps terms written in the language's own script, plus Latin (brand names
are Latin everywhere). A Hebrew term in front of an English take pulls Whisper toward
Hebrew. The "AI" normalisation and the geresh merge are Hebrew-only.

## 3. Layout: what mirrors with the direction

Everything below is automatic: it follows `language.direction`. RTL output is
byte-identical to what it was before languages were generalised.

| Element | RTL (Hebrew, Arabic…) | LTR (English, Spanish…) |
|---|---|---|
| Kinetic headlines | flush right, hanging from x 920 (right margin 160); fit to 860 px | flush **left at x 140**; fit to 800 px (to x 940) |
| Widget header `lead · title · aside` | right → left | left → right |
| Chips, strike pills, week days, buttons | first item on the right | first item on the **left** |
| Strike-through, progress fill, fill gradient | grow right → left | grow **left → right** |
| Calendar event "postponed" | slides left; stamp lands left | slides **right**; stamp lands right |
| Chat bubbles | incoming on the right, tail bottom-right | incoming on the **left**, tail bottom-left |
| Checklist rows | slide in from the right (x +40) | slide in from the **left** (x −40) |
| Search bar | icon right, typing grows leftward | icon left, typing grows **rightward** |
| Paper page turn, `line` outro | the hairline sweeps right → left | sweeps **left → right** |
| Outro tagline | first (bold) word on the right | first word on the **left** |
| Captions | the plate is RTL; Latin runs isolated | plain text, no isolation |
| Centred things (captions, hook titles, cards, widgets, stamps, the outro lockup) | x 540 | x 540 (unchanged) |

**Why LTR headlines sit at x 140, not the mirror (left 160).** The like/comment rail makes
x 940 the hard right limit in both directions, so an LTR line can only grow to x 940.
Starting at x 140 does three things:

- it gives the widest line the same 800 px as the centred lane;
- it keeps the left margin equal to the gap the rail leaves on the right (140 px either
  side of the frame centre);
- it lines the stack up with every widget, caption plate and card above and below it.

A 160 px left margin would only cost 20 px of width, and it would line up with nothing.

Not mirrored, on purpose:

- The hook card `swing` and the widget `slide` enter from the right. That is the "next"
  direction in LTR, and the established house look in RTL.
- The avatar check badge and the hourglass sit top/bottom-right in both directions.

**Hebrew-only:**

- the "AI" Roboto Slab span. In heavy Hebrew faces a capital I reads "Al". Arabic isolates
  Latin but does not get the slab;
- the Hebrew week letters (other languages default to `M T W T F S S`; pass `days=` in
  the video's language);
- the prefix letters (ב/ל/ה…) in word matching and locked phrases.

## 4. Caption rules per language

`captions.py` keys its lists by the language's base code (`pt-BR` → `pt`):

- **Sticky words** (a card may not end on them): he, en, es, fr, de, pt, it and ar have
  lists. These are articles, prepositions, conjunctions, possessives and intensifiers. The
  lists deliberately leave out words that often *close* a clause (fr `pas`, de `nicht`,
  pt `não`). A false sticky only costs a worse split. A missing one reads broken.
- **Any other language** falls back to a generic rule: a bare 1-2 letter word may not end
  a card (`de`, `op`, `ve`, `di`…). This is skipped for scripts written without spaces
  (Thai, CJK).
- **Additions:** `language.sticky_words` adds to either the list or the generic rule.
  `clause_openers` and `locked_phrases` add too.
- **Lean-back words** (en `too`, `itself`; he `הזאת`, `מאוד`) never open a card.

## 5. Fonts

- **The licence gate is language-agnostic.** A second check, `fonts.script_issues()` (run
  by `preflight_qa.py`), fails a caption or display face with no glyphs for the take's
  script, e.g. Bebas Neue on Hebrew or Heebo on Russian. Missing letters render in
  whatever fallback the machine has.
- **Heebo** (the house face) sets both Hebrew and Latin.
- **For Latin languages:** `python3 scripts/fonts.py pair` (it follows the config's
  language) lists Latin-first pairings: Inter, Montserrat, Poppins, Bebas Neue, Oswald and
  Space Grotesk.

## 6. Talking to the user

- **Speak the user's language:** the one they write to you in. This may differ from the
  take's language: a Hebrew speaker can edit an English reel.
- **RTL replies:** every Latin term, path or command goes on its own line.
- **The report:** state the detected language and its confidence in one line ("English,
  99.8 %").
