#!/usr/bin/env python3
"""Preflight QA — run BEFORE showing anyone anything.

Catches the mistake classes that actually come back as notes: dead space, frame drift,
caption gaps and overlaps, two-line captions, missing spoken words, a card ending on a
sticky word, an unprotected "AI", anything under the Reels UI (grid), a non-free font,
low bitrate, audio clipping, wrong loudness, an HDR-tagged master — and the motion-edit
gates of references/qa.md:

  captions    1-N words; a gap is fine only when it is a real pause (> 0.6 s, the card kept
              its 0.3 s tail) or sits under a hidden window; NO visible card starts inside a
              hidden window (build/caption_hide.json + the outro)
  on-screen   no dashes (number ranges excepted; a prefix hyphen as in "ב-AI" is not a dash),
              no emoji — in captions, headlines and every widget text in index.html
  CSS         class-name collisions across scenes; heavy overlays (filter blur, radial
              gradients, clip-paths: ≥ 40 elements renders black — counted in Chrome,
              hidden ones included); every local asset the page loads exists
  camera      any rotation / sway on the footage rides on a scale ≥ 1.07 (black corners)
  A-roll      audio as long as the video (± 1 frame); every cut boundary within 10 ms of the
              raw (cross-correlated); no source audio used twice; master fps == config
              project.fps == A-roll fps
  render      freezedetect=n=0.002:d=0.6 and blackdetect=d=0.2:pix_th=0.05 report nothing;
              loudness −14 ± 0.4 LUFS, true peak ≤ −1.0 dBTP (target ≈ −1.3)
  intelligibility  the master's speech re-transcribed with the same engine + glossary,
              compared word by word (≥ 97 %); every differing word listed with the SFX /
              music events nearest to it, read from index.html
  density     (warnings) a hook, 8-12 designed moments, 5-7 headlines, a callback when
              storyboard.md declares one

`--checklist` prints the spec's final checklist (references/qa.md) with ✓ / ✗ / ? per item,
decided from these measurements; "?" = only a human look can decide it.

A gate beats a rule. When a note repeats, add a check here rather than another line of
prose — and run the negative test when you add one. A check you have never seen fail is
not a check.

Usage
  python3 scripts/preflight_qa.py <project_dir> \
      [--aroll assets/aroll.mp4] [--transcript src/words.json] [--render renders/final.mp4]
      [--checklist] [--no-intelligibility]
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

FRAME = 0.04
issues, ok, warns = [], [], []
M = {}          # measurements for --checklist: item -> (True | False | None, note)


def mark(item, good, note=""):
    """Record a checklist measurement. An item failing anywhere stays failed; a measured
    pass is not downgraded by a "can't tell" from another check; notes accumulate."""
    prev = M.get(item)
    if prev:
        notes = "; ".join(x for x in (prev[1], note) if x)
        if prev[0] is False or (prev[0] is True and good is None):
            good = prev[0]
        M[item] = (good, notes)
        return
    M[item] = (good, note)


def check_dead_space(aroll):
    """Never pass -v error here — it suppresses silencedetect output entirely and the
    probe comes back looking like a missing file.

    A take kept WHOLE (cut_aroll.py --whole: an AI avatar or a clean single take) keeps the
    speaker's own pauses on purpose — cutting frames out of an avatar makes it jump — so
    pauses are reported, not failed, there."""
    whole = False
    if os.path.exists("src/bounds.json"):
        bj = json.load(open("src/bounds.json"))
        # "mode" since the sample-exact cutter; older bounds: one segment + fps = whole
        whole = bj.get("mode") == "whole" or (
            "mode" not in bj and len(bj.get("segments", [])) == 1 and "fps" in bj)
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
        (warns if whole else issues).append(("kept-whole take, natural pauses: " if whole else "")
                                            + "DEAD SPACE in %s: %s" % (
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


def check_aroll_sync(aroll):
    """GATES on the A-roll's lip sync (cut_aroll.py runs the same ones at cut time):

      * the audio stream is as long as the video, to within one frame — an A-roll whose
        audio outlasts its picture by ~20 ms per join was cut with per-segment AAC
        joined by -c copy, and its voice slides later behind the lips at every cut
      * at every segment boundary, a 0.5 s window of the A-roll's audio sits within
        10 ms of the same window in the raw (cross-correlation, src/bounds.json → src)
      * no two segments share source audio (a word heard twice at a join)

    Counting video packets cannot see any of this: the frame count of a drifting
    A-roll is perfect."""
    import cut_aroll as cut
    if not os.path.exists(aroll):
        return
    bj = json.load(open("src/bounds.json")) if os.path.exists("src/bounds.json") else {}
    segs = bj.get("segments") or []
    src = bj.get("src")
    if segs and not src:
        warns.append("src/bounds.json has no 'src' (cut by an older cut_aroll.py) — lip sync at "
                     "the boundaries cannot be verified; re-cut with the current cut_aroll.py")
    elif src and not os.path.exists(src):
        warns.append(f"the raw ({src}) is not here — lip sync at the boundaries not verified")
    bad = cut.sync_problems(aroll, src if src and os.path.exists(src) else None, segs,
                            verbose=False)
    if all("src_start" in s and "src_end" in s for s in segs):
        bad += cut.overlap_problems(segs)[0]
    if bad:
        issues.append("A-ROLL OUT OF SYNC (re-cut with scripts/cut_aroll.py): " + "; ".join(bad[:4]))
    else:
        ok.append(f"A-roll A/V: audio as long as the video"
                  + (f", every one of {len(segs)} boundaries within "
                     f"{cut.SYNC_TOL * 1000:.0f} ms of the raw" if src and os.path.exists(src) else ""))
    mark(11, not bad, "A-roll lip sync " + ("out" if bad else "in sync"))


def check_fps(cfg, aroll=None, render=None):
    """GATE: one frame rate end to end — the master's == config project.fps == the
    A-roll's (and src/bounds.json's). `hyperframes render` without --fps falls back to
    30, so a 25 fps A-roll rendered without it is resampled: frames repeat, every beat
    snapped to a 0.04 s grid lands between frames, and the motion judders."""
    import cut_aroll as cut
    want = float(cfg["project"].get("fps", 25))
    got = {}
    if aroll and os.path.exists(aroll):
        got["A-roll"] = cut.probe_fps(aroll)
    if render and os.path.exists(render):
        got["master"] = cut.probe_fps(render)
    if os.path.exists("src/bounds.json"):
        f = json.load(open("src/bounds.json")).get("fps")
        if f:
            got["src/bounds.json"] = float(f)
    if not got:
        return
    bad = {k: v for k, v in got.items() if abs(v - want) > 0.01}
    if bad:
        issues.append(f"FPS MISMATCH: config project.fps is {want:g}, but "
                      + ", ".join(f"the {k} is {v:g}" for k, v in bad.items())
                      + " — set project.fps to the A-roll's rate and render with "
                        "`--fps <project.fps>`")
    else:
        ok.append(f"fps: {want:g} everywhere (" + ", ".join(got) + ")")
    mark(11, not bad, f"fps {want:g}" + (" mismatch" if bad else ""))


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


def _hide_windows():
    import captions as capmod
    return capmod.load_hide("build/caption_hide.json", "build/outro.json")


def check_caption_continuity(cfg, index_html="index.html", captions_json="captions.json",
                             transcript=None):
    """Gaps are judged, not banned: the card is trimmed on purpose after a pause > 0.6 s, and
    nothing shows under a hidden window. A blank while someone speaks, or a blink inside a
    short pause, is still a failure (captions.gap_verdict, shared with captions.py)."""
    import captions as capmod
    rows = _caption_rows(index_html, captions_json)
    if not rows:
        issues.append("NO CAPTIONS FOUND in the composition and no captions.json to fall back on")
        mark(5, False, "no captions")
        return
    cc = cfg.get("captions", {})
    maxw = int(cc.get("max_words", 3))
    windows = _hide_windows()
    full = json.load(open(captions_json, encoding="utf-8")) if os.path.exists(captions_json) else []
    hidden_starts = {round(float(r["start"]), 3) for r in full if r.get("hidden")}
    vis = [r for r in rows if round(r["start"], 3) not in hidden_starts]
    words = [w for r in full for w in (r.get("words") or [])]
    if not words and transcript and os.path.exists(transcript):
        words = capmod.load_words(transcript)
    gaps, ov = [], 0
    for x, y in zip(vis, vis[1:]):
        e = x["start"] + x["dur"]
        if e - y["start"] > 0.0055:
            ov += 1
            continue
        good, why = capmod.gap_verdict(e, y["start"], words, windows,
                                       float(cc.get("pause_trim", 0.6)),
                                       float(cc.get("pause_tail", 0.3)))
        if not good:
            gaps.append(why)
    if gaps:
        issues.append(f"CAPTION GAP on {len(gaps)} boundaries (not a pause, not a hidden window): "
                      + "; ".join(gaps[:4]))
    if ov:
        issues.append(f"CAPTION OVERLAP on {ov} boundaries — two cards stacked for a frame")
    multi = [r for r in rows if r["text"].count("<br") or r["text"].count('class="l"') > 1]
    if multi:
        issues.append(f"{len(multi)} caption card(s) have TWO LINES — one line only, always")
    # tags removed WITHOUT a space: "ה-<span class=ltr>AI</span>" is one word, not two
    nwords = [len(re.sub(r"<[^>]+>", "", r["text"]).split()) for r in rows]
    fat = [n for n in nwords if n > maxw]
    if fat:
        issues.append(f"{len(fat)} caption card(s) over {maxw} words (max seen: {max(nwords)})")
    if not (gaps or ov or multi or fat):
        ok.append(f"captions: {len(vis)} visible cards (+{len(rows) - len(vis)} hidden), "
                  f"1-{maxw} words, single-line, no accidental gaps, no overlaps")
    mark(5, not (gaps or ov or multi or fat),
         f"{len(vis)} cards, max {max(nwords)} words" + (f", {len(gaps)} bad gaps" if gaps else ""))


def check_caption_windows(captions_json="captions.json"):
    """GATE: no visible caption STARTS inside a hidden window (the hook world, a headline —
    the headline IS the caption there —, a moment that owns the frame, the outro)."""
    import captions as capmod
    if not os.path.exists(captions_json):
        return
    windows = _hide_windows()
    rows = json.load(open(captions_json, encoding="utf-8"))
    vis = [r for r in rows if not r.get("hidden")]
    bad = [r for r in vis if capmod.inside(float(r["start"]), windows)]
    cross = [r for r in vis if any(float(r["start"]) < a < float(r["start"]) + float(r["dur"]) - 0.011
                                   for a, _ in windows)]
    if bad:
        issues.append("CAPTION STARTS INSIDE A HIDDEN WINDOW: " + "; ".join(
            f"c{r['i']:02d} {float(r['start']):.2f}s '{r['plain']}'" for r in bad[:5])
            + " — re-run captions.py after build_index.py")
    elif cross:
        warns.append(f"{len(cross)} caption card(s) run INTO a hidden window (captions.json is "
                     f"stale; caption_layer.py trims them) — re-run captions.py")
    else:
        ok.append(f"captions vs {len(windows)} hidden window(s): none starts or runs inside one")
    if not os.path.exists("build/caption_hide.json"):
        warns.append("no build/caption_hide.json — captions are not hidden under headlines / "
                     "hook / moments (run build_index.py, then captions.py)")
    mark(5, not bad, f"{len(windows)} hidden windows")


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
    mark(2, not found, f"grid: {len(found)} element(s) under the Reels UI")


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
    """−14 ± 0.4 LUFS integrated, true peak ≤ −1.0 dBTP (the master targets ≈ −1.3)."""
    out = hfcfg.run(["ffmpeg", "-nostdin", "-i", render, "-af", "ebur128=peak=true",
                     "-f", "null", "-"]).stderr
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", out)
    tp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", out)
    if not i:
        return
    lufs = float(i[-1])
    target = cfg["render"].get("target_lufs", -14.0)
    peak = float(tp[-1]) if tp else None
    good = abs(lufs - target) <= 0.4
    if not good:
        issues.append(f"LOUDNESS {lufs:.1f} LUFS (target {target:.0f} ± 0.4) — adjust the "
                      f"pre-limiter target in finish.py and re-master")
    else:
        ok.append(f"loudness: {lufs:.1f} LUFS" + (f", true peak {peak:.1f} dBTP" if peak is not None else ""))
    if peak is not None and peak > -1.0:
        issues.append(f"TRUE PEAK {peak:.1f} dBTP > -1 — it will clip after platform transcoding")
        good = False
    elif peak is not None and peak < -2.5:
        warns.append(f"true peak {peak:.1f} dBTP — far under the ≈ −1.3 target (over-limited?)")
    M["lufs"] = lufs
    mark(11, good, f"{lufs:.1f} LUFS, TP {peak}")


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


# ====================================================================== render scans
def check_freeze_black(render):
    """freezedetect=n=0.002:d=0.6 (nothing static > 0.6 s) and blackdetect=d=0.2:pix_th=0.05
    (no black stretch: also catches the heavy-overlay capture failure that renders the first
    half black). One decode pass for both."""
    out = hfcfg.run(["ffmpeg", "-nostdin", "-i", render, "-map", "0:v:0", "-vf",
                     "freezedetect=n=0.002:d=0.6,blackdetect=d=0.2:pix_th=0.05",
                     "-f", "null", "-"]).stderr
    fs = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", out)]
    fd = [float(x) for x in re.findall(r"freeze_duration: ([\d.]+)", out)]
    fe = re.findall(r"freeze_end: ([\d.]+)", out)
    bl = re.findall(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)", out)
    if fs:
        parts = []
        for k, t in enumerate(fs):
            d = fd[k] if k < len(fd) else None
            parts.append(f"{t:.2f}s" + (f" ({d:.2f}s)" if d else " (to the end)"))
        issues.append("FROZEN FRAMES (nothing changes for > 0.6 s): " + ", ".join(parts[:8])
                      + " — add drift, an earlier entrance or a punch there")
    else:
        ok.append("freezedetect n=0.002 d=0.6: nothing static")
    if bl:
        issues.append("BLACK FRAMES: " + ", ".join(f"{float(a):.2f}-{float(b):.2f}s" for a, b, _ in bl[:6])
                      + " — a heavy-overlay overload renders black; so does a missing asset")
    else:
        ok.append("blackdetect d=0.2 pix_th=0.05: no black stretch")
    M["freezes"], M["black"] = len(fs), len(bl)
    mark(4, None if not fs else False, f"{len(fs)} freeze(s)")
    mark(11, not fs and not bl, f"{len(fs)} freeze(s), {len(bl)} black")


def _audio_events(index_html="index.html"):
    """[(start, dur, kind, id, src, volume)] for every <audio> clip in the composition."""
    if not os.path.exists(index_html):
        return []
    html = open(index_html, encoding="utf-8").read()
    ev = []
    for tag in re.findall(r"<audio\b[^>]*>", html):
        at = dict(re.findall(r'([\w-]+)="([^"]*)"', tag))
        if "data-start" not in at:
            continue
        src = at.get("src", "")
        cls = at.get("class", "")
        kind = "music" if ("music" in cls or "/music/" in src or "bed" in os.path.basename(src)) else "sfx"
        ev.append((float(at["data-start"]), float(at.get("data-duration", 0) or 0), kind,
                   at.get("id", ""), os.path.basename(src), at.get("data-volume", "")))
    return sorted(ev)


def check_intelligibility(cfg, render, transcript, aroll=None, threshold=0.97):
    """Re-transcribe the MASTER's speech part with the same engine and glossary, and compare
    it word by word with src/words.json (SequenceMatcher on normalised words). An SFX on a
    short word, or a music hit, swallows it: that shows up here as a changed word, with the
    events nearest to it listed so you know what to move or duck.

    words.json carries the INTENDED spelling (xcheck.py corrections), which the ear does not
    hear; so the clean A-roll is transcribed the same way and the master is ALSO compared
    with it. That second number isolates what the MIX changed, and it is the one gated when
    it exists."""
    import difflib
    import html as _html
    import captions as capmod
    words = capmod.load_words(transcript)
    if not words:
        return
    speech_end = round(max(w[1] for w in words) + 0.3, 2)
    gl = ",".join(cfg["language"].get("glossary") or [])
    here = os.path.dirname(os.path.abspath(__file__))
    os.makedirs("build/qa", exist_ok=True)

    def run(src, tag):
        out = f"build/qa/{tag}_words.json"
        cmd = [sys.executable, os.path.join(here, "transcribe.py"), src, "--start", "0",
               "--end", str(speech_end), "--out", f"build/qa/{tag}_transcript.json",
               "--words", out, "--flags", ""]
        if gl:
            cmd += ["--glossary", gl]
        r = hfcfg.run(cmd)
        if r.returncode or not os.path.exists(out):
            return None
        return capmod.load_words(out)

    tok = re.compile(r"[\w\u0590-\u05ff]+", re.UNICODE)

    def toks(ws):
        out = []
        for s0, e0, w in ws:
            for t in tok.findall(_html.unescape(str(w))):
                out.append((t.lower(), s0, e0))
        return out

    heard = run(render, "master")
    if heard is None:
        warns.append("intelligibility: could not transcribe the master (transcribe.py failed) — "
                     "run it by hand")
        mark(11, None, "intelligibility not measured")
        return
    ref, got = toks(words), toks(heard)

    def score(a, b):
        sm = difflib.SequenceMatcher(None, [x[0] for x in a], [x[0] for x in b], autojunk=False)
        same = sum(bl.size for bl in sm.get_matching_blocks())
        diffs = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            span = a[i1:i2] or b[j1:j2] or a[max(0, i1 - 1):i1]
            t0, t1 = (span[0][1], span[-1][2]) if span else (0.0, 0.0)
            diffs.append((t0, t1, " ".join(x[0] for x in a[i1:i2]), " ".join(x[0] for x in b[j1:j2])))
        return same / max(1, len(a)), diffs

    pct_w, diffs_w = score(ref, got)
    base = run(aroll, "aroll") if aroll and os.path.exists(aroll) else None
    pct, diffs, vs = pct_w, diffs_w, "src/words.json"
    if base:
        pct_c, diffs_c = score(toks(base), got)
        pct, diffs, vs = pct_c, diffs_c, "the clean A-roll"
    ev = _audio_events()
    lines = []
    for t, t1, a_, b_ in diffs[:20]:
        # an event whose sound overlaps the differing words (±0.3 s); music clips are long,
        # so for them only a START (a hit, a drop) near the words counts
        near = [e for e in ev if (e[2] == "sfx" and e[0] - 0.3 <= t1 and e[0] + max(e[1], 0.3) >= t - 0.3)
                or (e[2] == "music" and t - 0.6 <= e[0] <= t1 + 0.3)]
        near = sorted(near, key=lambda e: abs(e[0] - t))[:3]
        tag = ", ".join(f"{e[2]} {e[3] or e[4]}@{e[0]:.2f}" for e in near) or "no SFX/music event near"
        lines.append(f"[{t:6.2f}-{t1:6.2f}] '{a_ or '∅'}' → heard '{b_ or '∅'}'  ({tag})")
    M["intelligibility"] = round(100 * pct, 1)
    msg = (f"intelligibility: {100 * pct:.1f} % of the words match {vs}"
           + (f" ({100 * pct_w:.1f} % vs words.json, which carries the corrected spellings)" if base else ""))
    if pct < threshold:
        issues.append(f"INTELLIGIBILITY BELOW {100 * threshold:.0f} % — " + msg
                      + " — move the SFX off these words or deepen the duck:\n      "
                      + "\n      ".join(lines))
    else:
        ok.append(msg)
        if lines:
            warns.append("words heard differently in the master (check the SFX near each):\n      "
                         + "\n      ".join(lines))
    mark(11, pct >= threshold, f"{100 * pct:.1f} %")


# ========================================================== the composition (index.html)
class _Tree:
    """Minimal DOM from html.parser: elements with id, classes, inline style, text, parent,
    and the top-level timed SCENE each one lives in."""

    def __init__(self, html):
        from html.parser import HTMLParser
        self.els, self.texts, self.styles = [], [], []
        tree = self
        VOID = {"img", "br", "hr", "input", "meta", "link", "source", "area", "col", "embed",
                "param", "track", "wbr", "path", "circle", "rect", "line", "stop", "ellipse",
                "polygon", "polyline", "use"}

        class P(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.stack, self.skip = [], 0

            def handle_starttag(self, tag, attrs):
                at = dict(attrs)
                parent = self.stack[-1] if self.stack else None
                el = {"tag": tag, "id": at.get("id") or "", "cls": (at.get("class") or "").split(),
                      "style": at.get("style") or "", "parent": parent, "timed": "data-start" in at,
                      "attrs": at}
                tree.els.append(el)
                if tag in ("script", "style"):
                    self.skip += 1
                if tag not in VOID:
                    self.stack.append(el)

            def handle_startendtag(self, tag, attrs):
                self.handle_starttag(tag, attrs)
                if tag not in VOID and self.stack:
                    self.stack.pop()

            def handle_endtag(self, tag):
                if tag in ("script", "style"):
                    self.skip = max(0, self.skip - 1)
                for k in range(len(self.stack) - 1, -1, -1):
                    if self.stack[k]["tag"] == tag:
                        del self.stack[k:]
                        break

            def handle_data(self, data):
                if self.stack and self.stack[-1]["tag"] == "style":
                    tree.styles.append(data)
                if self.skip or not data.strip():
                    return
                tree.texts.append((data.strip(), self.stack[-1] if self.stack else None))

        P().feed(html)

    def scene(self, el):
        """The outermost timed ancestor below the root (or the element's own id)."""
        top, n = None, el
        while n is not None:
            if n["timed"] and not n["attrs"].get("data-composition-id"):
                top = n
            n = n["parent"]
        return (top["id"] or top["tag"]) if top else "(static)"


def _css_rules(css):
    """[(selector, {prop: value}, block_index)] from a stylesheet (no @media nesting)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"@font-face\s*{[^}]*}", "", css)
    out = []
    for k, m in enumerate(re.finditer(r"([^{}@]+){([^{}]*)}", css)):
        decl = {}
        for d in m.group(2).split(";"):
            if ":" in d:
                a, b = d.split(":", 1)
                decl[a.strip().lower()] = b.strip()
        for sel in m.group(1).split(","):
            sel = sel.strip()
            if sel:
                out.append((sel, decl, k))
    return out


def _page_css(html, tree):
    css = "\n".join(tree.styles)
    for href in re.findall(r'<link[^>]+href="([^"]+\.css)"', html):
        if not re.match(r"^[a-z]+:", href) and os.path.exists(href):
            css += "\n" + open(href, encoding="utf-8").read()
    return css


LAYOUT = ("position", "left", "top", "right", "bottom", "inset", "width", "height",
          "transform", "display")


def check_class_collisions(index_html="index.html"):
    """Two scenes that use the same class name for different things. The real failure: a
    particle class `.tw {position:absolute; ...}` and the outro tagline's `.tw` — the
    particle rule also applied to the tagline words and stacked them on top of each other.
    Flagged:
      (a) the same bare class rule `.x {...}` written twice with a conflicting value
      (b) a bare class rule that POSITIONS (position:absolute/fixed or left/top) used in
          two or more scenes, where one of them also styles it under a scope (`#tag .x`):
          the bare rule leaks into the scoped one
    Fix: prefix scene-specific classes (`.ot-word`, `.m3-dot`)."""
    if not os.path.exists(index_html):
        return
    html = open(index_html, encoding="utf-8").read()
    tree = _Tree(html)
    rules = _css_rules(_page_css(html, tree))
    bare = {}
    for sel, decl, k in rules:
        m = re.fullmatch(r"\.([\w-]+)", sel)
        if m:
            bare.setdefault(m.group(1), []).append((decl, k))
    scoped = {}
    for sel, decl, k in rules:
        parts = sel.split()
        if len(parts) >= 2:
            m = re.fullmatch(r"(?:[\w-]*)\.([\w-]+)(?:[.:#\[].*)?", parts[-1].lstrip(">+~"))
            if m:
                scoped.setdefault(m.group(1), []).append(sel)
    scenes = {}
    for el in tree.els:
        for c in el["cls"]:
            scenes.setdefault(c, set()).add(tree.scene(el))
    found = []
    for c, defs in bare.items():
        if len(defs) > 1:
            conflict = []
            for (d1, _), (d2, _) in zip(defs, defs[1:]):
                conflict += [p for p in d1 if p in d2 and d1[p] != d2[p]]
            if conflict:
                found.append(f".{c} is defined {len(defs)} times with different "
                             f"{', '.join(sorted(set(conflict))[:3])}")
        positions = any(d.get("position") in ("absolute", "fixed") or "left" in d or "top" in d
                        for d, _ in defs)
        sc = scenes.get(c, set())
        if positions and len(sc) >= 2 and scoped.get(c):
            found.append(f".{c} positions elements (bare rule) in {len(sc)} scenes "
                         f"({', '.join(sorted(sc)[:3])}) and is also styled as "
                         f"'{scoped[c][0]}' — the bare rule leaks into it")
    if found:
        issues.append("CSS CLASS COLLISION across scenes (prefix scene classes): " + " | ".join(found[:4]))
    else:
        ok.append(f"CSS: no class collisions across scenes ({len(bare)} bare class rules checked)")
    mark(9, not found, f"{len(found)} collision(s)")


HEAVY_PROBE = r"""
<script>
window.addEventListener('load', () => setTimeout(() => {
  const out = []; const root = document.getElementById('root') || document.body;
  for (const el of root.querySelectorAll('*')) {
    const cs = getComputedStyle(el); const why = [];
    if (/blur\(/.test(cs.filter)) why.push('filter blur');
    if (/blur\(/.test(cs.backdropFilter || cs.webkitBackdropFilter || '')) why.push('backdrop blur');
    if (/radial-gradient/.test(cs.backgroundImage)) why.push('radial-gradient');
    if (cs.clipPath && cs.clipPath !== 'none') why.push('clip-path');
    if (why.length) out.push({id: el.id, cls: (el.getAttribute('class') || '').slice(0, 30), why});
  }
  document.getElementById('__heavy').textContent = JSON.stringify(out);
}, 200));
</script><div id="__heavy"></div>
"""


def check_heavy_overlays(index_html="index.html", limit=40):
    """Fewer than ~40 elements may carry filter:blur, a radial gradient or a clip-path, HIDDEN
    ONES INCLUDED — above that the frame capture renders solid black for the first half of
    the video. Counted on the computed style in Chrome (CSS + inline), before any seek."""
    if not os.path.exists(index_html):
        return
    import subprocess
    html = open(index_html, encoding="utf-8").read()
    page = html.replace("</body>", HEAVY_PROBE + "</body>") if "</body>" in html else html + HEAVY_PROBE
    tmp = os.path.join(os.path.dirname(os.path.abspath(index_html)), "_heavyprobe.html")
    open(tmp, "w", encoding="utf-8").write(page)
    try:
        r = subprocess.run([hfcfg.chrome_path(), "--headless", "--disable-gpu", "--no-sandbox",
                            "--allow-file-access-from-files", "--window-size=1080,1920",
                            "--virtual-time-budget=4000", "--dump-dom", "file://" + tmp],
                           capture_output=True, text=True, timeout=120)
    except Exception as e:                                      # noqa: BLE001
        warns.append(f"heavy-overlay count could not run Chrome: {e}")
        return
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    import html as _h
    m = re.search(r'id="__heavy">(.*?)</div>', r.stdout, re.S)
    if not m:
        warns.append("heavy-overlay count: Chrome returned nothing")
        return
    rows = json.loads(_h.unescape(m.group(1)) or "[]")
    n = len(rows)
    kinds = {}
    for x in rows:
        for w in x["why"]:
            kinds[w] = kinds.get(w, 0) + 1
    detail = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
    if n >= limit:
        issues.append(f"HEAVY OVERLAYS: {n} elements ({detail}) — keep < {limit}; above it the "
                      f"capture renders black. Particles: solid colour + box-shadow glow")
    elif n >= limit * 0.8:
        warns.append(f"heavy overlays: {n} elements ({detail}) — close to the {limit} limit")
    else:
        ok.append(f"heavy overlays: {n} elements" + (f" ({detail})" if detail else "") + f" < {limit}")
    M["heavy"] = n
    mark(10, n < limit, f"{n} heavy elements")


def check_assets(index_html="index.html"):
    """Every LOCAL file the page loads exists — the renderer silently skips a missing image
    (and HyperFrames' lint calls missing_local_asset fatal)."""
    if not os.path.exists(index_html):
        return
    html = open(index_html, encoding="utf-8").read()
    refs = set(re.findall(r'\b(?:src|href)="([^"#?]+)"', html))
    refs |= set(re.findall(r"url\(\s*['\"]?([^'\")#?]+)['\"]?\s*\)", html))
    from urllib.parse import unquote
    missing = sorted(r for r in refs if not re.match(r"^(?:[a-z]+:|//)", r) and r.strip()
                     and not os.path.exists(unquote(r)))
    if missing:
        issues.append(f"MISSING LOCAL ASSET(S): {missing[:6]}")
    else:
        ok.append(f"assets: all {len(refs)} local references exist")
    mark(10, not missing, f"{len(missing)} missing asset(s)")


EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000026FF\U0001F1E6-\U0001F1FF"
                   "\u2700-\u2712\u2714-\u2716\u2719-\u27BF\uFE0F\u200D\u2B50\u2B55\u231A\u231B"
                   "\u23E9-\u23F3\u23F8-\u23FA]")
DASH = re.compile(r"[\u2012-\u2015\u2E3A\u2E3B]|(?<![\w\u0590-\u05ff])-|-(?![\w\u0590-\u05ff])")
RANGE = re.compile(r"\d\s*[-\u2012-\u2015]\s*\d")


def on_screen_problems(text):
    """(dash?, emoji?) for one on-screen string. A number range ("10-20") is the one dash
    allowed; a hyphen glued between letters ("ב-AI", "e-mail") is orthography, not a dash.
    ✓ / ✗ are type, not keyboard emoji."""
    t = RANGE.sub("0", text)
    # a Hebrew prefix hyphen whose word sits in its own span (an isolated Latin run: the text
    # node is just "ה-") — spelling, not a dash
    t = re.sub(r"(?<=[\u05d0-\u05ea])-(?=$|[A-Za-z0-9])", "", t.strip())
    return bool(DASH.search(t)), bool(EMOJI.search(text))


def check_on_screen_text(index_html="index.html", captions_json="captions.json"):
    if not os.path.exists(index_html) and not os.path.exists(captions_json):
        return
    import html as _h
    items = []
    if os.path.exists(captions_json):
        for r in json.load(open(captions_json, encoding="utf-8")):
            if not r.get("hidden"):
                items.append((f"caption c{r['i']:02d}", re.sub(r"<[^>]+>", "", _h.unescape(r["text"]))))
    if os.path.exists(index_html):
        tree = _Tree(open(index_html, encoding="utf-8").read())
        for text, el in tree.texts:
            if el is not None and el["tag"] in ("title",):
                continue
            who = f"#{el['id']}" if el and el["id"] else (f".{el['cls'][0]}" if el and el["cls"] else "text")
            items.append((who, text))
    dashes = [f"{w} '{t[:30]}'" for w, t in items if on_screen_problems(t)[0]]
    emo = [f"{w} '{t[:30]}'" for w, t in items if on_screen_problems(t)[1]]
    if dashes:
        issues.append(f"DASH IN ON-SCREEN TEXT ({len(dashes)}): " + "; ".join(dashes[:5])
                      + " — rewrite without it (only number ranges may keep one)")
    if emo:
        issues.append(f"EMOJI IN ON-SCREEN TEXT ({len(emo)}): " + "; ".join(emo[:5]))
    if not dashes and not emo:
        ok.append(f"on-screen text: {len(items)} strings, no dashes, no emoji")
    mark(5, not dashes and not emo, f"{len(dashes)} dash, {len(emo)} emoji")


def check_rotation_scale(index_html="index.html", footage=("#aroll", "#cam")):
    """Rotation / sway on the footage MUST ride on a scale ≥ 1.07, or the rotated frame shows
    black corners. Reads the timeline: every tween or set that rotates a footage selector is
    checked against the scale that footage has at that time (the latest scale set / tween
    end before it, or its own scale)."""
    if not os.path.exists(index_html):
        return
    js = "\n".join(re.findall(r"<script>(.*?)</script>", open(index_html, encoding="utf-8").read(), re.S))
    sel_re = "|".join(re.escape(f) for f in footage)
    calls = []
    for m in re.finditer(r"tl\.(set|to|fromTo|from)\(\s*['\"](" + sel_re + r")['\"]\s*,(.*?)\)\s*;", js, re.S):
        kind, sel, body = m.group(1), m.group(2), m.group(3)
        tm = re.search(r",\s*([\d.]+)\s*$", body.strip())
        t = float(tm.group(1)) if tm else None
        objs = re.findall(r"{([^{}]*)}", body)
        last = objs[-1] if objs else ""
        sc = re.search(r"\bscale\s*:\s*([\d.]+)", last)
        rot = [float(x) for x in re.findall(r"\brotation\s*:\s*(-?[\d.]+)", body)]
        calls.append({"t": t, "sel": sel, "scale": float(sc.group(1)) if sc else None,
                      "rot": max((abs(r) for r in rot), default=0.0)})
    bad = []
    for c in calls:
        if c["rot"] <= 0.01 or c["t"] is None:
            continue
        cur = c["scale"]
        if cur is None:
            prev = [x for x in calls if x["sel"] == c["sel"] and x["scale"] is not None
                    and x["t"] is not None and x["t"] <= c["t"] + 1e-6]
            cur = prev[-1]["scale"] if prev else 1.0
        if cur < 1.07 - 1e-6:
            bad.append(f"{c['sel']} rotates {c['rot']:g}° at {c['t']:.2f}s on scale {cur:g}")
    if bad:
        issues.append("ROTATION WITHOUT A PUNCH (black corners; needs scale ≥ 1.07): "
                      + "; ".join(bad[:4]))
    else:
        n = sum(1 for c in calls if c["rot"] > 0.01)
        ok.append(f"camera: {n} rotation(s) on the footage, all on scale ≥ 1.07")
    mark(6, not bad, f"{len(bad)} rotation(s) under 1.07")


def check_density(media_json="media.json", storyboard="storyboard.md"):
    """The premium-edit density targets — WARNINGS, the story decides in the end."""
    if not os.path.exists(media_json):
        return
    m = json.load(open(media_json, encoding="utf-8"))
    moms = [x for x in m.get("moments", []) if x.get("type") != "punch"]
    scenes = list(m.get("scenes", []) or [])
    # the per-video scenes.py (scripts/scenes.py writes build/scenes.json at build time):
    # hook-* fragments are the hook world, "punch" is the camera, the hook's cards sit inside
    # the hook window; everything else is a designed moment
    built = []
    if os.path.exists("build/scenes.json"):
        built = json.load(open("build/scenes.json", encoding="utf-8")).get("scenes", [])
    hw = next(((float(x["start"]), float(x["end"])) for x in built
               if str(x.get("id")) == "hook-world"), None)
    in_hook = lambda x: hw and hw[0] - 0.1 <= float(x["start"]) and float(x["end"]) <= hw[1] + 0.5
    designed_built = [x for x in built if not str(x.get("id", "")).startswith("hook")
                      and x.get("id") != "punch" and not in_hook(x)]
    designed = len(moms) + len(scenes) + len(designed_built)
    heads = len(m.get("headlines", []) or [])
    hook = bool(m.get("hook")) or bool(hw) or any(
        x.get("type") == "fly" and float(x.get("start", 99)) < 2.0 for x in moms) \
        or any("hook" in str(x.get("type", "")) + str(x.get("id", "")) for x in scenes)
    punch = any(x.get("type") == "punch" for x in m.get("moments", [])) \
        or any(x.get("id") == "punch" for x in built)
    scenes = scenes + built
    w = []
    if not hook:
        w.append("no hook (the frame flying into a designed world in the first ~1 s)")
    if not 8 <= designed <= 12:
        w.append(f"{designed} designed moment(s) — target 8-12")
    if not 5 <= heads <= 7:
        w.append(f"{heads} kinetic headline(s) — target 5-7")
    if not punch:
        w.append("no punch-ins (scripts/plan_punches.py)")
    cb_declared = os.path.exists(storyboard) and re.search(r"callback|קולבק|תשלום חוזר",
                                                           open(storyboard, encoding="utf-8").read(), re.I)
    cb = any(x.get("callback") or re.search(r"callback|payoff", str(x.get("id", "")))
             for x in moms + scenes)
    if cb_declared and not cb:
        w.append("storyboard.md declares a callback but no moment/scene is marked \"callback\"")
    for x in w:
        warns.append("density: " + x)
    if not w:
        ok.append(f"density: hook, {designed} moments, {heads} headlines, punch-ins")
    M.update(hook=hook, designed=designed, heads=heads, punch=punch, callback=cb or not cb_declared)
    mark(3, True if hook else None, "hook" if hook else "no hook")
    mark(4, None if w else True, f"{designed} moments, {heads} headlines")
    mark(6, True if punch else None, "punch moment present" if punch else "no punches")


def check_lint():
    """HyperFrames lint: 0 errors (missing_local_asset is fatal). Of the warnings, overlaps and
    missing assets matter; nested-structure and file-size ones may be ignored. The CLI comes
    from $HYPERFRAMES_CMD, else the project's package.json pin, else npx hyperframes."""
    import shlex
    cmd = os.environ.get("HYPERFRAMES_CMD")
    if not cmd and os.path.exists("package.json"):
        m = re.search(r"(npx --yes hyperframes@[\d.]+)", open("package.json").read())
        cmd = m.group(1) if m else None
    cmd = shlex.split(cmd or "npx --yes hyperframes") + ["lint", "--json"]
    r = hfcfg.run(cmd)
    try:
        d = json.loads(r.stdout[r.stdout.index("{"):])
    except ValueError:
        warns.append(f"lint could not run ({' '.join(cmd)}): {r.stderr.strip()[-200:]}")
        mark(10, None, "lint not run")
        return
    errs = [f for f in d.get("findings", []) if f.get("severity") == "error"]
    loud = [f for f in d.get("findings", []) if f.get("severity") == "warning"
            and re.search(r"overlap|missing|asset", f.get("code", ""))]
    if errs:
        issues.append(f"LINT: {len(errs)} error(s): " + "; ".join(
            f"{f.get('code')} {f.get('elementId', '')}" for f in errs[:5]))
    else:
        ok.append(f"lint: 0 errors, {d.get('warningCount', 0)} warning(s)")
    for f in loud[:5]:
        warns.append(f"lint warning that matters: {f.get('code')} — {f.get('message', '')[:120]}")
    mark(10, not errs, f"lint {len(errs)} error(s)")


CHECKLIST = [
    (1, "Two-model transcription diffed; caption text corrected to the intended script; changes listed"),
    (2, "Framing map written; every element inside the grid's safe zone; captions on a high-contrast band"),
    (3, "Hook: words, frame flies away, 2-3 literal cards, return through the tint, speaker away ≤5s"),
    (4, "8-12 literal designed moments, 5-7 word-by-word headlines, at least 1 callback, nothing static >0.6s"),
    (5, "Captions 1-3 words, hard swaps, hidden under headlines, hook and outro; no dashes; no emoji"),
    (6, "Punch-ins on phrase boundaries; rotation only with scale ≥1.07"),
    (7, "Music generated, 2 variants compared, drop aligned to the turn by offset, sections calibrated, relative drops, outro lift"),
    (8, "SFX on every transition, scaled to the voice, never on words (except marked impacts)"),
    (9, "Outro geometry recomputed for this framing; logo inside the grid; no class collisions"),
    (10, "Lint 0 errors; no heavy-overlay overload; all assets present"),
    (11, "Snapshots reviewed and clean; master at −14 LUFS; no freezes; no black; transcript match ≥97%"),
    (12, "Report with location, beats, music, QA numbers, deviations"),
]


def checklist_files(transcript=None):
    """Checklist items decided from files rather than from a scan."""
    if os.path.exists("src/xcheck.json"):
        x = json.load(open("src/xcheck.json", encoding="utf-8"))
        good = x.get("undecided", 1) == 0 and not x.get("unmatched_keys")
        mark(1, good, f"{len(x.get('disagreements', []))} disagreements, {x.get('undecided')} undecided, "
                      f"{len(x.get('changes', []))} correction(s) applied")
    else:
        mark(1, False, "no src/xcheck.json — run scripts/xcheck.py")
    if os.path.exists("build/framing.json"):
        f = json.load(open("build/framing.json", encoding="utf-8"))
        lu = f.get("caption_band_luma")
        mark(2, None if lu is None else lu <= 0.6,
             f"caption band {f.get('caption_band')} luma {lu}")
    else:
        mark(2, False, "no build/framing.json — run scripts/framing_map.py")
    if os.path.exists("media.json"):
        m = json.load(open("media.json", encoding="utf-8"))
        music = (m.get("audio") or {}).get("music") or []
        mark(7, None if music else False, f"{len(music)} music clip(s) — variants/drop: by hand")
        sfx = [e for e in _audio_events() if e[2] == "sfx"]
        on_word = []
        if transcript and os.path.exists(transcript):
            import captions as capmod
            ws = capmod.load_words(transcript)
            on_word = [e for e in sfx if any(w[0] - 0.04 <= e[0] < w[1] - 0.02 for w in ws)]
        mark(8, None if sfx else False,
             f"{len(sfx)} SFX, {len(on_word)} starting inside a word (exempt impacts allowed)")
    mark(9, None if os.path.exists("build/outro.json") else None,
         "outro planned" if os.path.exists("build/outro.json") else "no outro planned")
    if glob.glob("build/qa/*.png"):
        mark(11, None, "contact sheets exist in build/qa — look at every frame")
    mark(12, None, "written by hand")


def print_checklist():
    print("\n== FINAL CHECKLIST (references/qa.md) ==")
    for n, text in CHECKLIST:
        good, note = M.get(n, (None, "not measured"))
        sym = "✓" if good is True else ("✗" if good is False else "?")
        print(f"  {sym} {n:2d}. {text}\n         {note}")


def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("project", nargs="?", default=".")
    ap.add_argument("--aroll")
    ap.add_argument("--transcript")
    ap.add_argument("--render")
    ap.add_argument("--captions", default="captions.json")
    ap.add_argument("--html", default="index.html")
    ap.add_argument("--checklist", action="store_true", help="print the final checklist")
    ap.add_argument("--no-intelligibility", action="store_true",
                    help="skip re-transcribing the master (it takes ~30 s per minute)")
    ap.add_argument("--no-chrome", action="store_true", help="skip the Chrome-based checks")
    ap.add_argument("--lint", action="store_true", help="also run `hyperframes lint` (0 errors)")
    a = ap.parse_args()
    cfg = hfcfg.load(a.config)
    hfcfg.require("ffmpeg", "ffprobe")
    os.chdir(a.project)
    transcript = a.transcript or ("src/words.json" if os.path.exists("src/words.json") else None)

    check_drift()
    aroll = a.aroll or ("assets/aroll.mp4" if os.path.exists("assets/aroll.mp4") else None)
    check_fps(cfg, aroll, a.render)
    check_caption_continuity(cfg, a.html, a.captions, transcript)
    check_caption_windows(a.captions)
    check_caption_language(cfg, a.captions)
    check_on_screen_text(a.html, a.captions)
    check_class_collisions(a.html)
    check_assets(a.html)
    check_rotation_scale(a.html)
    check_density()
    if not a.no_chrome:
        check_grid(cfg, a.html)
        check_heavy_overlays(a.html)
    check_fonts(cfg, a.html)
    if a.lint:
        check_lint()
    if a.transcript:
        check_words_covered(a.captions, a.transcript, cfg["language"].get("typos"))
    if a.aroll:
        check_dead_space(a.aroll)
        check_aroll_sync(a.aroll)
    if a.render:
        check_render(a.render, cfg)
        check_loudness(a.render, cfg)
        check_freeze_black(a.render)
        if transcript and not a.no_intelligibility:
            check_intelligibility(cfg, a.render, transcript, a.aroll or
                                  ("assets/aroll.mp4" if os.path.exists("assets/aroll.mp4") else None))

    print("== PREFLIGHT QA ==")
    for o in ok:
        print(f"  ✓ {o}")
    for w in warns:
        print(f"  ! {w}")
    for i in issues:
        print(f"  ✗ {i}")
    if a.checklist:
        checklist_files(transcript)
        print_checklist()
    if not issues:
        print("\n  clean — now look at the contact sheets (scripts/qa_frames.py) and walk "
              "references/qa.md before you show it to anyone")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
