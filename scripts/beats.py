#!/usr/bin/env python3
"""THE beat map — the single source of truth for layout state and caption slot.

Copy this into your project as `scripts/beats.py`. Both the composition builder and the
caption-layer builder import it, so a cut point can never move in one without moving in
the other.

WHEN TO EDIT IT. Most reels never do: a talking head that stays full-screen (the default,
an avatar or a single take, with designed moments drawn OVER the speaker by scenes.py) is
the one "std" beat below, as shipped. Edit BEATS only when the LAYOUT of the speaker
changes along the reel:
  * "panel"  the speaker drops into a lower panel under a B-roll / graphic panel on top
  * "ctr" / "hi"  a full-frame B-roll covers the speaker (caption centred / lifted)
  * "hook"   ONLY a matted hook (media.json "hook" with a matte: the speaker cut out over a
             blurred backdrop). The kit's flying hook world is NOT a beat — scenes.py
             drives it — and a "hook" beat on a plain take makes the outro and the punch
             planner treat the opening as matted.
Every beat start must sit on a segment boundary (src/bounds.json) — run this file to check.
END stays 0.0: build_index.py sets it from the A-roll's real duration.

The failure this prevents: a hand-written `SLOTS = [(start, end, class)]` table kept
alongside a separate visual beat map. They drift, and the symptom lands on the
speaker's face — a caption plate sitting on their mouth because the slot table still
said "panel" across a stretch where the beat map had them full-screen. Nothing is
wrong with the slot VALUES; the two tables simply disagree.
"""
import json
import os
import sys

# ---------------------------------------------------------------- slot table
# Vertical position (plate TOP) of the caption, per layout state. See references/captions.md
# and references/grid.md. The speaker states sit in the Reels caption band (y 1110-1190),
# read from scripts/grid.py so the grid has one owner. Every slot must pass
# `grid.py check` — the plate is centred by grid.centered_box(): up to 800 px wide it sits
# on the frame centre x 540; a wider one keeps its right edge on 940.
try:
    import grid as _grid
    _band_top = _grid.caption_top(_grid.profile("reels"), _grid.plate_height(70))
except Exception:                     # beats.py run on its own, outside the skill
    _band_top = 1092

SLOT = {
    "hook":  _band_top,   # matte hook — caption band, below the chin
    "std":   _band_top,   # speaker full-screen — caption band, on the chest
    "panel":  780,        # 900px top panel — at/above the seam, over B-roll
    "ctr":    930,        # full-frame B-roll or graphic — CENTRED, never lower third
    "hi":     520,        # B-roll whose ACTION is dead centre — lifted clear of it
}

# ------------------------------------------------------------------- the map
# (start_seconds, kind, tag).  `kind` must be a key of SLOT.
# `tag` is free-text for humans and for the validator's error messages.
BEATS = [
    (0.00, "std", "speaker full-screen (the default — keep it unless the layout changes)"),
    # (0.00,  "hook",  "ONLY a matted hook: the speaker cut out over a blurred backdrop"),
    # (8.28,  "std",   "plain A-roll after the matted hook"),
    # (11.56, "ctr",   "full-frame B-roll: step 1"),
    # (17.50, "hi",    "the click happens dead centre — lift the caption"),
    # (21.22, "std",   "A-roll, steady push"),
]

END = 0.0          # leave it: build_index.py sets it from the A-roll's REAL duration
FRAME = 0.04       # 25 fps — keep in step with config.json project.fps


def slot_at(t):
    """The caption slot class active at time t."""
    kind = BEATS[0][1]
    for start, k, _ in BEATS:
        if t >= start - 1e-6:
            kind = k
    return kind


def top_at(t):
    return SLOT[slot_at(t)]


def beat_starts():
    return [b[0] for b in BEATS]


def panels():
    """Contiguous `panel` beats merged into (start, end) ranges — the gradient panels."""
    out = []
    for i, (start, kind, _) in enumerate(BEATS):
        end = BEATS[i + 1][0] if i + 1 < len(BEATS) else END
        if kind != "panel":
            continue
        if out and abs(out[-1][1] - start) < 1e-6:
            out[-1] = (out[-1][0], end)
        else:
            out.append((start, end))
    return out


def assert_on_boundaries(bounds, tol=1e-3):
    """Every beat start must be a real segment boundary. Run this before building
    anything — a beat that sits mid-sentence is a cut that fires early."""
    bad = [(t, tag) for t, _, tag in BEATS
           if not any(abs(t - b) < tol for b in bounds)]
    if bad:
        lines = "\n".join(f"    {t:7.2f}  {tag}" for t, tag in bad)
        raise AssertionError(
            "beat start(s) are not segment boundaries — every layout cut must land on a "
            f"sentence start:\n{lines}")
    return True


def load_bounds(path="src/bounds.json"):
    """Segment boundaries as written by cut_aroll.py."""
    if not os.path.exists(path):
        sys.exit(f"{path} not found — run scripts/cut_aroll.py first")
    d = json.load(open(path, encoding="utf-8"))
    return d["bounds"], d["total"]


if __name__ == "__main__":
    bounds, total = load_bounds()
    globals()["END"] = total
    assert_on_boundaries(bounds)
    print(f"{len(BEATS)} beats over {total:.2f}s — every start is a segment boundary")
    for start, kind, tag in BEATS:
        print(f"  {start:7.2f}  {kind:6s} top={SLOT[kind]:4d}  {tag}")
