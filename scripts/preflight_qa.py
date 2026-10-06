#!/usr/bin/env python3
"""Preflight QA — run BEFORE showing anyone anything.

Catches the mistake classes that actually come back as notes: dead space, frame drift,
caption gaps and overlaps, two-line captions, missing spoken words, a card ending on a
sticky word, an unprotected "AI", anything under the Reels UI (grid), a non-free font,
low bitrate, audio clipping, wrong loudness, an HDR-tagged master.

A gate beats a rule. When a note repeats, add a check here rather than another line of
prose — and run the negative test when you add one. A check you have never seen fail is
not a check.

Usage
  python3 scripts/preflight_qa.py <project_dir> \
      [--aroll assets/aroll.mp4] [--transcript src/words.json] [--render renders/final.mp4]
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

FRAME = 0.04
issues, ok = [], []


def check_dead_space(aroll):
    """Never pass -v error here — it suppresses silencedetect output entirely and the
    probe comes back looking like a missing file."""
    out = hfcfg.run(["ffmpeg", "-nostdin", "-i", aroll,
                     "-af", "silencedetect=noise=-33dB:d=0.3", "-f", "null", "-"]).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", out)]
    durs = [float(x) for x in re.findall(r"silence_duration: ([0-9.]+)", out)]
    total = float(hfcfg.probe(aroll, "format=duration") or 0)
    hits = list(zip(starts, durs))
    # The CLOSING tail (~0.45 s) is deliberate — without it the final word reads as
    # clipped. Any silence run that reaches the end of the file is that tail, not a gap.
    bad = [(t, d) for t, d in hits if d > 0.42 and (total - (t + d)) > 0.10]
    tail = [(t, d) for t, d in hits if (total - (t + d)) <= 0.10]
    if bad:
        issues.append("DEAD SPACE in %s: %s" % (
            os.path.basename(aroll),
            ", ".join(f"{t:.2f}s ({d:.2f}s long)" for t, d in bad)))
    else:
        note = f"dead space: clean ({len(hits) - len(tail)} short breaths ≤0.42s)"
        if tail:
            note += f", closing tail {tail[-1][1]:.2f}s"
        ok.append(note)
    if tail and tail[-1][1] < 0.30:
        issues.append(f"CLOSING TAIL only {tail[-1][1]:.2f}s — the last word will read as "
                      f"clipped; the closing segment takes ~0.45s")


def check_drift(segments_dir="segments"):
    """Frame-quantisation drift is the cause of the most-repeated note in this pipeline:
    a segment whose duration is not a whole frame encodes LONGER than planned, so every
    later layout beat fires early. Count PACKETS, not seconds — format=duration reports
    the AAC tail and makes a perfect concat look 0.02s long."""
    segs = sorted(glob.glob(os.path.join(segments_dir, "*.mp4")))
    segs = [s for s in segs if not s.endswith("list.txt")]
    if not segs:
        return
    total_frames, bad = 0, []
    for f in segs:
        n = hfcfg.frames(f)
        total_frames += n
        d = float(hfcfg.probe(f, "stream=duration", "v:0") or 0)
        if abs(d / FRAME - round(d / FRAME)) > 1e-3:
            bad.append(f"{os.path.basename(f)} is {d:.4f}s — not a whole {FRAME}s frame")
    if bad:
        issues.append("SEGMENT NOT FRAME-ALIGNED (causes cut drift): " + "; ".join(bad[:3]))
    cum = round(total_frames * FRAME, 4)
    if os.path.exists("src/bounds.json"):
        planned = json.load(open("src/bounds.json"))
        if abs(planned.get("total", cum) - cum) > 0.001:
            issues.append(f"TIMELINE DRIFT: planned {planned['total']:.3f}s vs real {cum:.3f}s "
                          f"— every layout beat after the first is off")
        else:
            ok.append(f"segment timing: frame-exact, planned == real ({cum:.3f}s)")
    elif not bad:
        ok.append(f"segment timing: all frame-aligned ({cum:.3f}s)")


def _caption_rows(index_html, captions_json):
    """Captions may be composited as an external alpha layer instead of living in the
    composition — fall back to captions.json so these checks still run."""
    rows = []
    if os.path.exists(index_html):
        html = open(index_html, encoding="utf-8").read()
        for m in re.finditer(r'class="([^"]*\bcap\b[^"]*)"[^>]*data-start="([\d.]+)"'
                             r'[^>]*data-duration="([\d.]+)"[^>]*>(.*?)</div>', html, re.S):
            rows.append({"cls": m.group(1), "start": float(m.group(2)),
                         "dur": float(m.group(3)), "text": m.group(4)})
    if not rows and os.path.exists(captions_json):
        for r in json.load(open(captions_json, encoding="utf-8")):
            rows.append({"cls": "", "start": r["start"], "dur": r["dur"], "text": r["text"]})
    return sorted(rows, key=lambda r: r["start"])


def check_caption_continuity(index_html="index.html", captions_json="captions.json"):
    rows = _caption_rows(index_html, captions_json)
    if not rows:
        issues.append("NO CAPTIONS FOUND in the composition and no captions.json to fall back on")
        return
    gaps = ov = 0
    for x, y in zip(rows, rows[1:]):
        e = x["start"] + x["dur"]
        if y["start"] - e > 0.0055:
            gaps += 1
        if e - y["start"] > 0.0055:
            ov += 1
    if gaps:
        issues.append(f"CAPTION GAP on {gaps} boundaries — blank frames with no caption")
    if ov:
        issues.append(f"CAPTION OVERLAP on {ov} boundaries — two plates stacked for a frame")
    multi = [r for r in rows if r["text"].count("<br") or r["text"].count('class="l"') > 1]
    if multi:
        issues.append(f"{len(multi)} caption card(s) have TWO LINES — one line only, always")
    # tags removed WITHOUT a space: "ה-<span class=ltr>AI</span>" is one word, not two
    words = [len(re.sub(r"<[^>]+>", "", r["text"]).split()) for r in rows]
    fat = [n for n in words if n > 4]
    if fat:
        issues.append(f"{len(fat)} caption card(s) over 4 words (max seen: {max(words)})")
    if not (gaps or ov or multi or fat):
        ok.append(f"captions: {len(rows)} cards, no gaps, no overlaps, single-line, ≤4 words")


def check_words_covered(captions_json, transcript_json, typos=None):
    """Every spoken word must appear in a caption — verbatim is the rule.

    Both sides are cut into the SAME tokens (letters/digits runs, HTML unescaped), so
    "מ-100" in a caption and "מ" + "-100" in the transcript, or "ג&#x27;מיני" and
    "ג'מיני", compare equal. Then a sequence diff finds words the captions dropped."""
    if not (os.path.exists(captions_json) and os.path.exists(transcript_json)):
        return
    import difflib
    import html as _html
    typos = typos or {}
    tok = re.compile(r"[\w\u0590-\u05ff]+", re.UNICODE)

    def tokens(text):
        out = []
        for t in tok.findall(_html.unescape(text)):
            out.extend(tok.findall(typos.get(t, t)))
        return out

    caps = json.load(open(captions_json, encoding="utf-8"))
    cap_toks = tokens(" ".join(re.sub(r"<[^>]+>", "", c["text"]) for c in caps))
    d = json.load(open(transcript_json, encoding="utf-8"))
    if isinstance(d, dict) and "segments" in d:
        spoken_text = " ".join(str(w.get("word", "")) for sg in d["segments"]
                               for w in sg.get("words", []))
    else:
        spoken_text = " ".join(str(x[2]) for x in d)
    spoken = tokens(spoken_text)
    missing = []
    for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, spoken, cap_toks,
                                                     autojunk=False).get_opcodes():
        if tag in ("delete", "replace"):
            missing.extend(spoken[i1:i2])
    if missing:
        issues.append(f"CAPTION WORDS missing or changed vs the transcript: {missing[:12]}")
    else:
        ok.append(f"captions: all {len(spoken)} spoken tokens covered, in order")


def check_caption_language(cfg, captions_json="captions.json"):
    """Hebrew caption rules (references/hebrew.md) on the FINAL card text."""
    if not os.path.exists(captions_json):
        return
    import captions as capmod
    caps = json.load(open(captions_json, encoding="utf-8"))
    sticky = capmod.sticky_set(cfg["language"])
    dangling = [c for c in caps[:-1]
                if c.get("n", 2) > 1 and not c.get("sticky_ok")
                and capmod.is_sticky(c["plain"].split()[-1], sticky)]
    if dangling:
        issues.append("CAPTION ENDS ON A STICKY WORD (reads broken): " +
                      "; ".join(f"c{c['i']:02d} '{c['plain']}'" for c in dangling[:5]))
    bare = [c for c in caps if re.search(r"(?<![\w>])AI(?![\w<])", re.sub(
        r'<span class="ltr ai">AI</span>', "", c["text"]))]
    if bare and cfg["language"].get("direction") == "rtl":
        issues.append(f"'AI' without class=\"ltr ai\" in {len(bare)} card(s) — it renders as "
                      f"'Al' in heavy Hebrew faces")
    if not dangling and not bare:
        ok.append("captions: no sticky endings, every 'AI' protected")


def check_grid(cfg, index_html="index.html"):
    if not os.path.exists(index_html):
        return
    import grid
    beatmap, _ = hfcfg.load_beats()
    g = grid.from_config(cfg)
    slots = dict(getattr(beatmap, "SLOT", {}) or {}) if beatmap else {}
    try:
        found, n, nt = grid.check(index_html, g, None, slots,
                                  grid.plate_height(cfg["brand"]["caption_size"]),
                                  g["safe_width"])
    except SystemExit as e:
        issues.append(f"GRID CHECK could not run: {e}")
        return
    if found:
        issues.append(f"UNDER THE REELS UI ({len(found)}): " + " | ".join(found[:4]) +
                      "  — python3 scripts/grid.py check index.html")
    else:
        ok.append(f"grid ({g['name']}): {n} visible elements over {nt} times, all inside the safe zone")


def check_fonts(cfg, index_html="index.html"):
    try:
        import fonts
    except ImportError:
        return
    files = [f for f in (index_html, cfg["brand"].get("css", "")) if f and os.path.exists(f)]
    if not files:
        return
    found = fonts.guard(files, cfg["brand"]["font_dir"])
    if found:
        issues.append("NON-FREE OR UNLICENSED FONT: " + "; ".join(found[:4]))
    else:
        ok.append("fonts: every face is free for commercial use")


def check_loudness(render, cfg):
    out = hfcfg.run(["ffmpeg", "-nostdin", "-i", render, "-af", "ebur128=peak=true",
                     "-f", "null", "-"]).stderr
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", out)
    tp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", out)
    if not i:
        return
    lufs = float(i[-1])
    target = cfg["render"].get("target_lufs", -14.0)
    peak = float(tp[-1]) if tp else None
    if abs(lufs - target) > 1.5:
        issues.append(f"LOUDNESS {lufs:.1f} LUFS (target {target:.0f} ±1.5) — compress gently "
                      f"before the limiter")
    else:
        ok.append(f"loudness: {lufs:.1f} LUFS" + (f", true peak {peak:.1f} dBTP" if peak is not None else ""))
    if peak is not None and peak > -1.0:
        issues.append(f"TRUE PEAK {peak:.1f} dBTP > -1 — it will clip after platform transcoding")


def check_render(render, cfg):
    br_s = hfcfg.probe(render, "format=bit_rate")
    if br_s:
        br = int(br_s)
        if br < 28_000_000:
            issues.append(f"RENDER BITRATE {br/1e6:.1f} Mbps < 30-35 target — render with "
                          f"--video-bitrate {cfg['render']['video_bitrate']}")
        else:
            ok.append(f"render bitrate: {br/1e6:.1f} Mbps")
    vol = hfcfg.run(["ffmpeg", "-nostdin", "-i", render, "-af", "volumedetect",
                     "-f", "null", "-"]).stderr
    m = re.search(r"max_volume: ([-0-9.]+) dB", vol)
    if m:
        peak = float(m.group(1))
        if peak > -0.3:
            issues.append(f"AUDIO CLIPPING risk: max {peak} dB — fix with volume=-2dB on the "
                          f"A-roll (never alimiter: its makeup gain puts the peak straight back)")
        elif peak < -4.0:
            issues.append(f"AUDIO TOO QUIET: max {peak} dB (target -1 to -2)")
        else:
            ok.append(f"audio peak: {peak} dB")
    colours = hfcfg.probe(render, "stream=color_space,color_transfer,color_primaries", "v:0")
    bad = [t for t in ("bt2020", "arib-std-b67", "smpte2084") if t in colours]
    if bad:
        issues.append(f"MASTER IS HDR-TAGGED {bad} — every player will apply an HDR→SDR "
                      f"transform and it will look washed out. Run scripts/finish.py")
    else:
        ok.append("colour: SDR bt709")


def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("project", nargs="?", default=".")
    ap.add_argument("--aroll")
    ap.add_argument("--transcript")
    ap.add_argument("--render")
    ap.add_argument("--captions", default="captions.json")
    ap.add_argument("--html", default="index.html")
    a = ap.parse_args()
    cfg = hfcfg.load(a.config)
    hfcfg.require("ffmpeg", "ffprobe")
    os.chdir(a.project)

    check_drift()
    check_caption_continuity(a.html, a.captions)
    check_caption_language(cfg, a.captions)
    check_grid(cfg, a.html)
    check_fonts(cfg, a.html)
    if a.transcript:
        check_words_covered(a.captions, a.transcript, cfg["language"].get("typos"))
    if a.aroll:
        check_dead_space(a.aroll)
    if a.render:
        check_render(a.render, cfg)
        check_loudness(a.render, cfg)

    print("== PREFLIGHT QA ==")
    for o in ok:
        print(f"  ✓ {o}")
    for i in issues:
        print(f"  ✗ {i}")
    if not issues:
        print("\n  clean — now walk references/checklist.md before you show it to anyone")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
