#!/usr/bin/env python3
"""Merge an analysed reference style (style/style.json) into the project's config.json.

Claude writes style/style.json after reading the reference sheets (method:
references/reference-analysis.md; start from style/style.draft.json). This script is the
only thing that moves those values into config.json, so every rule below is enforced in
one place and every change is printed.

Precedence (highest first)
  1. the user's explicit request   → keys listed in --lock or config style.locked
  2. the brand (logo, fonts)       → brand colours / fonts are NEVER written from a
                                     reference; a value set by hand in config.json is kept
                                     (--force to override)
  3. the reference                 → style.json
  4. house defaults                → hfcfg.DEFAULTS
  The grid beats all of them: captions.center_y is clamped into the caption-safe region.

style.json schema (every field optional; null = "no opinion", left untouched)
  {
    "meta":     {"references": ["name", ...]},          → style.source
    "captions": {"style": "plate" | "shadow",
                 "max_words": 1-4,                       (int, one line always)
                 "weight": 100-900,                      (int, rounded to 100)
                 "center_y": px at 1920 high},           (clamped to the grid)
    "brand":    {"caption_size": px at 1080 wide},       (40-110; the ONLY brand key)
    "style":    {"target_cuts_per_30s": float, "median_shot_s": float,
                 "broll_share": 0-0.5, "headline": str, "transitions": str,
                 "hook_notes": str, "sfx_per_min": float, "notes": [str],
                 "palette": ["#RRGGBB", ...],            (never overwrites brand colours)
                 "transitions_allowed": ["page-turn", "frame-fly", "dissolve", ...],
                                                         (default [] = Omer's rule: no
                                                          transition on B-roll, ever)
                 "kinetic": true | "rollin" | "classic" | "bold"},   (the reference builds
                                                          word-by-word headlines; a name
                                                          sets the default headline style)
    "audio":    {"music_db_under_voice": 6-30 (positive dB), "music_character": str}
  }
  Keys starting with "_" are comments. A non-empty "_todo" list refuses to apply
  (finish the analysis or pass --allow-todo).

  transitions_allowed opens SPECIFIC designed transitions only when the reference really
  uses them (read the cut strips): a page turn, the frame-shrink-and-fly, a dissolve through
  a tint. It is never inferred silently — when style.transitions / style.headline describe
  one but the switch is missing, a HINT is printed and nothing is written. A value the user
  set by hand in config.json wins, like every house-managed key; --lock keeps it too.

Usage
  python3 scripts/apply_style.py [--style style/style.json] [--config config.json]
                                 [--dry-run] [--lock captions.style,brand.caption_size]
                                 [--force] [--allow-todo]
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grid   # noqa: E402
import hfcfg  # noqa: E402
import kinetic  # noqa: E402

# Named designed transitions a style may open (references/layout.md "Transitions are a
# style decision"). A name outside this list is dropped, loudly.
TRANSITIONS = ("page-turn", "frame-fly", "dissolve", "flash", "whip", "push", "zoom")
# free-text cues in style.transitions → the switch they suggest (a hint, never written)
TRANSITION_CUES = (
    (r"page[\s-]*turn|hairline.*(turn|wipe)|wipe", "page-turn"),
    (r"frame[\s-]*(shrink|fly)|shrink.*fly|fly(s|ing)?\s+(in|out|away)", "frame-fly"),
    (r"dissolve|cross[\s-]*fade|crossfade", "dissolve"),
    (r"\bflash", "flash"), (r"\bwhip", "whip"), (r"\bpush\b", "push"),
)

BASE_H = 1920


# ------------------------------------------------------------------ helpers
def get(d, key):
    for k in key.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def put(d, key, value):
    ks = key.split(".")
    for k in ks[:-1]:
        if not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[ks[-1]] = value


def flat(d, pre=""):
    out = {}
    for k, v in (d or {}).items():
        if k.startswith("_"):
            continue
        key = pre + k
        if isinstance(v, dict) and key not in ("style.applied",):
            out.update(flat(v, key + "."))
        else:
            out[key] = v
    return out


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ------------------------------------------------------------------ validators
# Each returns (value, clamp_message_or_None) or raises ValueError.
def v_enum(*choices):
    def f(v, ctx):
        if v not in choices:
            raise ValueError(f"must be one of {', '.join(map(repr, choices))}")
        return v, None
    return f


def v_num(lo, hi, cast=float, why="", step=None):
    def f(v, ctx):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("must be a number")
        x = cast(round(v / step) * step) if step else cast(v)
        y = cast(clamp(x, lo, hi))
        msg = None
        if y != x:
            msg = f"{v} → {y} (allowed {lo}-{hi}{'; ' + why if why else ''})"
        elif x != v and not (cast is float):
            msg = f"{v} → {x} (rounded)"
        return y, msg
    return f


def v_text(v, ctx):
    if not isinstance(v, str) or not v.strip():
        raise ValueError("must be a non-empty string")
    return v.strip(), None


def v_list_text(v, ctx):
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ValueError("must be a list of strings")
    return [x.strip() for x in v if x.strip()], None


def v_palette(v, ctx):
    if not isinstance(v, list):
        raise ValueError("must be a list of #RRGGBB strings")
    good = [x.upper() for x in v if isinstance(x, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", x)]
    bad = [x for x in v if not (isinstance(x, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", x))]
    return good, (f"dropped invalid colour(s) {bad}" if bad else None)


def v_transitions(v, ctx):
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ValueError(f"must be a list of names from {', '.join(TRANSITIONS)}")
    names = [x.strip().lower().replace("_", "-").replace(" ", "-") for x in v if x.strip()]
    good = [x for x in dict.fromkeys(names) if x in TRANSITIONS]
    bad = [x for x in names if x not in TRANSITIONS]
    return good, (f"dropped unknown transition(s) {bad} (known: {', '.join(TRANSITIONS)})"
                  if bad else None)


def v_kinetic(v, ctx):
    if isinstance(v, bool):
        return v, None
    if isinstance(v, str):
        name = kinetic.STYLE_ALIASES.get(v.strip().lower(), v.strip().lower())
        if name in kinetic.STYLES:
            return name, None
    raise ValueError(f"must be true/false or a headline style: {', '.join(kinetic.STYLES)}")


def v_center_y(v, ctx):
    """The plate must sit inside the safe zone and above the bottom-card zone."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("must be a number (px at 1920 high)")
    g, size = ctx["grid"], ctx["caption_size"]
    ph = grid.plate_height(size)
    _, sy0, _, sy1 = g["safe"]
    floor = sy1 - g["bottom_card_max_h"]               # bottom cards grow up to here
    lo = sy0 + ph / 2
    hi = min(sy1, floor) - ph / 2
    y = int(round(clamp(v, lo, hi)))
    if y == int(round(v)):
        return y, None
    if v > hi:
        why = (f"a {ph}px plate centred at {int(v)} spans {int(v - ph / 2)}-{int(v + ph / 2)} and "
               f"would cover the bottom-card zone y {floor}-{sy1}"
               + (" and the Reels UI below y %d" % sy1 if v + ph / 2 > sy1 else ""))
    else:
        why = f"a {ph}px plate centred at {int(v)} would reach into the top UI (safe from y {sy0})"
    return y, f"{int(v)} → {y} — {why}. The grid beats the reference."


SPEC = {
    "captions.style": v_enum("plate", "shadow"),
    "captions.max_words": v_num(1, 4, int, "one line, never more than 4 words"),
    "captions.weight": v_num(100, 900, int, step=100),
    "captions.center_y": v_center_y,
    "brand.caption_size": v_num(40, 110, int, "legible on a phone, fits one line"),
    "style.target_cuts_per_30s": v_num(1, 30, float),
    "style.median_shot_s": v_num(0.3, 15, float),
    "style.broll_share": v_num(0.0, 0.5, float, "the speaker is on screen nearly always"),
    "style.headline": v_text,
    "style.transitions": v_text,
    "style.hook_notes": v_text,
    "style.sfx_per_min": v_num(0, 20, float),
    "style.notes": v_list_text,
    "style.palette": v_palette,
    "style.transitions_allowed": v_transitions,
    "style.kinetic": v_kinetic,
    "audio.music_db_under_voice": v_num(6, 30, float, "positive dB the bed sits under the voice"),
    "audio.music_character": v_text,
}
# keys the reference may never write, with the reason
REFUSED = {
    "brand.": "brand keys (colours, fonts, logo) come from the brand, never from a reference — "
              "only brand.caption_size is accepted",
    "captions.font": "fonts: free faces only, chosen by hand in brand.font_family",
    "grid.": "the grid is fixed by the platform, a reference cannot move it",
    "language.": "verbatim captions: a reference never changes the words",
}
# reference keys that fall back to a house default when the reference is silent
# (the two switches too: a list the user wrote in config.json by hand is their request)
HOUSE_MANAGED = ("captions.", "brand.caption_size", "audio.",
                 "style.transitions_allowed", "style.kinetic")


def main():
    ap = hfcfg.arg_parser(__doc__.split("\n\n")[0])
    ap.add_argument("--style", default="style/style.json")
    ap.add_argument("--dry-run", action="store_true", help="print the changes, write nothing")
    ap.add_argument("--lock", action="append", default=[],
                    help="key(s) the user asked for explicitly — never overwritten (comma list)")
    ap.add_argument("--force", action="store_true",
                    help="override values set by hand in config.json (never locked ones)")
    ap.add_argument("--allow-todo", action="store_true", help="apply even if _todo is non-empty")
    a = ap.parse_args()

    if not os.path.exists(a.style):
        sys.exit(f"no style file at {a.style} — run analyze_reference.py, read the sheets, and "
                 f"write it from style/style.draft.json")
    style = json.load(open(a.style, encoding="utf-8"))
    todo = style.get("_todo") or []
    if todo and not a.allow_todo:
        sys.exit("style.json still has open TODOs — finish them (and delete _todo) first:\n  - "
                 + "\n  - ".join(todo))

    cfg_path = next((p for p in (a.config, "config.json", os.environ.get("HAPORTAL_VIDEO_EDITOR_CONFIG"))
                     if p and os.path.exists(p)), None)
    if not cfg_path:
        sys.exit("no project config.json — cp config.example.json config.json first "
                 "(apply_style never writes the skill's example)")
    raw = json.load(open(cfg_path, encoding="utf-8"))
    cfg = hfcfg.load(cfg_path)
    new = copy.deepcopy(raw)
    st = new.setdefault("style", {}) if isinstance(new.get("style"), dict) else new.setdefault("style", {})
    locks = set(st.get("locked") or [])
    for x in a.lock:
        locks.update(k.strip() for k in x.split(",") if k.strip())
    applied_before = st.get("applied") or {}

    g = grid.profile(cfg.get("grid", {}).get("profile", "reels"), 1080, BASE_H)
    sf = flat({k: v for k, v in style.items() if k != "meta"})
    size = sf.get("brand.caption_size") or cfg["brand"].get("caption_size", 70)
    if isinstance(size, (int, float)) and not isinstance(size, bool):
        size = int(clamp(size, 40, 110))
    else:
        size = cfg["brand"].get("caption_size", 70)
    ctx = {"grid": g, "caption_size": size}

    changes, clamps, kept, ignored, errors, applied = [], [], [], [], [], {}
    for key, val in sf.items():
        if val is None:
            continue
        refused = next((why for pre, why in REFUSED.items()
                        if key.startswith(pre) and key != "brand.caption_size"), None)
        if refused:
            ignored.append(f"{key}: refused — {refused}")
            continue
        if key not in SPEC:
            ignored.append(f"{key}: not a style key (see the schema in apply_style.py)")
            continue
        try:
            v, msg = SPEC[key](val, ctx)
        except ValueError as e:
            errors.append(f"{key}={val!r}: {e}")
            continue
        if msg:
            clamps.append(f"{key}: {msg}")
        if key in locks:
            kept.append(f"{key}: locked by the user's request — stays {get(cfg, key)!r} "
                        f"(reference wanted {v!r})")
            continue
        cur, dflt = get(cfg, key), get(hfcfg.DEFAULTS, key)
        hand_set = (key.startswith(HOUSE_MANAGED) and cur is not None and cur != dflt
                    and cur != applied_before.get(key) and cur != v)
        if hand_set and not a.force:
            kept.append(f"{key}: config.json sets {cur!r} by hand (user/brand beats the reference) "
                        f"— reference wanted {v!r}; --force to override")
            continue
        applied[key] = v
        if key == "style.notes":                      # merged with apply_style's own notes below
            continue
        if cur != v:
            changes.append(f"{key}: {cur!r} → {v!r}")
        put(new, key, v)

    # notes the build should see: every clamp, plus the palette rule
    cur_notes = list(get(cfg, "style.notes") or [])
    if "style.notes" in applied:
        notes = list(applied["style.notes"])
    else:
        notes = [n for n in cur_notes if not n.startswith("apply_style:")]
    for c in clamps:
        n = "apply_style: clamped " + c
        if n not in notes:
            notes.append(n)
    pal = get(new, "style.palette")
    logo = (cfg.get("brand") or {}).get("logo")
    if pal:
        pal_note = ("apply_style: brand logo present → brand colours win; style.palette is "
                    "reference-only" if logo else
                    "apply_style: no brand logo → style.palette may drive motion accents; "
                    "brand.accent* untouched")
        notes = [n for n in notes if not n.startswith("apply_style: brand logo present")
                 and not n.startswith("apply_style: no brand logo")] + [pal_note]
    if notes != cur_notes:
        changes.append(f"style.notes: {len(cur_notes)} → {len(notes)} note(s)")
    if notes:
        put(new, "style.notes", notes)
    refs = (style.get("meta") or {}).get("references")
    if refs:
        put(new, "style.source", refs)
    put(new, "style.applied", {**applied_before, **applied})
    if locks:
        put(new, "style.locked", sorted(locks))

    print(f"== APPLY STYLE  {a.style} → {cfg_path}{'  (dry run)' if a.dry_run else ''}")
    print(f"   grid {g['name']}: safe y {g['safe'][1]}-{g['safe'][3]}, bottom cards from y "
          f"{g['safe'][3] - g['bottom_card_max_h']}, caption plate {grid.plate_height(size)}px at size {size}")
    for title, rows, mark in (("changed", changes, "~"), ("CLAMPED", clamps, "!"),
                              ("kept (higher precedence)", kept, "="),
                              ("ignored", ignored, "-"), ("INVALID", errors, "✗")):
        if rows:
            print(f"\n  {title}:")
            for x in rows:
                print(f"    {mark} {x}")
    if pal:
        print(f"\n  palette: {pal} — " + ("brand logo set: brand colours win, palette kept for "
                                         "reference only" if logo else
                                         "no logo: the build may use it for motion accents"))
    hints = []
    if "style.transitions_allowed" not in sf and sf.get("style.transitions"):
        txt = str(sf["style.transitions"]).lower()
        sug = [n for rx, n in TRANSITION_CUES if re.search(rx, txt)]
        if sug:
            hints.append(f"style.transitions reads {sf['style.transitions']!r} — if the strips "
                         f"confirm it, add \"transitions_allowed\": {sug} to style.json (Omer's "
                         f"no-transition rule stays until you do)")
    if "style.kinetic" not in sf and re.search(r"kinetic|word[\s-]*by[\s-]*word",
                                                str(sf.get("style.headline") or "").lower()):
        hints.append("style.headline describes word-by-word headlines — add \"kinetic\": "
                     "\"rollin\" (or ko / bold) to style.json, then put 2-4 \"headlines\" in "
                     "media.json (references/kinetic.md)")
    if hints:
        print("\n  hints (nothing written):")
        for x in hints:
            print(f"    ? {x}")
    if not changes:
        print("\n  nothing to change")
    if errors:
        print("\n  fix the INVALID values in style.json and run again — nothing written")
        return 1
    if a.dry_run:
        return 0
    shutil.copy(cfg_path, cfg_path + ".bak")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(new, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"\n  wrote {cfg_path} (backup {cfg_path}.bak)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
