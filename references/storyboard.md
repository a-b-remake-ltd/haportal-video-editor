# The storyboard method (do this on EVERY video)

The edits this skill is judged against do not come from a menu of effects. They come from
**authoring**: every line of the script gets looked at, and the strongest lines get a literal,
witty UI moment that the viewer understands in under a second, landing on the exact word. The
user of this skill will usually never send a correction or a reference, so this is the
default, not an option. Write the storyboard table FIRST, then build it in the project's
`scenes.py` on the kit (`references/kit.md`). The house spec this method belongs to, with every
number, is [house-spec.md](house-spec.md) (§3 is the storyboard).

---

## 1. The principles (the taste layer)

1. **Premium restraint.** One idea on screen at a time. Generous negative space. Never two
   widgets competing (the build warns when two sky widgets overlap). Glass, not neon.
2. **Literal, concrete, witty.** Illustrate the sentence being spoken with a UI the viewer
   already knows: an inbox, a calendar, a task card, a progress bar, a waiting room, an
   approval dialog, a chat thread, a notification. Not abstract glowing shapes, not stock
   "tech" visuals. A light comic twist where it fits (§5). Never cringe.
3. **Derive, don't copy.** Every object and every word on a widget comes from THIS script.
   Keep the language (rhythm, type, motion, sound, layout), invent the objects. The examples
   here are examples.
4. **Callbacks.** Plant a visual early and pay it off at the end: the same object returns and
   changes state (bars slam on "the trap", the same bars burst on "that is why you are free";
   strings appear on "someone else decides", the same strings snap on "the power is yours").
   One or two per video make it feel authored.
5. **Something changes every 2-4 seconds.** A widget, a headline, a punch-in, a caption swap,
   a state change inside a widget. No stretch frozen longer than ~0.6 s (QA checks it).
6. **Sync to words, not to guesses.** Every visual beat lands within 0.1 s of its word. Use
   `ctx.t("word")`, never a typed second. `python3 $S/scripts/scenes.py words` prints the
   transcript with times.
7. **Brand at the end, not throughout.** The body is about the message; the brand lands in the
   outro. Brand colours tint the kit (tokens), but no logos inside the body.

---

## 2. Before building: the framing map and the transcript

- `build/framing.json` (head top, face centre, chin, chest band, free zones) decides where
  things go: widgets in the free sky (y 230-600), headlines on the chest, overlays anchored to
  the shoulders and head. The kit reads it (`ctx.framing`); without it, defaults assume a
  centred selfie framing.
- Read the transcript in full, then once more marking: the hook sentences (first ~6 s), the
  turn (where the story flips from problem to answer), the punchlines, the closing line.

---

## 3. The storyboard table (write it, then build it)

One row per line (or phrase). Columns:

| Time | Words | On screen | Captions | Punch | SFX |
|---|---|---|---|---|---|
| 0.00-1.20 | the opening words | thin word-by-word opener on the chest | off | — | — |
| 1.20 | (hook) | frame flies into the world | off | — | whoosh impact |
| ... | ... | widget / overlay / headline / plain | on / off | 1.0 / 1.08 / 1.12 | the effect |

Rules for the "on screen" column:
- Name the object AND what changes in it, AND on which word: "calendar; the event slides one
  day later on 'tomorrow', 'next week', 'next month'; red stamp on 'never'".
- Mark which rows are **plants** and which are **payoffs** of a callback.
- Plain captions are a valid choice for a row. Not every line gets a widget; the strongest do.

The table lives in ONE place: `storyboard.md` in the project folder (SKILL.md step 5). A
short copy at the top of `scenes.py` as its docstring is optional, as a reading aid next to
the code; when the two disagree, `storyboard.md` wins and the docstring is updated.

---

## 4. Structure and density (a ~55 s monologue)

- **Hook (0 to ~6 s).** The first words land word by word as a thin headline on the chest
  (0 to ~1 s). Then `kit.hook`: the frame flies up into the brand world; **2-3 cards**, one
  per hook sentence, ~1.6-1.8 s each, each a literal widget with a big gradient title under
  it; then the frame flies back through the blue tint. The speaker is away ≤ ~5 s. Captions
  are hidden throughout.
- **Body.** **8-12 designed moments** (sky widgets or full-frame overlays) plus **5-7 kinetic
  headlines** (`kinetic.py`, media.json "headlines") on the punchiest phrases. Plain captions
  fill the rest. Punch-ins every 2-4 s on phrase boundaries (`ctx.punch`), bigger (1.12-1.14)
  on the key words.
- **Turn.** The music drop lands on the story's turning line; the visuals can turn too
  (problem widgets in grey/amber, answer widgets in electric blue/green).
- **Ending.** Pay off the callback on the final line, then the outro.
- **At least one callback.** Check it in the table before building.

---

## 5. A catalogue: kinds of lines → widget ideas

Start here, then invent. The kit has a component for every row; the comic twist column is
where the authorship shows.

| The line is about… | Widget idea (kit component) | A twist that works |
|---|---|---|
| Waiting, being kept waiting | hourglass, waiting room (`waiting_room`), spinner that never stops, progress crawling 2%→3% (`progress`) | the host "will not join"; the bar moves 1% after a long wait |
| A promise, permission, approval | approval dialog with two buttons + tap cursor (`dialog`) | the viewer approves themself; "approved by: you" |
| Time, deadlines, "someday" | week strip lighting up (`week`), calendar with an event that keeps sliding (`calendar`), "today" page with a marker circle (`today`) | "postponed", "postponed again"; a red stamp "never" |
| Growth, progress, results | percent counter on the chest (`percent`), progress bar, a chart line (`icon("chart")` + custom path) | 0% → 3% on "years"; then 100% on the turn |
| Being chosen, comparison | avatar row getting green checks, one dashed avatar left out (`avatars`) | the left-out one is labelled "you?" |
| Nobody, everyone, the crowd | empty inbox (`empty`), a crowd of avatars, a chat with no reply (`chat`) | "no new messages"; typing… then nothing |
| Being controlled | puppet control bar + strings to the shoulders (`puppet`) | the camera sways with the bar; later the strings snap |
| Being trapped, limits | steel prison bars slam (`bars`) | callback: the same bars burst with sparks |
| Building, starting | bricks falling into a symbol, the brand mark (`bricks`) | the last brick lands on the keyword; light inside |
| Stopping a habit | glass pills that get a red strike-through (`strike_pills`) | strike lands on the word "stop" |
| Responsibility, ownership | task card: owner "unassigned" → "you" chip, status open → in progress (`task`) | the chip flips on "you" |
| A message, news, an alert | notification banner (`notify`), chat bubbles (`chat`) | the notification is from the future self |
| Searching, asking | search bar typing (moments `searchbar`), comment card (moments `question`) | the query in the speaker's exact clumsy words |
| Money, buying | cart, pre-order with progress (`phone`), price stamp (`stamp`) | "sold out" stamp; pre-order for something absurd |
| A verdict, a claim | rubber stamp (`stamp`) | slams on the word, small camera shake |
| Something beautiful, a turn | warm light leak + twinkles (`light_leak`), light streak orbit (`streak`) | the streak arrives exactly on the turn word |
| Power, freedom, the payoff | glow ring around the speaker + slow push (`glow_ring`, `cam_push`) | the callback object breaks at the same moment |

### How to be funny without cringe
- The joke is **in the UI's own logic**, never in a caption: a button the viewer presses for
  themself, an event that keeps moving, a progress bar that is honest, a host who never comes.
- **Understate.** One small wrong detail beats an exaggerated one ("3%" is funnier than "0%").
- **Agree with the line.** The twist sharpens the speaker's point; it never mocks the speaker
  or the viewer's pain. If the line is sad or serious, skip the twist.
- **Use the real words.** Labels are the speaker's own phrasing, shortened. No slang the
  speaker would not use. No emoji, no dashes in on-screen copy (number ranges excepted).

---

## 6. Timing rules (all enforced or checked by the build)

- A widget enters ~0.05 s after the first word of its line and leaves ~0.3 s before the next
  idea (`widget` defaults). A state change lands ON its word (`ctx.t`).
- Hook cards: enter differently (from below, from the right with a turn, from below), title
  0.3 s after the card, ambient drift on everything.
- Punch-ins never inside a camera move (hook, bars shake, sway, push): the build refuses it.
- Captions: the hook, every headline and any scene that carries the words itself
  (`s.hide()`) hide them. The external caption layer reads `build/caption_hide.json`.
- SFX: one effect on every designed transition (`s.sfx`); never on a word (normal effects are
  slid off words, impacts are marked `exempt`). Do not put effects on captions or headlines.
- Heavy elements (glass, blur, radial gradients) under ~40 in the whole composition; the build
  counts them.

---

## 7. A worked example (an invented script)

The script (Hebrew-first users: the same method, any language):

> "Everyone tells you to wait for the right moment. For the right job. For someone to finally
> say yes. I waited three years for that. Three years of 'next quarter'. And nobody came.
> Then one night I stopped asking. I just pressed send. And that click? That was the whole
> plan. The right moment is not coming. It is the one you are in."

The table:

| Time | Words | On screen | Captions | Punch | SFX |
|---|---|---|---|---|---|
| 0.0-1.4 | Everyone tells you to wait | thin opener, "*wait*" bold blue | off | — | — |
| 1.4 | (hook) | frame flies away | off | — | whoosh impact |
| 1.4-3.6 | for the right moment, for the right job | card "Inbox": empty tray, spinner, "No new offers"; title "the right job" | off | — | pop |
| 3.6-6.0 | for someone to finally say yes | card "Team pick": avatars get checks on "finally"/"say"; dashed "you?" with hourglass; title "say yes" | off | — | soft whoosh |
| 5.6 | (return) | frame back through the tint | — | 1.08 | whoosh impact |
| 6.3-8.0 | I waited three years | sky: "Three years" progress 1% → 2% (**the twist: honest progress**) | on | 1.0 | soft whoosh |
| 8.0-10.4 | Three years of 'next quarter' | calendar: event "The right moment" slides on each "next"; **plant: stamp "postponed"** | on | 1.12 on "quarter" | swap pops |
| 10.4-11.8 | And nobody came | waiting room flips to "The host will not join", shakes | on | 1.0 | message |
| 12.0-13.6 | Then one night I stopped asking | headline "I stopped / *asking.*" | off | 1.1 | — |
| 13.8-16.0 | I just pressed send | dialog "Send request / waiting for approval…"; the hand taps "Send", it turns green "Sent ✓" (**the viewer approves themself**) | on | 1.0 | click (exempt), ding |
| 16.2-18.4 | And that click? That was the whole plan | headline "That was / *the whole plan.*" + light streak orbit on "plan" | off | 1.14 | whoosh high |
| 18.6-21.0 | The right moment is not coming | **payoff:** the calendar returns, the event flies off, red stamp "never" | on | 1.0 | stamp |
| 21.0-23.0 | It is the one you are in | "today" page, marker circle drawn on "in"; glow ring + slow push | off (headline) | push | riser |

Density check: hook with 2 cards; 7 body moments in 23 s (scales to ~12 in 55 s); 2
headlines; one callback (the calendar: postponed → never); something changes every ≤ 2.5 s.

The build (excerpt of `scenes.py`):

```python
def build(ctx):
    k = ctx.kit
    out = [ctx.punch([(ctx.t("waited"), 1.0), (ctx.t("quarter"), 1.12), (ctx.t("nobody"), 1.0)])]

    a = k.hook_card(ctx, "hk-a", ctx.t("for"), ctx.t("for", 3), big="the right job",
                    head="Inbox", meta="Updated now")
    a.add(k.empty(a, "No new offers", "still waiting"))
    b = k.hook_card(ctx, "hk-b", ctx.t("for", 3), ctx.te("yes") + 0.48, big="say yes",
                    head="Team pick", meta="Pending", meta_tone="wait")
    b.add(k.avatars(b, [ctx.t("finally"), ctx.t("say")], odd="you?", odd_t=ctx.t("yes")))
    out += k.hook(ctx, [a, b], out=ctx.te("wait") + 0.02, back=ctx.te("yes") + 0.1,
                  intro="Everyone tells you / to *wait*")

    s = ctx.scene("send", ctx.t("just"), ctx.te("send") + 0.8)
    s.add(k.widget(s, k.dialog(s, "Send request", "Waiting for approval…", "Sent by you",
                               "Wait", "Send", "Sent", show_t=ctx.t("pressed"),
                               tap_t=ctx.t("send"))))
    out.append(s.done())
    return out
```

Then: `build_index.py` → gates (`validate.py`, `grid.py check`, `fonts.py guard`,
`npx hyperframes check`) → draft render → pull frames every 0.1-0.2 s through every scene and
LOOK at them (grid, overlaps, words before they are spoken, grey glass, static stretches).

---

## 8. Self-check before you build

- [ ] Every row of the table names an object, its change, and the word it lands on.
- [ ] Hook: opener + 2-3 literal cards + return; ≤ 5 s off the speaker.
- [ ] 8-12 designed moments, 5-7 headlines, ≥ 1 callback (plant and payoff both in the table).
- [ ] No two widgets on screen at once; nothing static > 0.6 s.
- [ ] Every label is the speaker's own words, short; no dashes, no emoji.
- [ ] One twist at most per moment, and only where the line allows it.
