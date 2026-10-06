# Fonts: free faces only

**The rule:** every face that can reach a render is licensed for free commercial use *and*
embedding: SIL Open Font License 1.1, Apache 2.0 or the Ubuntu Font Licence. That means
nothing installed "just on this machine", no paid foundry face, and no commercial name as a
CSS fallback either.

**Why:** the people using this skill publish commercial videos: ads, client reels, paid
content. A font burned into a frame is a published use of that font. Arial, Helvetica,
Futura, Gotham, Avenir, SF Pro, Segoe UI, Narkis, Guttman and the Fontbit `Fb*` catalogue are
licensed to *a machine* or *a buyer*, not to whoever ends up publishing the video. A fallback
is not harmless either. Whenever the free face misses a glyph, Chrome renders that glyph in
the next family in the stack, and the result is published just as visibly. The point is to
protect the user, so this is a hard requirement.

Everything lives in `scripts/fonts.py`.

---

## Commands

```bash
python3 scripts/fonts.py list                    # all 30 families, licence, weights, roles
python3 scripts/fonts.py list --hebrew           # the 20 with real Hebrew glyphs
python3 scripts/fonts.py list --role headline -v # by role, with notes
python3 scripts/fonts.py pair                    # proven Hebrew pairings
python3 scripts/fonts.py fetch "Secular One"     # download + licence + lock record
python3 scripts/fonts.py css Heebo "Secular One" --font-dir assets/fonts   # @font-face CSS
python3 scripts/fonts.py guard index.html brand/brand.css --font-dir assets/fonts
```

`setup_assets.py` always fetches **Heebo** (caption face), **Roboto Slab** (Latin acronyms)
and **Inter** (Latin fallback). It also fetches the project's `brand.font_family` and
`brand.display_family` when a `config.json` is present. Pass `--font NAME` for more.

Variable families are saved as `<Family>.ttf` (e.g. `Heebo.ttf`, `Roboto Slab.ttf`). That is
the convention `caption_layer.font_faces` reads as "one variable file, give it the full weight
range". Static families keep the upstream per-weight names (`Alef-Bold.ttf`), and the suffix
carries the weight.

Each fetch writes three things to the font dir:

- the font file(s)
- the licence text, e.g. `Heebo-OFL.txt` or `Roboto Slab-LICENSE.txt`
- a record in `fonts.lock.json`: family, licence, licence URL, source URL and sha256 of every
  file

---

## How to pick

1. **Hebrew coverage first.** For a Hebrew reel, both the caption face and the display face
   come from `list --hebrew`. A Latin-only display face (Bebas Neue, Anton, Montserrat) is
   only for Latin-only words such as numbers, an English wordmark or a term of art. It must
   never be the face of a Hebrew line, or the Hebrew drops to the fallback.
2. **The "AI" rule.** In Heebo (and most Hebrew sans faces), the capital I has no serifs, so a
   Latin `AI` inside a Hebrew caption reads as **"Al"**. Set every isolated Latin acronym
   (`AI`, `API`, `CEO`, `GPT`) in **Roboto Slab 800**:
   `<span class="ltr" style="font-family:'Roboto Slab', serif; font-weight:800">AI</span>`.
   The slab serifs on the I end the ambiguity. Check this in every QA pass.
3. **Weights that read at phone size.** Captions use 700–900 (house: 800). A 300–400 caption
   falls apart on a phone at arm's length. Light weights are only for large display words of
   about 120 px and up.
4. **One caption face + at most ONE display face.** Never use two display faces in the same
   reel, because they compete and the reel looks templated. The display face is for punch
   words, cards and the outro tagline, never for captions.
5. **Single-weight faces stay single-weight.** Secular One, Suez One, Varela Round, Bellefair,
   Bebas Neue and Anton ship one weight (400). Do not ask for 800, because Chrome fakes it
   with a smeared synthetic bold.
6. **Every stack ends in `"Inter", sans-serif`.** Example:
   `font-family: var(--brand-font), "Inter", sans-serif`.

### Proven Hebrew pairings (`fonts.py pair`)

| Caption / body | Headline / display | Use for |
|---|---|---|
| Heebo 800 | Secular One | House default: neutral captions with heavy, compact punch words |
| Rubik 700 | Suez One | Friendly captions with editorial slab headlines (news, explainers) |
| Assistant 700 | Frank Ruhl Libre 800 | Elegant: finance, legal, luxury, family office |
| Varela Round 400 | Karantina 700 | Playful: lifestyle, kids, food |
| IBM Plex Sans Hebrew 700 | Heebo 900 | Corporate or tech |
| Noto Sans Hebrew 700 | Noto Serif Hebrew 800 | Multi-language sets |

When a reference video uses a commercial face, match its *character* with a registry face
(for example, a condensed poster face becomes Karantina or Bebas Neue). Never chase the exact
face. **Arimo** is the free, metric-compatible stand-in for Arial when a client file says
Arial.

---

## Adding a font (only with a verified free licence)

1. Find the family in `github.com/google/fonts` (`ofl/`, `apache/` or `ufl/<dir>/`). Read its
   `METADATA.pb`. You need the `license`, the exact `filename:` entries, the axis range for a
   variable font, and for Hebrew, `subsets: "hebrew"`.
2. Confirm that the licence file exists in that directory (`OFL.txt` / `LICENSE.txt` /
   `UFL.txt`). Tinos is left out of the registry for exactly this reason: google/fonts ships
   it with no licence file, so it cannot be recorded with one.
3. Add one `_family(...)` entry to `_ENTRIES` in `fonts.py`, with `variable=` for an `[axes]`
   file or `static={weight: filename}` for per-weight files.
4. Verify every URL with a real HTTP request (both mirrors and the licence), then
   `fetch` it into a scratch directory and check the cmap for the scripts you claimed.

A client's own font, which is not on Google Fonts, can come in only through
`setup_assets.py --font-dir <folder>`. The folder must ship the font's free licence text: an
`OFL.txt` or `LICENSE` file that contains "SIL Open Font License" or "Apache License".
Anything else is refused with an explanation. A font installed on a computer is not a licence
to publish with it. If the client insists on a paid face, they need a licence that covers
video and broadcast use, and that is their decision to make outside this skill. The skill
still will not render with it.

---

## The gate

`fonts.guard(paths, font_dir) -> list[str]` (empty = pass) is called by the QA gate. On the
CLI it is `fonts.py guard`. It reads the HTML/CSS and checks every place a family can hide:

- `font-family:` declarations, `font:` shorthands, inline `style=""`, SVG
  `font-family="..."`, GSAP `fontFamily:` and canvas `ctx.font = "..."`
- custom properties: `var(--brand-font)` is resolved through every file passed (pass
  `brand/brand.css` together with `index.html`), and an undefined variable fails
- `@font-face`: an alias (`font-family:"Brand"; src:url(.../Rubik.ttf)`) passes only when
  every `src` is a file the registry or the lock vouches for; `local()` and `~/Library/Fonts`
  / `/Library/Fonts` sources fail
- paid font CDNs (Typekit / Adobe Fonts, fonts.com, MyFonts, Fontbit) fail; a Google Fonts
  link for a family that is not in the registry fails

A family passes when it is a registry family, has a free-licence lock record, or is a generic
keyword (`sans-serif`, `serif`, `monospace`, `system-ui`, `cursive`, `inherit`, `initial`...).
Banned names fail **even as a fallback**: Arial, Helvetica (Neue), Times (New Roman), Futura,
Gotham, Avenir, Proxima Nova, SF Pro / `-apple-system`, Segoe UI, Calibri, Narkis, Guttman,
Microsoft's David / Miriam / FrankRuehl, and any `Fb*` (Fontbit) face. When an unknown name is
installed in `~/Library/Fonts`, the message says so.

**Every font file in the font dir must be accounted for.** It needs a lock record (whose
sha256 still matches) or a registry filename. An unknown `.ttf/.otf/.woff2/.ttc` fails,
because a stray file is how a commercial face ends up in a build.

It was negative-tested when it was built. An Arial fallback, an unknown `.ttf`, a tampered
file, `local()`, Typekit, `Fb*` and an undefined `var()` all fail; Heebo + Inter passes. Run
the negative test again if you change it.
