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
  python3 scripts/map_words.py                                     # src/raw_words.json → src/words.json
  python3 scripts/map_words.py --raw src/raw.json                  # another raw transcript
  python3 scripts/map_words.py --verify src/aroll.json             # check src/words.json against
                                                                   # the A-roll's re-transcription
  python3 scripts/map_words.py --selftest

--raw defaults to src/raw_words.json (what xcheck.py writes). `--verify` on its own checks
the words.json already written and does not rewrite it (it used to fail with "--raw is
required" — the command workflow.md gives); with an explicit --raw it maps, writes, then
checks.
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
    ap.add_argument("--raw", default=None,
                    help="raw transcript (words json or whisper json); default src/raw_words.json")
    ap.add_argument("--bounds", default="src/bounds.json")
    ap.add_argument("--out", default="src/words.json")
    ap.add_argument("--verify", help="a re-transcription of the final A-roll to compare against")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.verify and not a.raw and os.path.exists(a.out):
        # check only: the words.json already written (and audited) against the re-run
        return report(load_words(a.out), a.verify, a.out)
    a.raw = a.raw or "src/raw_words.json"
    if not os.path.exists(a.raw):
        sys.exit(f"{a.raw} not found — run scripts/xcheck.py (it writes src/raw_words.json), "
                 f"or pass --raw <transcript>")

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
        report(mapped, a.verify, a.out)
    return 0


def report(mapped, check_path, label):
    if not os.path.exists(check_path):
        sys.exit(f"{check_path} not found — transcribe assets/aroll.mp4 first "
                 f"(transcribe.py --out {check_path})")
    diffs = verify(mapped, load_words(check_path))
    if not diffs:
        print(f"  ✓ {label} and the re-transcription agree word for word")
    else:
        print(f"  {len(diffs)} disagreement(s) between {label} and {check_path} — the raw "
              f"text is used; check any that could be a clipped word or an SFX collision:")
        for t, x, y in diffs[:40]:
            print(f"    {t:7.2f}s  raw: {x or '—'}   |   final: {y or '—'}")
    return 0


def selftest():
    """`--verify` alone must work (it failed with "--raw is required"). Runs the CLI in a
    temp project. Exit 1 on any failure."""
    import subprocess
    import tempfile
    tmp = tempfile.mkdtemp(prefix="map_words_")
    os.makedirs(os.path.join(tmp, "src"))
    json.dump({"segments": [{"start": 0.0, "dur": 2.0, "src_start": 1.0, "src_end": 3.0}],
               "bounds": [0.0], "total": 2.0}, open(os.path.join(tmp, "src", "bounds.json"), "w"))
    json.dump([[1.1, 1.5, "שלום"], [1.6, 2.2, "עולם"], [5.0, 5.4, "נחתך"]],
              open(os.path.join(tmp, "src", "raw_words.json"), "w"), ensure_ascii=False)
    json.dump([[0.1, 0.5, "שלום"], [0.6, 1.2, "עולמם"]],
              open(os.path.join(tmp, "src", "aroll.json"), "w"), ensure_ascii=False)
    me = os.path.abspath(__file__)
    fails = []

    def run(*args):
        return subprocess.run([sys.executable, me, *args], cwd=tmp, capture_output=True, text=True)
    r1 = run()
    ok = r1.returncode == 0 and os.path.exists(os.path.join(tmp, "src", "words.json"))
    print(f"  {'✓' if ok else '✗'} no arguments: src/raw_words.json → src/words.json")
    fails += [] if ok else ["default raw"]
    mapped = json.load(open(os.path.join(tmp, "src", "words.json"))) if ok else []
    ok = [w[2] for w in mapped] == ["שלום", "עולם"] and abs(mapped[0][0] - 0.1) < 1e-6
    print(f"  {'✓' if ok else '✗'} words moved onto the cut timeline, the dropped take falls away")
    fails += [] if ok else ["mapping"]
    r2 = run("--verify", "src/aroll.json")
    ok = r2.returncode == 0 and "1 disagreement" in r2.stdout
    print(f"  {'✓' if ok else '✗'} --verify alone works (was: --raw is required) and finds the diff")
    fails += [] if ok else ["verify alone: " + r2.stdout + r2.stderr]
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"  {'all passed' if not fails else str(len(fails)) + ' FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
