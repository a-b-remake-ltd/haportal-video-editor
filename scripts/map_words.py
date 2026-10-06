#!/usr/bin/env python3
"""Carry the RAW transcript's words onto the cut A-roll's timeline.

Re-transcribing the finished A-roll gives the right timebase but often WORSE text: the
model hears each phrase with less context and the butt-joined segments confuse it
("ChatGPT" became "ChatGPGPT", "ג'מיני" became "ג'מימנאי" on a real test). The raw
transcript is the one you already audited and corrected. Its words just need moving
onto the new timeline, and the cut map in src/bounds.json says exactly how:

    out_time = segment.start + (raw_time - segment.src_start)

A word is kept when its midpoint falls inside a kept segment's source window. Words
from dropped takes simply have no segment and fall away.

Use the re-transcription of the final A-roll as a CHECK, not as the caption source:
`--verify src/aroll.json` lists every place the two disagree.

Usage
  python3 scripts/map_words.py --raw src/raw_words.json            # → src/words.json
  python3 scripts/map_words.py --raw src/raw.json --verify src/aroll.json
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402
from captions import load_words  # noqa: E402


NEAR = 0.35   # a word this close to a kept segment belongs to it


def map_words(words, segments):
    """Whisper's word stamps are loose at pause edges: a word often straddles the cut or
    sits just outside the segment's source window. Assign each word to the kept segment
    it overlaps most, or the nearest one within NEAR seconds. Only words far from every
    kept segment (a dropped take) fall away."""
    out = []
    for a, b, w in words:
        best, score = None, None
        for s in segments:
            ov = min(b, s["src_end"]) - max(a, s["src_start"])
            dist = max(s["src_start"] - b, a - s["src_end"], 0.0)
            k = (ov > 0, ov if ov > 0 else -dist)
            if (ov > 0 or dist <= NEAR) and (score is None or k > score):
                best, score = s, k
        seg = best
        if not seg:
            continue
        off = seg["start"] - seg["src_start"]
        lo, hi = seg["start"], seg["start"] + seg["dur"]
        out.append([round(max(lo, a + off), 3), round(min(hi, b + off), 3), w])
    return out


def _norm(w):
    return re.sub(r"[^\w֐-׿]", "", w).lower()


def verify(mapped, check):
    """Word-level diff (by sequence) between the mapped raw words and a re-transcription."""
    import difflib
    a = [_norm(w) for _, _, w in mapped]
    b = [_norm(w) for _, _, w in check]
    diffs = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        t = mapped[i1][0] if i1 < len(mapped) else (check[j1][0] if j1 < len(check) else 0)
        diffs.append((t, " ".join(w for _, _, w in mapped[i1:i2]),
                      " ".join(w for _, _, w in check[j1:j2])))
    return diffs


def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("--raw", required=True, help="raw transcript (words json or whisper json)")
    ap.add_argument("--bounds", default="src/bounds.json")
    ap.add_argument("--out", default="src/words.json")
    ap.add_argument("--verify", help="a re-transcription of the final A-roll to compare against")
    a = ap.parse_args()

    if not os.path.exists(a.bounds):
        sys.exit(f"{a.bounds} not found — run scripts/cut_aroll.py first")
    segs = json.load(open(a.bounds, encoding="utf-8")).get("segments")
    if not segs:
        sys.exit(f"{a.bounds} has no 'segments' — re-run scripts/cut_aroll.py")
    raw = load_words(a.raw)
    mapped = map_words(raw, segs)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(mapped, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(f"  {len(mapped)} of {len(raw)} raw words mapped onto {len(segs)} segments → {a.out}")

    if a.verify:
        diffs = verify(mapped, load_words(a.verify))
        if not diffs:
            print("  ✓ the re-transcription agrees word for word")
        else:
            print(f"  {len(diffs)} disagreement(s) — the raw text is used; check any that "
                  f"could be a clipped word or an SFX collision:")
            for t, x, y in diffs[:40]:
                print(f"    {t:7.2f}s  raw: {x or '—'}   |   final: {y or '—'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
