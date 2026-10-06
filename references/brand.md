# Brand colours from a logo

**The rule:** if the client gave a logo, the motion graphics, animations, cards and keyword
highlights take their colour from it. **Brand colours beat a reference palette.** A reference
video sets the rhythm, the layout and the type character; it never repaints the client's
brand.

---

## When to ask for a logo

At intake, if the reel is for a business or a personal brand: *"Do you have a logo? A
transparent PNG or SVG is best; a JPG on a white background works too."* A logo is also
required for the animated outro (`references/outro.md`). No logo means the house tokens
(teal / blue) and no brand outro.

## What to run

```bash
python3 scripts/brand_from_logo.py logo.png --out brand/
python3 scripts/brand_from_logo.py logo.svg --out brand/ --accent "#F2A93B"   # override
```

- PNG, JPG and WEBP are decoded by ffmpeg, using the standard library only.
- An SVG is parsed for its declared colours and rasterised by headless Chrome for pixel
  weights. Clusters snap to the designer's exact hex.
- An opaque logo (a JPG in a white box) has its background flood-filled away from the
  border and its edges un-matted, so it gets no white halo on dark footage.
- `--primary` / `--accent` override a role. Use them when the user says "our colour is X" or
  when the extraction picked the wrong one.

**Then LOOK at `brand/palette.png`.** It shows the ten swatches, the five text samples with
their measured contrast, and the three logo variants on their intended backgrounds. Every
sample that will be used must read.

Outputs:

| File | Use |
|---|---|
| `brand.css` | `:root` tokens; `build_index.py` inlines it after the house defaults |
| `brand.json` | Roles, palette, contrast table, notes, logo size/aspect/holes (read by the outro) |
| `logo_trim.png` | Full colour, transparent margins cropped |
| `logo_on_dark.png` | White silhouette, same alpha, enclosed white fills knocked out |
| `logo_on_light.png` | Ink silhouette, same alpha |

---

## The roles and how Claude must use them

| Token | Role | Use it for |
|---|---|---|
| `--hl-on-dark` | Highlight colour, at least 4.5:1 on black | **Kinetic keyword highlights on footage**, kickers and sub-lines on dark cards, glows. The workhorse. |
| `--hl-on-light` | Same hue, darkened to at least 4.5:1 on paper | **Any brand-coloured text on a light/paper scene.** Never put `--brand-primary` text on paper. |
| `--brand-primary` / `-rgb` | Most-used brand colour | Shapes, bars, rings, underlines, card borders, glows: `rgba(var(--brand-primary-rgb), .3)` |
| `--brand-secondary` / `-rgb` | Next distinct colour (or a tonal shade of the primary) | The second glow of the hook wash, secondary shapes, chart series 2 |
| `--brand-accent` / `-rgb` | Most vivid colour | **Sparingly: one accent per frame.** The single thing that must pop: a price, a tick, the payoff word |
| `--brand-ink` / `--brand-paper` | Darkest / lightest brand neutral | "Paper" scenes: paper background, ink text, `--hl-on-light` keywords |
| `--brand-on-primary` | Black or white, whichever reads on primary | Text on a primary-filled chip or button |
| `--brand-grad-a` → `--brand-grad-b` | Deep versions of the primary | Card backgrounds. White text stays at least 7:1 (a is at least 7.5:1, b at least 14:1) |

`--hl-on-dark` and `--hl-on-light` derive from the **primary** when it is vivid enough to carry
a word (chroma 35 or more). Otherwise they derive from the **accent**. A navy primary does not
highlight anything on footage, but its gold accent does. `brand.json → hl_base` records which
one was used, and the notes say why.

Colour rules in the composition:

- Use tokens only, never a hard-coded hex, in any card, highlight or shape you add. This is
  what makes a logo swap recolour the whole reel.
- `.hl` = `var(--hl-on-dark)`; `.on-light .hl` = `var(--hl-on-light)`. Mark every light
  surface with `.on-light`.
- One accent per frame. Two vivid colours fighting in one frame looks cheap.
- Cards: `linear-gradient(163deg, var(--brand-grad-a), var(--brand-grad-b))` with white text.

## Contrast rules

- **Text: at least 4.5:1** against what is actually behind it. A brand colour that fails is
  moved along L* with its hue kept until it passes. It is never swapped for a different
  colour.
- **The gold lesson.** A gold `#D2AE38` on a bone/cream background measures about 1.9:1 and
  was unreadable. The fix was a darker gold for text on light (`#8E6E16`). `colorkit` darkens
  with a correction that eases chroma and warms the hue, so dark yellow turns bronze instead of
  olive. It gives `#876A0F` on bone `#F4F0E8`.
- Cards keep white text at 7:1 or better. Large display words (100 px and up) may go down to
  3:1, but only when the palette sheet shows them reading.
- Footage is not black: on a bright shot, give a `--hl-on-dark` word a shadow or a dark plate.
- `brand.json → contrast` lists the measured pairs. Quote them when a reviewer asks.

## Monochrome logos

If the logo is only black, white or grey, `logo.monochrome` is `true` and `needs_accent` is
`true`. The script prints three suggestions. **Claude must ask the user for one accent
colour** and offer those three (electric blue `#2F6BFF`, warm amber `#F2A93B`, signal red
`#E5484D`) or the user's own. Then rerun with `--accent`. The provisional accent in the files
is a placeholder: never ship a reel built on it without asking.

A white mark on a brand-coloured square (a JPG) is the reverse case. The square's colour is
removed as background, then promoted to primary, and the notes say so.

## Holes (for the outro)

`brand.json → logo.holes`: enclosed transparent regions, plus enclosed white or background
fills, such as the counter of an "O". They are sorted largest and roundest first. Each hole
gives `cx, cy, r` (equivalent radius), `r_inscribed` (the circle that truly fits), `area`,
`roundness` (1.0 = a disc), `bbox`, `fill` and normalised `cx_n/cy_n/r_n`, all in
`logo_trim.png` pixels. The outro shrinks the speaker into a circle of `r_inscribed` and lands
it at `(cx, cy)`. An empty list means there is no hole, so use the `line` or `impact` outro
style.
