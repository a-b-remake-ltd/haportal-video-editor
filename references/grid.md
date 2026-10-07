# The Reels grid

Every reel is placed against the Instagram Reels UI before anything else. The numbers
live in ONE place, `scripts/grid.py`. The caption slots, the caption-width limit, the
card geometry, the outro layout and the grid gate all read them from there. Never type
one of these numbers by hand into a composition.

All values are for a **1080 x 1920** frame and scale linearly with the frame. A
2160 x 3840 master gets the same zones doubled.

---

## The zones (profile `reels`, the default)

| zone | area | what covers it |
|---|---|---|
| hidden: top | y 0-220 | reel title, status bar |
| hidden: bottom | y 1520-1920 | username, caption text, audio line |
| hidden: rail | x 940-1080 at y 880-1520 | like / comment / share / audio column |
| **safe** | **x 60-940, y 220-1520** | every readable word and every logo |
| caption band | y 1110-1190 | the speaker-state captions |
| centred lane | x 140-940 | the widest box still centred on the frame (800 px) |
| 3:4 profile crop | y 240-1680 | what the profile grid shows as the thumbnail |

- **Centre on the FRAME, x 540 — one rule, one helper.** `grid.centered_box(width)`
  places every centred element: up to **800 px** wide it sits exactly on x 540 (its right
  edge stays ≤ 940, clear of the rail); only a wider element shifts **left**, just enough
  to keep its right edge on 940, and never past x 60. Above y 880 the rail is not there,
  but the rule is the same, so nothing jumps sideways as it moves down the frame.
  `grid.center_lane()` is the 800 px lane (x 140-940) that flowing content (captions, chip
  rows, big titles) is centred in.

  **Why.** Everything used to centre on the safe zone's middle, x 500, because the rail
  makes the safe zone asymmetric. Next to a speaker framed on the frame centre that reads
  as off-centre: the note after a real test reel was "the captions aren't centred, many
  elements aren't centred". A block up to 800 px wide can sit on 540 and still clear the
  rail, so it does; capping captions, cards and widgets at 800 px is cheaper than looking
  off-centre.
- **Kinetic headlines** hang right-aligned from x 920 (a 160 px right margin) in RTL: a
  design choice, the one exception to the centring rule.
- **Bottom cards** anchor to y 1520 and grow upward, ~300 px at most, so they stop
  below the caption band. In `media.json` a graphic with `"class": "card low"` is exactly
  this, and the speaker's face stays clear: the default card for a talking beat.
- **Cover / opening frame:** the title that sells the reel on the profile grid must
  read inside the 3:4 crop (y 240-1680). A title at y 200 is invisible on the grid.

## Profile `ads` (Meta's guidance for paid placements)

14 % top, 35 % bottom, 6 % on each side. That gives safe x 65-1015, y 269-1248. It is
stricter, so use it when the reel will run as a sponsored ad. Ask about it in the opening
conversation ("organic, or a paid ad too?"). Set it in `config.json`:
`"grid": {"profile": "ads"}`.

---

## What the grid constrains

It constrains **framing**, not just graphics. If the layout needs the speaker's face
low, the caption band and the face compete for the same pixels. Solve the crop (see
`references/layout.md` §reframing) so the face sits above the band, rather than
moving captions out of the band.

Full-frame footage, B-roll and background washes may bleed to every edge, because
nothing in them must be read. Mark a decorative element that is meant to bleed with
`data-grid="bleed"` and the gate skips it.

## The gate

```bash
python3 scripts/grid.py check index.html                 # every timed element, auto times
python3 scripts/grid.py check index.html --at 0.5,8.2    # specific moments
python3 scripts/grid.py overlay renders/draft.mp4 --at 0.4,3,9.5,21 -o build/grid.png
python3 scripts/grid.py show                             # print the numbers
```

`check` loads the composition in headless Chrome, seeks the GSAP timeline to each probe
time, and measures every visible text node, image and SVG. Anything that crosses into a
hidden zone fails, with its id, its text and its rectangle. It also checks the external
caption layer's slots. Two more gates:

- **Centring.** Every element marked `data-center` (the kit's widgets and hook cards,
  chip and pill rows, big titles, stamps, moment cards) must sit where `centered_box()`
  puts an element of its measured width, within ±4 px, at some probe time (mid-slide or
  mid-shake is fine; never on its mark is not). `data-center="content"` measures the union
  of the children (a centred row), a bare `data-center` the element itself.
- **Head clearance.** Every sky widget (`data-sky`) must end at least 20 px above the head
  top in `build/framing.json` when that file exists.

The caption layer has its own centring gate on the real pixels: `caption_layer.py`
measures each rendered plate's ink and fails one more than 4 px off x 540.

`python3 scripts/grid.py selftest` runs the rule and both gates against known-good and
known-bad rectangles. Run `check` before every render. A failure is fixed in `media.json`,
`scenes.py`, `beats.py` or the card CSS, never by hand in `index.html`.

`overlay` paints the zones over real frames: red for hidden, green for safe (a thin line
on the centre x 540, a faint box for the 800 px centred lane), yellow for the caption band,
cyan for the 3:4 crop. Put it in the contact sheet you show at every
milestone. It is the fastest way for a human to trust the layout.

## A reference cannot move the grid

A reference reel may move the caption band (`captions.center_y`). `apply_style.py`
clamps it so the plate stays inside the safe zone and clear of the bottom-card area,
and it reports the clamp. If the reference put its captions where the Reels UI covers
them, say so in one line. Never copy that.
