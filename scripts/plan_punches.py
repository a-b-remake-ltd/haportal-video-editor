#!/usr/bin/env python3
"""Plan the camera punch-ins — a two-camera edit made from one take.

    python3 scripts/plan_punches.py                                   # print the plan
    python3 scripts/plan_punches.py --apply                           # write it into media.json
    python3 scripts/plan_punches.py --key "word,another phrase" --apply
    python3 scripts/plan_punches.py --selftest                        # the gates' own tests

WHEN. AFTER the first build (build_index.py + captions.py), then rebuild. It reads what the
build wrote — the hook, every scene camera move, every headline window and the outro — and
REFUSES to run without those files: run before the build, it knew none of them, printed
"blocked: nothing" and put punches inside the hook (a from-zero test).

WHY. A single locked-off take reads as static within seconds. Hard scale steps on phrase
boundaries — 1.0 ↔ 1.06-1.10, and 1.12-1.14 on the key words — make it feel cut between a
wide and a tight camera, which is the cheapest "something changes every 2-4 s" there is.
Done by hand the steps drift onto mid-word frames, pile up inside the hook, and get stranded
by every re-cut; here they are derived from the words, so a rebuild moves them with the cut.

Rules (spec §5, references/moments.md → punch):
  * a step lands ONLY on a phrase boundary: a caption card start (captions.json — the
    splitter already found the phrases), a word after punctuation, or a segment boundary;
    key-word steps land on the key word's own start
  * one change every 2-4 s (--gap 2 4), alternating 1.0 and a punch that cycles
    1.08 / 1.06 / 1.10; a key word gets 1.12 / 1.14, and when the camera is already punched
    in shortly before a key word it first returns to 1.0, so the key punch reads as a jump
  * never inside (all found automatically from the build):
      - the hook window (media.json "hook", and any hidden window whose source is the
        hook / world / fly / intro), the outro (build/outro.json)
      - a footage moment (paper / fly: they drive the A-roll's transform themselves) or a
        hand-made punch moment (media.json "moments")
      - a scene camera move — kit cam_shake / cam_sway / cam_push, a hook fly, anything a
        scene in build/scenes.json tweens on the footage: read from index.html's scene
        blocks (scenes.py would reject a step there anyway)
      - a HEADLINE window (build/caption_hide.json windows whose source is a media.json
        headline id). Blocked, not capped: kinetic.py places the stack under the chin as
        the footage is when the headline starts; a step inside the window scales the chin
        down onto the words (a test reel: headlines jumped above the head)
    Each free stretch between those becomes its own punch moment, which hands the frame
    back at scale 1 (× the beat's scale) at its end — so the block's edges are changes too
  * density: a stretch of 1.2-4 s gets one step in its middle; a longer one a step every
    2-4 s, the first ~0.6 s after the stretch opens. The longest stretch without a change
    is printed; a gap over 8 s that HAD a phrase boundary in it fails (a planner bug: the
    old rule needed 2 s of lead in every stretch and left 6.3-20.4 s of a reel untouched)
  * ROTATION / SWAY: rotating the frame exposes black corners unless it is scaled up, so any
    rotation or sway on the footage MUST ride on a scale ≥ 1.07. This planner emits none;
    preflight_qa.py checks every rotation tween on the footage in index.html against the
    scale active at that moment and fails below 1.07.

Inputs   src/words.json, src/bounds.json, captions.json (phrase starts), media.json (hook,
         moments, headlines) and the BUILD: index.html, build/caption_hide.json,
         build/scenes.json (when the reel has scenes), build/outro.json (when the outro is on)

punches.json (optional, next to media.json; --keys picks another file):
    {"key":   ["word", "two words"],   # key words: a BIG punch (1.12 / 1.14) on their start
     "block": [[12.3, 14.3]]}          # extra seconds where no step may land (by hand —
                                       # everything the build knows is blocked already)
   --key "w1,two words" adds key words on the command line.

Output   one "punch" moment per free stretch, "_auto": true; --apply replaces the previous
         auto punches in media.json (hand-made ones are kept and blocked around). Rebuild
         (build_index.py) after --apply.
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

FRAME = 0.04
REGULAR = (1.08, 1.06, 1.10)
KEY = (1.12, 1.14)
FOOTAGE_TYPES = ("paper", "fly")
BLOCK_SOURCES = ("hook", "world", "fly", "outro", "intro")
NIQQUD = re.compile(r"[֑-ׇ]")
PUNCT = re.compile(r"[\s.,?!…:;\"'“”„״׳()\[\]{}\-–—־/]")


def norm(w):
    return PUNCT.sub("", NIQQUD.sub("", str(w))).lower()


def snap(t):
    return round(round(t / FRAME) * FRAME, 3)


def snap_in(a, b):
    """A free stretch snapped INWARD to the frame grid: its start up, its end down. The
    punch moment hands the frame back at its end; rounded to nearest, a stretch ending at
    9.34 handed back at 9.36 — inside the camera shake that starts at 9.34, and the build
    refused it."""
    import math
    return (round(math.ceil(a / FRAME - 1e-6) * FRAME, 3),
            round(math.floor(b / FRAME + 1e-6) * FRAME, 3))


def load_words(path):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from captions import load_words as lw, normalise
    return normalise(lw(path), {})


def phrase_starts(words, bounds, caps):
    """Times a scale step may land on."""
    out = set()
    for k, (s, e, w) in enumerate(words):
        if k == 0 or re.search(r"[,.?!…:;]$", words[k - 1][2]) or s - words[k - 1][1] >= 0.15:
            out.add(snap(s))
    for b in bounds:
        out.add(snap(b))
    for c in caps:
        out.add(snap(float(c["start"])))
    return sorted(out)


def key_points(words, keys):
    """[(t, key)] for every occurrence of every key word / phrase."""
    pts = []
    nw = [norm(w[2]) for w in words]
    for key in keys:
        pat = [norm(x) for x in key.split() if norm(x)]
        if not pat:
            continue
        hit = False
        for i in range(len(nw) - len(pat) + 1):
            if nw[i:i + len(pat)] == pat:
                pts.append((snap(words[i][0]), key))
                hit = True
        if not hit:
            print(f"  ! key {key!r} is not in src/words.json — check the spelling")
    return sorted(set(pts))


FOOT_SEL = re.compile(r'"([^"]*#(?:aroll|cam|amatte)\b[^"]*)"')
SCENE_HEAD = re.compile(r"//\s*scene\s+(\S+)\s+([\d.]+)-([\d.]+)s")
NUM = r"(-?\d+(?:\.\d+)?)"


def scene_camera_moves(html):
    """[(a, b, scene id)] — every window in which a SCENE moves the footage, read from the
    built index.html: scene fragments are emitted as `{ const {...} = __KIT;   // scene <id>
    <start>-<end>s … }` blocks (scripts/scenes.py), and the kit's camera moves are calls on
    the footage selector inside them: shake(sel, t, …) lasts 0.26 s (0.06 × yoyo ×4),
    push(sel, t, d, …) lasts d, ft/fromTo/to(sel, …, {duration, repeat}, t) lasts
    duration × (repeat + 1), tl.set(sel, …, t) is an instant. One window per scene, from
    its first footage call to the end of its last one (cam_sway: the scale-up set, the
    sway, the set that restores it)."""
    out, cur, span = [], None, None

    def close():
        if cur and span:
            out.append((round(span[0], 3), round(span[1], 3), cur))
    for ln in html.splitlines():
        m = SCENE_HEAD.search(ln)
        if m and "__KIT" in ln:
            close()
            cur, span = m.group(1), None
            continue
        if cur and ln.strip() == "}":
            close()
            cur, span = None, None
            continue
        if not cur or not FOOT_SEL.search(ln):
            continue
        st = ln.strip()
        a = b = None
        mm = re.match(r"shake\(\s*\"[^\"]*\"\s*,\s*" + NUM, st)
        if mm:
            a = float(mm.group(1))
            b = a + 0.26
        mm = mm or re.match(r"push\(\s*\"[^\"]*\"\s*,\s*" + NUM + r"\s*,\s*" + NUM, st)
        if mm and a is None:
            a = float(mm.group(1))
            b = a + float(mm.group(2))
        if a is None:
            mt = re.search(r",\s*" + NUM + r"\s*\)\s*;", st)
            if not mt:
                continue
            a = float(mt.group(1))
            d = re.search(r"duration:\s*" + NUM, st)
            rp = re.search(r"repeat:\s*" + NUM, st)
            b = a + (float(d.group(1)) * (int(float(rp.group(1))) + 1 if rp else 1) if d else 0.0)
        span = (a, b) if span is None else (min(span[0], a), max(span[1], b))
    close()
    return out


def headline_ids(media):
    """Ids of the kinetic headlines (media.json "headlines"; kinetic.py's own h1, h2 … when
    an entry has none)."""
    ids = set()
    for i, h in enumerate(media.get("headlines") or [], 1):
        ids.add(str(h.get("id") or f"h{i}") if isinstance(h, dict) else f"h{i}")
    return ids


def blocked_windows(media, hide, outro, end, extra, moves=()):
    """[(a, b, why)] where no step may land."""
    out = []
    hook = media.get("hook")
    if hook and hook.get("end") is not None:
        out.append((0.0, float(hook["end"]), "hook"))
    heads = headline_ids(media)
    for m in media.get("moments") or []:
        t = m.get("type")
        if t in FOOTAGE_TYPES:
            out.append((float(m["start"]), float(m["end"]), f"{m.get('id', t)} ({t})"))
        elif t == "punch" and not m.get("_auto"):
            out.append((float(m["start"]), float(m["end"]), f"{m.get('id')} (hand-made punch)"))
    for (a, b), src in zip(hide.get("windows", []), hide.get("sources", [[]] * 999)):
        lst = [str(x) for x in (src if isinstance(src, list) else [src])]
        srcs = " ".join(lst).lower()
        if any(k in srcs for k in BLOCK_SOURCES):
            out.append((float(a), float(b), f"hidden window ({srcs})"))
        elif any(x in heads for x in lst):
            out.append((float(a), float(b), f"headline {' '.join(x for x in lst if x in heads)}"))
    for a, b, sid in moves:
        out.append((float(a), max(float(b), float(a) + 0.04), f"scene {sid} camera move"))
    if outro and outro.get("start") is not None:
        out.append((float(outro["start"]), max(end, float(outro.get("end") or end)), "outro"))
    for a, b in extra:
        out.append((float(a), float(b), "punches.json block"))
    return sorted(out)


def free_regions(blocked, end, min_len=1.2):
    regions, t = [], 0.0
    for a, b, _ in blocked:
        if a > t + min_len:
            regions.append((t, a))
        t = max(t, b)
    if end > t + min_len:
        regions.append((t, end))
    return regions


LEAD = 0.6     # s: the first step after a stretch opens (the block's end is a change too)


def plan_region(r0, r1, cands, keys, gmin, gmax, kmin=1.0, ri0=0):
    """Steps [(t, factor, why)] for one free stretch. The stretch's own edges count as
    changes (the frame returns to scale 1 at its end; the block before it — a headline,
    the hook, a camera move — ends at its start)."""
    if r1 - r0 < gmin + LEAD:
        # a short stretch: one step near its middle, if a phrase boundary is there
        mid = [c for c in cands if r0 + LEAD <= c <= r1 - LEAD]
        kin = [(t, k) for t, k in keys if r0 + LEAD <= t <= r1 - LEAD]
        if kin:
            return [(round(kin[0][0], 3), KEY[0], f"key: {kin[0][1]}")]
        if mid:
            c = min(mid, key=lambda x: abs(x - (r0 + r1) / 2))
            return [(round(c, 3), REGULAR[ri0 % len(REGULAR)], "tight (short stretch)")]
        return []
    cands = [c for c in cands if r0 + LEAD <= c <= r1 - LEAD]
    keys = [(t, k) for t, k in keys if r0 + LEAD <= t <= r1 - 1.0]
    # the first step may come LEAD after the stretch opens: start the clock gmin - LEAD early
    steps, cur, t_last, ri, ki = [], 1.0, r0 - (gmin - LEAD), ri0, 0
    used_keys = set()
    while True:
        nk = next(((t, k) for t, k in keys if t >= t_last + kmin and (t, k) not in used_keys), None)
        # a key word punches from WIDE: when the camera is wide and the key word is near,
        # hold wide until it (1.10 → 1.12 is invisible; 1.0 → 1.12 is a cut)
        if nk and (nk[0] - t_last <= gmax or (cur == 1.0 and nk[0] - t_last <= gmax + 1.0)):
            t, k = nk
            used_keys.add(nk)
            if cur != 1.0:
                back = [c for c in cands if t_last + gmin <= c <= t - kmin]
                if back:
                    c = min(back, key=lambda x: abs(x - (t_last + t) / 2))
                    steps.append((c, 1.0, "back to wide before the key word"))
                    t_last, cur = c, 1.0
            f = KEY[ki % len(KEY)]
            ki += 1
            steps.append((t, f, f"key: {k}"))
            t_last, cur = t, f
            continue
        lo, hi = t_last + gmin, t_last + gmax
        # leave room to come back to wide before the next key word
        room = (gmin + kmin) if cur == 1.0 else kmin
        pool = [c for c in cands if lo <= c <= hi and (not nk or c <= nk[0] - room)]
        if not pool:
            pool = [c for c in cands if c > hi and (not nk or c <= nk[0] - room)][:1]
        if not pool:
            if nk:                          # nothing fits before the key word: jump to it
                t_last = max(t_last, nk[0] - gmax)
                continue
            break
        c = min(pool, key=lambda x: abs(x - (t_last + (gmin + gmax) / 2.0)))
        if cur != 1.0:
            f, why = 1.0, "wide"
        else:
            f, why = REGULAR[ri % len(REGULAR)], "tight"
            ri += 1
        steps.append((c, f, why))
        t_last, cur = c, f
        if t_last > r1 - gmin:
            break
    return [(round(t, 3), f, w) for t, f, w in steps if r0 < t < r1]


def missing_build_files(a, media):
    """The build outputs this planner needs, missing ones listed. scenes.json only when the
    reel has scenes (scenes.py or media.json "scenes"); outro.json only when the outro is
    on — build_index.py writes neither otherwise."""
    need = [a.hide, a.index]
    if os.path.exists("scenes.py") or media.get("scenes"):
        need.append(a.scenes)
    try:
        import outro as _outro
        on = bool(_outro.settings(hfcfg.load(a.config), media))
    except SystemExit:
        on = False
    except Exception:
        on = False
    if on:
        need.append(a.outro)
    return [p for p in need if not os.path.exists(p)]


def longest_quiet(regions, steps, cands, limit):
    """(the longest gap between changes inside the free stretches, [gaps over `limit` that
    had a phrase boundary in them]). A stretch's edges count as changes."""
    worst, starved = None, []
    for r0, r1 in regions:
        pts = [r0] + sorted(t for t in steps if r0 < t < r1) + [r1]
        for x, y in zip(pts, pts[1:]):
            if worst is None or y - x > worst[1] - worst[0]:
                worst = (x, y)
            if y - x > limit and any(x + LEAD <= c <= y - LEAD for c in cands):
                starved.append((x, y))
    return worst, starved


def selftest():
    """Negative tests on synthetic inputs. Exit 1 on any failure."""
    fails = []

    def expect(cond, what):
        print(f"  {'✓' if cond else '✗'} {what}")
        if not cond:
            fails.append(what)
    html = "\n".join([
        '      tl.set("#aroll", { scale: 1.02, y: 0 }, 0.0);   // speaker full-screen',
        '      { const { ft, shake, push } = __KIT;   // scene bars1 8.46-10.40s',
        '        ft("#bars1-b", { y: -1920 }, { y: 0, duration: 0.24 }, 9.1);',
        '        shake("#aroll", 9.34, 14, "y", 0);',
        '      }',
        '      { const { ft, shake, push } = __KIT;   // scene pup1 10.44-15.10s',
        '        tl.set("#aroll", { scale: 1.08 }, 12.32);',
        '        ft("#aroll", { rotation: 0, x: 0 }, { rotation: 1.2, x: 16, duration: 0.32, yoyo: true, repeat: 5 }, 12.32);',
        '        tl.set("#aroll", { rotation: 0, x: 0, scale: 1.02 }, 14.24);',
        '      }',
        '      { const { ft, shake, push } = __KIT;   // scene em 20.00-22.00s',
        '        push("#aroll, #amatte", 20.5, 0.6, 1.02, 1.1);',
        '      }',
        '      { const { ft } = __KIT;   // scene card 23.00-25.00s',
        '        ft("#card", { y: 40 }, { y: 0, duration: 0.5 }, 23.2);',
        '      }'])
    mv = scene_camera_moves(html)
    expect((9.34, 9.6, "bars1") in mv, f"cam_shake found: {mv}")
    expect((12.32, 14.24, "pup1") in mv, "cam_sway found from its scale-up to its restore")
    expect((20.5, 21.1, "em") in mv, "cam_push found (matte selector too)")
    expect(not any(m[2] == "card" for m in mv), "a scene that never touches the footage is free")
    media = {"headlines": [{"id": "h1"}, {"text": "no id"}], "hook": {"end": 6.3}}
    hide = {"windows": [[0, 6.32], [15.12, 17.6], [30, 31]], "sources": [["hook-world"], ["h1"], ["h2"]]}
    bl = blocked_windows(media, hide, {"start": 55.3, "end": 59.8}, 59.8, [], mv)
    whys = " | ".join(w for _, _, w in bl)
    expect("headline h1" in whys and "headline h2" in whys, "headline windows are blocked")
    expect("scene pup1 camera move" in whys and "outro" in whys and "hook" in whys,
           "camera moves, the hook and the outro are blocked")
    # density: the stretch that used to be skipped (6.32-9.34, then 9.6-12.32) gets steps
    cands = [6.5, 7.28, 8.0, 8.46, 10.44, 11.2, 11.6, 18.0, 19.0, 20.0, 21.4, 23.5, 25.9, 27.3]
    regs = free_regions(bl, 30.0)
    rows = []
    for r0, r1 in regs:
        rows += [t for t, _, _ in plan_region(r0, r1, cands, [], 2.0, 4.0)]
    expect(any(6.32 < t < 9.34 for t in rows), f"a 3 s stretch after the hook gets a step ({rows})")
    expect(any(9.6 < t < 12.32 for t in rows), "a 2.7 s stretch between two camera moves gets one")
    worst, starved = longest_quiet(regs, rows, cands, 8.0)
    expect(not starved, f"no stretch over 8 s without a change (longest {worst})")
    expect(longest_quiet([(0, 20)], [], [5.0], 8.0)[1], "an empty 20 s stretch with a boundary FAILS")
    expect(not any(x - 1e-6 <= t < y for t in rows for x, y, _ in bl), "no step in a blocked window")
    expect(snap_in(6.32, 9.34) == (6.32, 9.32), "a stretch ending at 9.34 hands back at 9.32, "
           "not 9.36 (inside the shake)")
    print(f"  {'all passed' if not fails else str(len(fails)) + ' FAILED'}")
    return 1 if fails else 0


def main():
    ap = hfcfg.arg_parser(__doc__.split("\n\n")[0])
    ap.add_argument("--words", default="src/words.json")
    ap.add_argument("--bounds", default="src/bounds.json")
    ap.add_argument("--captions", default="captions.json")
    ap.add_argument("--hide", default="build/caption_hide.json")
    ap.add_argument("--outro", default="build/outro.json")
    ap.add_argument("--media", default="media.json")
    ap.add_argument("--key", default="", help='comma-separated key words / phrases')
    ap.add_argument("--keys", default="punches.json", help="json: {\"key\": [...], \"block\": [[a, b]]}")
    ap.add_argument("--gap", nargs=2, type=float, default=(2.0, 4.0), metavar=("MIN", "MAX"))
    ap.add_argument("--index", default="index.html")
    ap.add_argument("--scenes", default="build/scenes.json")
    ap.add_argument("--apply", action="store_true", help="write the punches into media.json")
    ap.add_argument("--selftest", action="store_true", help="the gates' negative tests")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    media = json.load(open(a.media, encoding="utf-8")) if os.path.exists(a.media) else {}
    missing = missing_build_files(a, media)
    if missing:
        print("  ✗ plan_punches.py runs AFTER the first build — it blocks the hook, every scene "
              "camera move, every headline and the outro, and reads them from the build:")
        for m_ in missing:
            print(f"      missing: {m_}")
        print("    run  python3 scripts/captions.py && python3 scripts/build_index.py && "
              "python3 scripts/captions.py  then this again (and rebuild after --apply).")
        return 2
    words = load_words(a.words)
    bd = json.load(open(a.bounds, encoding="utf-8")) if os.path.exists(a.bounds) else {}
    bounds, end = bd.get("bounds", [0.0]), float(bd.get("total") or words[-1][1])
    caps = json.load(open(a.captions, encoding="utf-8")) if os.path.exists(a.captions) else []
    hide = json.load(open(a.hide, encoding="utf-8"))
    outro = json.load(open(a.outro, encoding="utf-8")) if os.path.exists(a.outro) else None
    moves = scene_camera_moves(open(a.index, encoding="utf-8").read())
    keys = [k.strip() for k in a.key.split(",") if k.strip()]
    extra = []
    if a.keys and os.path.exists(a.keys):
        kj = json.load(open(a.keys, encoding="utf-8"))
        keys += list(kj.get("key", []) if isinstance(kj, dict) else kj)
        extra = kj.get("block", []) if isinstance(kj, dict) else []

    blocked = blocked_windows(media, hide, outro, end, extra, moves)
    last_word = max(w[1] for w in words)
    regions = free_regions(blocked, min(end, last_word + 0.3))
    cands = phrase_starts(words, bounds, caps)
    kps = key_points(words, keys)
    entries, rows = [], []
    ri = 0
    for n, (r0, r1) in enumerate(regions, 1):
        steps = plan_region(r0, r1, cands, kps, a.gap[0], a.gap[1], ri0=ri)
        ri += sum(1 for _, f, _ in steps if f in REGULAR)
        if not steps:
            continue
        s0_, s1_ = snap_in(r0, r1)
        steps = [x for x in steps if s0_ < x[0] < s1_]
        if not steps:
            continue
        e = {"id": f"punch_auto_{n}", "type": "punch", "start": s0_, "end": s1_,
             "steps": [[t, f] for t, f, _ in steps], "_auto": True,
             "_by": "scripts/plan_punches.py — re-run it after a re-cut instead of editing"}
        entries.append(e)
        rows += [(t, f, w, e["id"]) for t, f, w in steps]

    # gates: never inside a blocked window; nothing denser than one change per second
    bad = [f"{t:.2f}s ({w}) lies inside {why} {x:.2f}-{y:.2f}"
           for t, f, w, _ in rows for x, y, why in blocked if x - 1e-6 <= t < y]
    ts = [r[0] for r in rows]
    bad += [f"steps {p:.2f}s and {q:.2f}s are {q - p:.2f}s apart (< 1 s)" for p, q in zip(ts, ts[1:])
            if q - p < 1.0 - 1e-6]

    print(f"  blocked: " + (", ".join(f"{x:.2f}-{y:.2f} {why}" for x, y, why in blocked) or "nothing"))
    print(f"  {len(cands)} phrase boundaries, {len(kps)} key-word hit(s), "
          f"{len(regions)} free stretch(es)")
    wmap = {snap(w[0]): w[2] for w in words}
    for t, f, w, eid in rows:
        print(f"    {t:6.2f}s  scale {f:.2f}  {w:<34} {wmap.get(t, '')}")
    gaps = [q[0] - p[0] for p, q in zip(rows, rows[1:]) if p[3] == q[3]]   # within a stretch
    if gaps:
        print(f"  {len(rows)} steps, one every {sum(gaps) / len(gaps):.1f}s on average "
              f"(min {min(gaps):.1f}, max {max(gaps):.1f})")
    worst, starved = longest_quiet(regions, [r[0] for r in rows], cands, 2 * a.gap[1])
    if worst:
        print(f"  longest stretch without a change outside the blocked windows: "
              f"{worst[1] - worst[0]:.1f}s ({worst[0]:.2f}-{worst[1]:.2f})")
    bad += [f"{x:.2f}-{y:.2f}: {y - x:.1f}s without a change although a phrase boundary is "
            f"there — the planner left it empty" for x, y in starved]
    print(json.dumps(entries, ensure_ascii=False, indent=1))
    if bad:
        print("  ✗ " + "\n  ✗ ".join(bad))
        return 1

    if a.apply:
        if not os.path.exists(a.media):
            sys.exit(f"--apply: {a.media} not found")
        raw = json.load(open(a.media, encoding="utf-8"))
        os.makedirs("build", exist_ok=True)
        json.dump(raw, open("build/media.before_punches.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        old = [m for m in raw.get("moments", []) if m.get("_auto") and m.get("type") == "punch"]
        raw["moments"] = [m for m in raw.get("moments", []) if m not in old] + entries
        json.dump(raw, open(a.media, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"  --apply: {len(old)} old auto punch(es) replaced by {len(entries)} in {a.media} "
              f"(previous copy: build/media.before_punches.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
