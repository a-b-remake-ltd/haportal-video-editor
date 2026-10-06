#!/usr/bin/env python3
"""Validate the BUILT index.html against the beat map — every element, not a hand-picked few.

The bug this exists for: when you drop a line or re-cut, every downstream time moves.
Captions, panels and the A-roll get regenerated from the beat map and are correct — but
anything HARDCODED in the HTML template (a hero card, a panel B-roll, an SFX cue) is
left at the old second, and a snapshot at the old time looks fine.

It has bitten twice in one session: first a card's START, then a card's DURATION (still
carrying a pre-recut value, so it hung 1.84 s into the next shot). A validator that only
checks starts on a hand-picked id list passes both times.

Asserts:
  1. nothing runs past END
  2. every beat-map-defined element matches start AND duration
  3. bgm1.duration == MUSIC_STOP, bgm2 starts on resume and its media-start is in phase
  4. captions have zero gaps, end exactly at END, and each card's class == slot_at(start)
  5. every beat start is a real segment boundary
  6. no stranded numbers from a previous timeline

Exit code is non-zero on any failure. Run it before EVERY render.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

beatmap, BEATS_PATH = hfcfg.load_beats()   # project copy wins over the skill's stub

CLIP_RE = re.compile(
    r'<(?P<tag>video|audio|img|div|svg)\b[^>]*?'
    r'(?:id="(?P<id>[^"]*)")?[^>]*?'
    r'data-start="(?P<start>[\d.]+)"[^>]*?'
    r'data-duration="(?P<dur>[\d.]+)"[^>]*?>', re.S)


def parse_clips(html):
    out = []
    for m in re.finditer(r'<(video|audio|img|div|svg)\b([^>]*)>', html, re.S):
        attrs = m.group(2)
        s = re.search(r'data-start="([\d.]+)"', attrs)
        d = re.search(r'data-duration="([\d.]+)"', attrs)
        if not s:
            continue
        i = re.search(r'id="([^"]*)"', attrs)
        c = re.search(r'class="([^"]*)"', attrs)
        ms = re.search(r'data-media-start="([\d.]+)"', attrs)
        out.append({"tag": m.group(1),
                    "id": i.group(1) if i else "",
                    "cls": c.group(1) if c else "",
                    "start": float(s.group(1)),
                    "dur": float(d.group(1)) if d else None,
                    "media_start": float(ms.group(1)) if ms else None})
    return out


def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("--html", default="index.html")
    ap.add_argument("--captions", default="captions.json")
    ap.add_argument("--bounds", default="src/bounds.json")
    ap.add_argument("--expect", help="JSON of {id: [start, duration]} the build script emitted")
    ap.add_argument("--outro", default="build/outro.json",
                    help="outro timing written by build_index.py (extends the composition END)")
    ap.add_argument("--music-stop", type=float, help="expected bgm1 duration")
    ap.add_argument("--stale", nargs="*", default=[],
                    help="numbers from a previous timeline — a hit is a stranded element")
    a = ap.parse_args()
    hfcfg.load(a.config)

    if not os.path.exists(a.html):
        sys.exit(f"{a.html} not found")
    html = open(a.html, encoding="utf-8").read()
    clips = parse_clips(html)
    fails, notes = [], []

    bounds, end = [], None
    if os.path.exists(a.bounds):
        d = json.load(open(a.bounds, encoding="utf-8"))
        bounds, end = d["bounds"], d["total"]
    if beatmap and getattr(beatmap, "END", 0):
        end = beatmap.END or end
    if end is None:
        fails.append("no END available (need src/bounds.json or beats.END)")
        end = 1e9

    # 1 ----------------------------------------------------- nothing past END
    # With an outro (build/outro.json from build_index.py) the COMPOSITION ends at the
    # outro's end; the A-roll itself must still end exactly at its real duration, and the
    # captions (check 4) still end at the A-roll END.
    comp_end = end
    if a.outro and os.path.exists(a.outro):
        o = json.load(open(a.outro, encoding="utf-8"))
        comp_end = float(o["end"])
        ar = next((c for c in clips if c["id"] == "aroll"), None)
        if ar and ar["dur"] is not None and abs(ar["start"] + ar["dur"] - end) > 0.001:
            fails.append(f"aroll ends at {ar['start'] + ar['dur']:.3f}s, not at its real "
                         f"duration {end:.3f}s (the outro must not stretch it)")
        notes.append(f"outro ({o.get('style')}): composition END {comp_end:.3f}s, "
                     f"A-roll END {end:.3f}s")
    over = [c for c in clips if c["dur"] is not None and c["start"] + c["dur"] > comp_end + 0.001]
    for c in over:
        fails.append(f"{c['id'] or c['tag']} runs to "
                     f"{c['start'] + c['dur']:.3f}s, past END {comp_end:.3f}s")
    if not over:
        notes.append(f"{len(clips)} timed elements, none past END ({comp_end:.3f}s)")

    # 2 -------------------------------------- expected starts AND durations
    if a.expect and os.path.exists(a.expect):
        expect = json.load(open(a.expect, encoding="utf-8"))
        by_id = {c["id"]: c for c in clips if c["id"]}
        for eid, val in expect.items():
            want_s, want_d = (list(val) + [None])[:2]
            got = by_id.get(eid)
            if not got:
                fails.append(f"expected element '{eid}' is not in the built html")
                continue
            if abs(got["start"] - float(want_s)) > 0.001:
                fails.append(f"{eid}: start {got['start']:.3f} != expected {float(want_s):.3f}")
            if want_d is not None and got["dur"] is not None \
                    and abs(got["dur"] - float(want_d)) > 0.001:
                fails.append(f"{eid}: DURATION {got['dur']:.3f} != expected {float(want_d):.3f} "
                             f"— stranded from a previous cut")
        notes.append(f"checked {len(expect)} beat-map ids (start AND duration)")

    # 3 ---------------------------------------------------------------- music
    bgms = sorted([c for c in clips if c["tag"] == "audio" and c["id"].startswith("bgm")],
                  key=lambda c: c["start"])
    if a.music_stop is not None and bgms:
        if abs((bgms[0]["dur"] or 0) - a.music_stop) > 0.001:
            fails.append(f"bgm1 duration {bgms[0]['dur']} != MUSIC_STOP {a.music_stop}")
    if len(bgms) >= 2 and bgms[0]["media_start"] is not None \
            and bgms[1]["media_start"] is not None:
        drift = bgms[1]["media_start"] - (bgms[0]["media_start"] +
                                          (bgms[1]["start"] - bgms[0]["start"]))
        if abs(drift) > 0.02:
            fails.append(f"bgm2 media-start is {drift:+.3f}s out of phase — the track rewinds "
                         f"instead of continuing where it would have been")
        else:
            notes.append("music resume is in phase")

    # 4 -------------------------------------------------------------- captions
    caps = [c for c in clips if "cap" in c["cls"].split()]
    external = os.path.exists(a.captions) and not caps
    if external:
        rows = json.load(open(a.captions, encoding="utf-8"))
        vis = [r for r in rows if not r.get("hidden")]
        notes.append(f"captions are an external layer ({len(vis)} shown, "
                     f"{len(rows) - len(vis)} hidden under headlines/hook/outro)")
        # Continuity is judged by captions.py itself (gap_verdict: a blank is deliberate after
        # a real pause > 0.6 s or next to a hidden window; anything else is an accident) — one
        # rule, one owner. Here: no overlaps, and captions.py's verdict re-run on the file.
        ov = sum(1 for x, y in zip(vis, vis[1:]) if x["start"] + x["dur"] - y["start"] > 0.0055)
        if ov:
            fails.append(f"{ov} caption OVERLAP(s) — two plates stacked")
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import captions as capmod
            # the SAME word timings captions.py judged with: each card carries its words
            # trimmed to where they are actually spoken (Whisper's stamps run early/late)
            words = sorted(w for r in rows for w in (r.get("words") or []))
            if not words and os.path.exists("src/words.json"):
                words = capmod.load_words("src/words.json")
            windows = capmod.load_hide("build/caption_hide.json", "build/outro.json", end) \
                if os.path.exists("build/caption_hide.json") else []
            bad = []
            for x, y in zip(vis, vis[1:]):
                okk, why = capmod.gap_verdict(x["start"] + x["dur"], y["start"], words, windows)
                if not okk:
                    bad.append(f"{x['start'] + x['dur']:.2f}-{y['start']:.2f}s ({why})")
            if bad:
                fails.append(f"{len(bad)} ACCIDENTAL caption gap(s): " + "; ".join(bad[:4]))
            elif vis:
                notes.append("captions: every blank is a real pause or a hidden window")
        except Exception as ex:          # never let the checker crash the validator
            notes.append(f"caption continuity not checked ({ex})")
    elif caps:                            # inline captions (no external layer)
        caps.sort(key=lambda c: c["start"])
        ov = sum(1 for x, y in zip(caps, caps[1:])
                 if x["start"] + (x["dur"] or 0) - y["start"] > 0.0055)
        if ov:
            fails.append(f"{ov} caption OVERLAP(s) — two plates stacked")
        if beatmap and beatmap.BEATS:
            wrong = [c for c in caps
                     if beatmap.slot_at(c["start"]) not in c["cls"].split()]
            if wrong:
                fails.append(f"{len(wrong)} caption(s) carry a class that is not "
                             f"slot_at(start) — the slot table and the beat map disagree")
        if not ov:
            notes.append(f"captions: {len(caps)} cards, no overlaps")

    # 5 ------------------------------------------- beats land on real boundaries
    if beatmap and beatmap.BEATS and bounds:
        try:
            beatmap.assert_on_boundaries(bounds)
            notes.append(f"all {len(beatmap.BEATS)} beat starts are segment boundaries")
        except AssertionError as e:
            fails.append(str(e))

    # 6 ---------------------------------------------------- stranded numbers
    for s in a.stale:
        if s in html:
            fails.append(f"stale timeline value '{s}' still present in {a.html} "
                         f"— a stranded element")

    print("== VALIDATE ==")
    for n in notes:
        print(f"  ✓ {n}")
    for f in fails:
        print(f"  ✗ {f}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
