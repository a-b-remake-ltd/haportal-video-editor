# The widget kit — API

`scripts/kit.py` (components, motion, hook), `scripts/scenes.py` (the ctx, loading,
validation, SFX), `templates/kit/*.css` (the look). The method for deciding WHAT to build is
`references/storyboard.md`; this file is HOW.

```bash
python3 $S/scripts/scenes.py example > scenes.py   # a starting file (invented script)
python3 $S/scripts/scenes.py words [a b]           # transcript with times, to author from
python3 $S/scripts/scenes.py list                  # components, recipes, icons
python3 $S/scripts/scenes.py plan                  # dry run: scene table, SFX moves, warnings
python3 $S/scripts/build_index.py                  # emits the `# scenes` section
```

---

## 1. The project file: `scenes.py`

In the project folder (next to `media.json`). One function:

```python
def build(ctx):
    k = ctx.kit
    s = ctx.scene("inbox", ctx.t("nobody"), ctx.te("writes") + 0.4)
    s.add(k.widget(s, k.empty(s, "No new messages", "since Monday"), title="Inbox",
                   aside=k.spinner(s)))
    return [s.done()]
```

It returns a list of **fragments** (`Scene.done()` makes one; `kit.hook` returns several):

```
{id, start, end, html, css, js[list], sfx[{name, t, kind: "exempt"|"normal", base_vol}],
 hide_captions: bool | [[a, b]], footage: bool | [[a, b]], punch_ok: bool, z, cls}
```

`build_index.py` wraps each in one clip (`<div id class="clip kt-scene" data-start
data-duration data-track-index="80+lane" style="z-index">`), adds its SFX as audio clips on
lanes 90+, feeds its caption-hide windows to `build/caption_hide.json`, emits the kit CSS and
the motion vocabulary once, and writes `build/scenes.json` (the scene table and every SFX cue,
for QA stills). Nothing else in the composition changes.

### The JSON shortcut: media.json "scenes"

For simple cases, no Python: a kit **recipe** per entry. Times are seconds or spoken words
(`"word"`, `"word@2"` = second occurrence, `"word@2+0.1"`).

```json
"scenes": [
  {"recipe": "waiting_room", "id": "wr", "start": "waiting", "end": "come+0.5",
   "title": "Waiting room", "wait": "Waiting for the host…", "fail": "The host will not join",
   "fail_t": "never", "leave": "Leave", "leave_t": "nobody"},
  {"recipe": "bars", "id": "trap", "start": 9.1, "end": 10.7, "slam_t": "trap", "lift_t": 10.36}
]
```

Recipes: `week phone inbox waiting_room dialog task calendar today notify chat strike percent
streak bars puppet leak bricks stamp glow` (`scenes.py list` prints each signature).

---

## 2. `ctx`

| Member | What |
|---|---|
| `ctx.t(word, n=1, after=None)` | spoken START of the n-th occurrence (a word or a phrase). Hebrew prefixes ו ה ב ל מ ש כ are tolerated, so "אתם" also matches "שאתם": anchor with `after=` when it matters |
| `ctx.te(word, n=1, after=None)` | its spoken END |
| `ctx.span(a, b=None, n=1)` | (start of a, end of b after it), or of the phrase a |
| `ctx.at(v)` | seconds pass through; `"word@2+0.1"` resolves |
| `ctx.say(a, b)` | the words spoken in [a, b) |
| `ctx.scene(id, start, end, layer="front", z=None, punch_ok=False)` | a `Scene` |
| `ctx.punch([(t, factor), ...])` | punch-in steps (`tl.set` on the footage, factor × the beat scale, 1.00-1.16). **Declare first**: the hook lands on the punch active at its landing |
| `ctx.state_at(t)` | the footage's (scale, y) at t: beat map × punches × pushes |
| `ctx.framing` | `build/framing.json`: `head_top, face_cx, face_cy, chin, chest [y0, y1], free_zones`; defaults when absent (`framing["source"]` says which) |
| `ctx.G`, `ctx.safe`, `ctx.sky` | the Reels grid (`grid.py`); sky = left 90, right 170, top 250, bottom 600 |
| `ctx.tokens`, `ctx.brand` | the colour roles; `brand/brand.json` or None |
| `ctx.rng(seed)` | seeded `random.Random` for positions (never `Math.random`) |
| `ctx.note(msg)` | a warning printed with the build |

Layers (z): `world` 10 < `card` 13 < the A-roll 20 < `tint` 21 < `front` 45 (default) < `top` 47.

---

## 3. `Scene` — motion and camera

Selectors: a local name (`"w"`) means `#<scene id>-w`; `#…`, `.…` are literal. Every call is
a seek-safe `fromTo(…, immediateRender:false)`; hidden starting states live in CSS.

| Method | Motion (spec §4.3) |
|---|---|
| `s.word(sel, t)` | opacity .18 + blur 6 + grey → resolved, 0.22 s (headline words) |
| `s.pop(sel, t, d=.5)` / `s.drop(sel, t, d=.55)` / `s.away(sel, t)` | scale .5 → 1 / y −60 → 0 with blur, expo.out / y −40 + blur 12 out, power3.in |
| `s.slide(sel, t, dx=300)` / `s.rise(sel, t, dy=260, s=.86)` / `s.swing(sel, t, dx=600)` | from the side / from below / from the right turning (rotationY −18) |
| `s.fade(sel, t, d, a, b)` | opacity a → b |
| `s.steps(sel, [(t, k), ...])` | show child k of a state stack from t (`swap` uses it) |
| `s.shake(sel, t, amp=14, axis="y")` | ±amp for 0.06 s, yoyo ×3 |
| `s.spin(sel, t0, t1)` | linear rotation for the widget's whole life |
| `s.strike(bar, pill, t)` | RTL red bar scaleX 0 → 1 in 0.22 s, then the pill dims to .45 |
| `s.stamp(sel, t, rot=-6)` | scale 2.3, rot −16 → 1, rot −6 in 0.18 s power4.in |
| `s.draw(sel, t, d=.5)` | stroke-dashoffset 100 → 0 (paths with `pathLength="100"`) |
| `s.push(sel, t, d=.6, a=1, b=1.08)` / `s.drift(sel, t0, t1, b=1.04)` / `s.float(sel, t0, t1)` / `s.pulse(sel, t)` / `s.tap(sel, t)` | slow push / ambient drift / float / pulse / press |
| `s.set(sel, props, t)` / `s.tween(sel, frm, to, t, d, ease, **kw)` / `s.js(line)` | escape hatches (FROM must equal what is on screen) |
| `s.cam_shake(t)` | impact shake of the footage (records a camera window) |
| `s.cam_push(t, d=.6, k=1.08)` | slow push on an emotional beat; holds until the next punch step |
| `s.cam_sway(t, n=5)` | frame sway, scale lifted to ≥ 1.07 so no black corners show |
| `s.sfx(name, t, kind="normal", vol=.2)` | an effect; `exempt` impacts stay on the beat, `normal` ones are slid off words |
| `s.hide(a=None, b=None)` | hide captions (default: the whole scene) |
| `s.add(html)`, `s.css(text)`, `s.uid(name)`, `s.n(prefix)` | content, scene CSS, ids |

Two scenes may never move the footage at the same time, and a punch step may not land inside
a camera move: the build stops with the two ids and times.

---

## 4. Components

All return `Html`. Text arguments are escaped, Latin runs isolated, "AI" slabbed; pass
`kit.html("<b>…</b>")` for trusted markup. Times accept seconds or words.

### Shells

```python
k.widget(s, body, title=None, sub=None, lead=None, aside=None, eyebrow=None, eyebrow_icon=None,
         t_in=None, t_out=None, enter="drop", top=None, name="w", sfx="soft_whoosh")
```
The glass sky widget (left 90, right 170, top 250; glass .84). Header in RTL order: `lead`
(avatar/badge), title + sub, `aside` (spinner/pill); `eyebrow` = small icon + label instead.
Enters at `t_in` (default scene start) with `drop` | `slide` | `pop` | `none`, leaves with
`away` at `t_out` (default end − 0.3; `False` = hard cut).

```python
k.card(s, body, head, meta=None, meta_tone="")      # the dark hook card (used by hook_card)
k.panel(s, body, top=..., left=..., width=..., t_in=None, enter="fade")  # free-positioned; enters on its own (fade|pop|drop|none)
```

### State and status

```python
k.swap(s, ["Open", "In progress"], at=[ctx.t("now")], tones=None)   # stacked states
k.pill(s, [("wait", "Pending"), ("ok", "Approved")], at=[ctx.t("yes")])  # mute|wait|ok|bad|info
k.spinner(s, t0=None, t1=None, size=54, kind="refresh"|"ring")       # spins its whole life
k.progress(s, "Progress", values=[(t, "2%"), (t2, "3%")], fills=[(t, d, .03), ...], warm=False)
k.percent(s, values=[(t, "10%"), (t2, "20%")] | count=(t0, t1, 0, 100), top=None)  # Rollin, on the chest
k.empty(s, "No new messages", "still waiting", icon_name="inbox")
```

### UI widgets

```python
k.avatars(s, picks=[t1, t2, ...], odd="you?", odd_t=t)          # checks one by one, one left out
k.week(s, hit=4, t0=None, step=.13, pulse_t=None, days=None)      # days light up to day `hit`
k.phone(s, "The next model", "Pre-order")                          # outline + filling bar + spinner
k.calendar(s, "The perfect day", labels=["Planned", "Postponed", "Postponed again"],
           moves=[t1, t2], fly_t=t3, never="Never", never_t=t4)   # event slides a day per move
k.today(s, "Today", ring_t, big="17")                              # page + hand-drawn red circle
k.dialog(s, "Approval request", "Waiting for someone else…", "You decided", "Wait", "Decide",
         "Decided", show_t, tap_t)                                 # buttons appear, hand taps, green ✓
k.task(s, "Your success", flip_t, who=("Unassigned", "You"),
       status=(("mute", "Open"), ("ok", "In progress")), label="Owner", pulse_t=None)
k.waiting_room(s, "Waiting room", "Waiting for the host…", "The host will not join", fail_t,
               leave="Leave", leave_t=None, press_t=None)          # flips red + shakes
k.notify(s, "Reminder", "Title", "one line", meta="now", icon_name="bell")
k.chat(s, [("in", "blah blah", t1), ("out", "reply", t2)], typing=(t0, t1))
k.strike_pills(s, [("waiting", t_in, t_strike), ("blaming", t_in2, t_strike2)])
k.stamp(s, "Never", t, x=None, y=None, rot=-8, size=96, tone="red"|"green"|"blue")
k.hand(s, t_in, t_tap, style="left:40%;top:60%")                   # the tap cursor alone
k.stack(s, "thin words / *keyword*", top=None, size=104)           # word-by-word stacked opener
k.icon(name, size=60, color="var(--blue)", sw=2.1)                 # the in-house stroke icon set
```
Markup for `stack`/`hook(intro=…)`: ` / ` breaks a line, `*bold blue*`, `^light blue^`,
`+bold white+`, `=gradient=`, plain = thin.

Icons (24 × 24 strokes, drawn for this kit): user users check x plus hourglass inbox refresh
ladder calendar clock bell lock unlock phone cursor arrow back up down spark chat mail send cart
play pause star heart trophy fire rocket brain code robot search home chart money eye key mic
flag doc gift door link.

### Full-frame overlays (decoration carries `data-grid="bleed"`)

```python
k.bars(s, slam_t=t, lift_t=None)            # plant: slam (first bar lands ON slam_t) + shake
k.bars(s, fade_t=t, burst_t=t2)             # payoff: fade back, burst outward with sparks
k.puppet(s, drop_t, sway_t=None, snap_t=None)   # control bar + strings to shoulders/head; sway
                                                # moves the camera too; snap = the payoff
k.light_leak(s, t, d=1.8, twinkles=14)      # warm leak sweeping across + twinkles
k.streak(s, t, d=.75)                       # Rollin light streak orbiting the speaker
k.bricks(s, t, glow_t=None, shape="arch"|"wall"|"brand"|[(x, y, rot), ...])
k.glow_ring(s, t, d=None)                   # halo + soft ring around the speaker
k.sparks(s, t, n=18, seed=11)               # seeded particle burst
```
`shape="brand"` rasterises the logo mark from `brand/brand.json` into bricks when the mark is
compact (aspect 0.5-1.6); a wide wordmark falls back to the arch, with a note.

### The hook world (spec §4.4)

```python
a = k.hook_card(ctx, "hk-a", start, end, big="the gradient title", head="Card title",
                meta="small right text" | k.spinner(a) | kit.html(...), meta_tone="wait",
                enter=None, title_t=None)
a.add(...components built on `a`...)
frags = k.hook(ctx, [a, b, c], out=t_out, back=t_back, intro="opening / *words*")
```
- `intro` lands word by word on the chest from 0 to `out` (captions hidden).
- At `out` the footage flies away: scale .34, y −900, radius 60, blur 14, 0.32 s power3.in
  (the y is corrected for the A-roll's own transform-origin so the geometry matches the spec).
- The world (radial `--world`) fades in and drifts 1 → 1.12; cards enter rise / swing / lift by
  default, titles 0.3 s after each card (`title_t` pins one to a word), everything drifts.
- At `back` the footage returns from scale .4, y −700, blur 16 to the punch scale active at
  `back + 0.38`, through a blue screen tint fading .9 → 0; the world fades 0.35 s later.
- The last card must run to `back + 0.38` (it sits under the landing frame). A punch step
  inside `out … back + 0.38` stops the build.

---

## 5. Colour tokens

Defined once in `:root` (house palette, spec §4.1) and DERIVED from `brand/brand.json` when a
logo exists: `--blue` (keyword) = `hl_on_dark`, `--blue2` = its light tint, `--eblue` =
`primary` (if it has colour), `--lav`/`--pink` from `secondary`/`accent`, the world from the
brand gradient pair. `--green --red --amber` stay semantic. Also: `--violet`, `--p1…--p5`
(people), `--wood --wood2 --warm`, `--glass --glass-edge --card --card-head --card-edge
--grad --world`. Use tokens in any CSS a scene adds (`s.css(...)`), never a hex.

---

## 6. Rules the kit enforces (and why)

| Rule | Why |
|---|---|
| ids prefixed by the scene, classes prefixed `kt-` | a shared class once stacked a tagline's words on top of each other |
| no CSS transform on tweened elements | `gsap_css_transform_conflict`; hidden starts are `opacity:0` |
| state changes via `swap`/`steps`, never `textContent` | text tweens are not seek-safe |
| positions seeded in Python | `Math.random` at render time differs per frame/worker |
| heavy elements counted (warn > 36) | > ~40 blur/radial/clip-path elements render black |
| two widgets overlapping → note | premium restraint: one idea at a time |
| a state change outside its scene → note | usually `ctx.t` found an earlier occurrence |
| SFX: moved ≤ 0.3 s off a word, else halved; exempt impacts stay | an effect on a short word swallows it; a tap heard 0.4 s late reads as a bug |
| missing SFX → nearest library effect, with a note | custom effects per video live in `assets/sfx/` (`references/sound.md`) |
