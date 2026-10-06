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
| 3:4 profile crop | y 240-1680 | what the profile grid shows as the thumbnail |

- **The horizontal centre is x 500, not 540.** The rail eats the right side, so the safe
  zone is asymmetric. Centre captions, logos, headlines and cards on x 500. A block
  centred on 540 loses its last letters under the buttons.
- **Kinetic headlines** hang right-aligned from x 920 (a 160 px right margin) in RTL.
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
caption layer's slots. Run it before every render. A failure is fixed in `media.json`,
`beats.py` or the card CSS, never by hand in `index.html`.

`overlay` paints the zones over real frames: red for hidden, green for safe, yellow for
the caption band, cyan for the 3:4 crop. Put it in the contact sheet you show at every
milestone. It is the fastest way for a human to trust the layout.

## A reference cannot move the grid

A reference reel may move the caption band (`captions.center_y`). `apply_style.py`
clamps it so the plate stays inside the safe zone and clear of the bottom-card area,
and it reports the clamp. If the reference put its captions where the Reels UI covers
them, say so in one line. Never copy that.
