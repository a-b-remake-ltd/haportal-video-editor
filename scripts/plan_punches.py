#!/usr/bin/env python3
"""Plan the camera punch-ins — a two-camera edit made from one take.

    python3 scripts/plan_punches.py --key "word,another phrase"      # print the plan
    python3 scripts/plan_punches.py --keys punches.json --apply       # write it into media.json

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
  * never inside the hook window, a footage moment (paper / fly: they drive the A-roll's
    transform themselves), a hand-made punch moment, a hidden window whose source is the
    hook / world / outro, or the outro. Each free stretch between those becomes its own
    punch moment, which hands the frame back at scale 1 (× the beat's scale) at its end
  * ROTATION / SWAY: rotating the frame exposes black corners unless it is scaled up, so any
    rotation or sway on the footage MUST ride on a scale ≥ 1.07. This planner emits none;
    preflight_qa.py checks every rotation tween on the footage in index.html against the
    scale active at that moment and fails below 1.07.

Inputs   src/words.json, src/bounds.json, captions.json (phrase starts), build/caption_hide.json,
         build/outro.json, media.json (hook, moments)
Key words  --key "w1,two words" and/or --keys punches.json: {"key": [...], "block": [[a, b]]}
Output   one "punch" moment per free stretch, "_auto": true; --apply replaces the previous
         auto punches in media.json (hand-made ones are kept and blocked around).
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


def blocked_windows(media, hide, outro, end, extra):
    """[(a, b, why)] where no step may land."""
    out = []
    hook = media.get("hook")
    if hook and hook.get("end") is not None:
        out.append((0.0, float(hook["end"]), "hook"))
    for m in media.get("moments") or []:
        t = m.get("type")
        if t in FOOTAGE_TYPES:
            out.append((float(m["start"]), float(m["end"]), f"{m.get('id', t)} ({t})"))
        elif t == "punch" and not m.get("_auto"):
            out.append((float(m["start"]), float(m["end"]), f"{m.get('id')} (hand-made punch)"))
    for (a, b), src in zip(hide.get("windows", []), hide.get("sources", [[]] * 999)):
        srcs = " ".join(map(str, src if isinstance(src, list) else [src])).lower()
        if any(k in srcs for k in BLOCK_SOURCES):
            out.append((float(a), float(b), f"hidden window ({srcs})"))
    if outro and outro.get("start") is not None:
        out.append((float(outro["start"]), max(end, float(outro.get("end") or end)), "outro"))
    for a, b in extra:
        out.append((float(a), float(b), "punches.json block"))
    return sorted(out)


def free_regions(blocked, end, min_len=2.5):
    regions, t = [], 0.0
    for a, b, _ in blocked:
        if a > t + min_len:
            regions.append((t, a))
        t = max(t, b)
    if end > t + min_len:
        regions.append((t, end))
    return regions


def plan_region(r0, r1, cands, keys, gmin, gmax, kmin=1.0):
    """Steps [(t, factor, why)] for one free stretch."""
    cands = [c for c in cands if r0 + 0.3 <= c <= r1 - gmin + 0.5]
    keys = [(t, k) for t, k in keys if r0 + 0.3 <= t <= r1 - 1.0]
    steps, cur, t_last, ri, ki = [], 1.0, r0, 0, 0
    used_keys = set()
    while True:
        nk = next(((t, k) for t, k in keys if t >= t_last + kmin and (t, k) not in used_keys), None)
        if nk and nk[0] - t_last <= gmax:
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
        pool = [c for c in cands if lo <= c <= hi and (not nk or c <= nk[0] - kmin)]
        if not pool:
            pool = [c for c in cands if c > hi and (not nk or c <= nk[0] - kmin)][:1]
        if not pool:
            if nk:                          # nothing fits before the key word: jump to it
                t_last = max(t_last, nk[0] - gmax)
                continue
            break
        c = min(pool, key=lambda x: abs(x - (t_last + 3.0)))
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
    ap.add_argument("--apply", action="store_true", help="write the punches into media.json")
    a = ap.parse_args()

    words = load_words(a.words)
    bd = json.load(open(a.bounds, encoding="utf-8")) if os.path.exists(a.bounds) else {}
    bounds, end = bd.get("bounds", [0.0]), float(bd.get("total") or words[-1][1])
    caps = json.load(open(a.captions, encoding="utf-8")) if os.path.exists(a.captions) else []
    hide = json.load(open(a.hide, encoding="utf-8")) if os.path.exists(a.hide) else {}
    outro = json.load(open(a.outro, encoding="utf-8")) if os.path.exists(a.outro) else None
    media = json.load(open(a.media, encoding="utf-8")) if os.path.exists(a.media) else {}
    keys = [k.strip() for k in a.key.split(",") if k.strip()]
    extra = []
    if a.keys and os.path.exists(a.keys):
        kj = json.load(open(a.keys, encoding="utf-8"))
        keys += list(kj.get("key", []) if isinstance(kj, dict) else kj)
        extra = kj.get("block", []) if isinstance(kj, dict) else []

    blocked = blocked_windows(media, hide, outro, end, extra)
    last_word = max(w[1] for w in words)
    regions = free_regions(blocked, min(end, last_word + 0.3))
    cands = phrase_starts(words, bounds, caps)
    kps = key_points(words, keys)
    entries, rows = [], []
    for n, (r0, r1) in enumerate(regions, 1):
        steps = plan_region(r0, r1, cands, kps, a.gap[0], a.gap[1])
        if not steps:
            continue
        e = {"id": f"punch_auto_{n}", "type": "punch", "start": snap(r0), "end": snap(r1),
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
    if len(ts) > 1:
        gaps = [q - p for p, q in zip(ts, ts[1:])]
        print(f"  {len(rows)} steps, one every {sum(gaps) / len(gaps):.1f}s on average "
              f"(min {min(gaps):.1f}, max {max(gaps):.1f})")
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
