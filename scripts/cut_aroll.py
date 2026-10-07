#!/usr/bin/env python3
"""Cut the A-roll to the speech — onset-precise, frame-exact, sample-exact.

This is the quality gate everything downstream inherits. It implements
references/cutting.md:

  * map speech islands with silencedetect (NOT with the transcript — the transcript
    smears aborted takes)
  * find each chunk's true onset at SPEECH level (-26 dB, relative to the speaker's
    level, never under the room's noise floor + 8 dB)
  * bound the onset search on BOTH sides so it cannot lock onto a neighbour's tail
  * land true speech exactly one frame into every segment (or the chunk's own "lead")
  * derive the edited gap from the ORIGINAL pause so the speaker's rhythm survives
  * quantise every segment to whole frames, then PROVE it by counting packets
  * never let two segments share source audio (clamp + warn; a GATE)
  * build the A-roll's audio from exact PCM and encode AAC ONCE, then PROVE lip sync at
    every boundary by cross-correlating the A-roll's audio against the raw (a GATE)

Usage
  python3 scripts/cut_aroll.py --src raw.mp4 --plan                 # inspect the islands
  python3 scripts/cut_aroll.py --src raw.mp4 --chunks chunks.json   # do the cut
  python3 scripts/cut_aroll.py --src raw.mp4 --verify assets/aroll.mp4   # re-run the sync proof

`chunks.json` is a list of the chunks you want, in order:
  [{"name":"c01","start":12.44,"end":15.02}, {"name":"c02","start":16.1,"end":19.3,"lead":0.10}]
Produce it from --plan output, dropping false starts and keeping the LAST take of any
repeated line. Optional per chunk: "lead" (seconds of runway before the measured onset —
raise it for a quiet first consonant such as ל / ו / ת that the gate clips), "floor"
(a hard lower bound for the onset search, just after a dropped false start).
Merges and drops come from config.json → cutting.merge / cutting.drop.
"""
import array
import json
import math
import os
import re
import sys
import tempfile
import wave
from operator import mul

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

FRAME = 0.04            # the cut is always 25 fps (config project.fps must say 25)
SR = 48000              # every segment's PCM: 48 kHz, 16-bit, stereo
SYNC_SR = 16000         # sync proof: 1 sample = 0.0625 ms, plenty for a 10 ms gate
SYNC_TOL = 0.010        # max audio offset vs the raw at any boundary
LEAD_TOL = (-0.025, 0.015)   # accepted onset window around each segment's target lead


# ------------------------------------------------------------------ probing

def _tmp(suffix):
    """A private temp file. A fixed /tmp/_onset.txt is shared by every run on the
    machine: two cuts at once (or a cut and a preflight) read each other's numbers."""
    fd, p = tempfile.mkstemp(prefix="hfcut_", suffix=suffix)
    os.close(fd)
    os.remove(p)
    return p


def islands(src, noise_db=-33.0, min_sil=0.30):
    """Speech islands, from silencedetect. Never pass -v error: it suppresses the
    silencedetect output entirely and the probe comes back looking like a missing file.
    -vn: a 4K phone raw would otherwise be fully DECODED just to read its audio."""
    r = hfcfg.run(["ffmpeg", "-nostdin", "-i", src, "-vn",
                   "-af", f"silencedetect=noise={noise_db}dB:d={min_sil}", "-f", "null", "-"])
    txt = r.stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", txt)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", txt)]
    total = float(hfcfg.probe(src, "format=duration") or 0)
    marks = sorted([(s, "s") for s in starts] + [(e, "e") for e in ends])
    out, cur = [], 0.0
    for t, kind in marks:
        if kind == "s":
            if t - cur > 0.12:
                out.append((round(cur, 3), round(t, 3)))
        else:
            cur = t
    if total - cur > 0.12:
        out.append((round(cur, 3), round(total, 3)))
    return out, total


def rms_series(src, lo=None, hi=None, window=480):
    """[(t, dB)] — the RMS of every `window`-sample block (10 ms at 48 kHz), t relative
    to `lo`. Digital silence (-inf) reads as -120 dB."""
    tmp = _tmp(".txt")
    cmd = ["ffmpeg", "-nostdin", "-y"]
    if lo is not None:
        cmd += ["-ss", f"{lo:.4f}"]
        if hi is not None:
            cmd += ["-t", f"{hi - lo:.4f}"]
    cmd += ["-i", src, "-map", "0:a:0", "-vn",
            "-af", f"asetnsamples={window},astats=metadata=1:reset=1,"
                   f"ametadata=print:key=lavfi.astats.Overall.RMS_level:file={tmp}",
            "-f", "null", "-"]
    hfcfg.run(cmd)
    out, t = [], None
    if not os.path.exists(tmp):
        return out
    with open(tmp) as f:
        for line in f:
            m = re.match(r"frame:\d+\s+pts:\d+\s+pts_time:([0-9.]+)", line)
            if m:
                t = float(m.group(1))
            elif t is not None and "RMS_level=" in line:
                try:
                    v = float(line.split("=")[1])
                except ValueError:
                    continue
                out.append((t, v if v > -120 else -120.0))
    os.remove(tmp)
    return out


def rms_onset(src, lo, hi, thresh_db=-26.0):
    """First 10 ms window above `thresh_db` inside [lo, hi] — the true speech onset.

    silencedetect at -33/-40 dB stops at breath and room tone, which sit 40-150 ms
    BEFORE the first consonant. Cutting there leaves an audible beat of nothing.
    """
    if hi <= lo:
        return None
    for t, v in rms_series(src, lo, hi):
        if v > thresh_db:
            return lo + t
    return None


def rms_offset(src, lo, hi, thresh_db=-26.0):
    """Last window above threshold — where speech actually stops."""
    if hi <= lo:
        return None
    last = None
    for t, v in rms_series(src, lo, hi):
        if v > thresh_db:
            last = lo + t
    return last


def level_stats(src):
    """(voice reference, noise floor) in dB, from every 10 ms RMS window of the raw.

    * reference = 90th percentile: the speaker's loud-speech level. The -26 dB gate was
      tuned on a voice peaking around -12/-16 dB; a quiet raw barely crosses it, so the
      gate is held at most `onset_rel_db` under this reference.
    * floor = 10th percentile: the room between words. On a QUIET take the relative gate
      sinks to where the decaying tail of a word, a breath or the room itself still
      reads as "speech" — the offset search then runs to the edge of the next island,
      the segment's tail overlaps the next segment and a word plays twice at the join.
      So the gate also stays at least `floor_margin_db` (8) above this floor.
    """
    vals = sorted(v for _, v in rms_series(src) if v > -119)
    if not vals:
        return None, None
    return vals[int(len(vals) * 0.90)], vals[int(len(vals) * 0.10)]


def effective_onset_db(src, c):
    """The onset/offset gate for this recording, and why. Returns (gate, ref, floor)."""
    ref, floor = level_stats(src)
    if ref is None:
        return c["onset_db"], None, None
    gate = min(c["onset_db"], ref - c.get("onset_rel_db", 12.0))
    gate = max(gate, floor + c.get("floor_margin_db", 8.0))
    if gate > ref - 6.0:
        # speech barely clears the room: no gate separates them well
        print(f"  ! noisy recording: voice {ref:.1f} dB, room {floor:.1f} dB — only "
              f"{ref - floor:.0f} dB apart; onsets may be late. Check every join by ear.")
        gate = ref - 6.0
    return round(gate, 1), ref, floor


# -------------------------------------------------------------------- planning

def plan_chunks(src, chunks, cfg):
    """Resolve every chunk's real onset/offset, with the search window bounded on
    BOTH sides. An unbounded search catches the previous chunk's tail (or the next
    chunk's head) and silently glues neighbouring sentences together.

    A shift that comes back as exactly -0.600 is always this bug, never a real onset.
    An offset that comes back at the very end of its window means the gate sits under
    the pause noise (see level_stats) — it is reported, and the overlap gate clamps it.
    """
    c = cfg["cutting"]
    db = c["onset_db"]          # already made relative to this speaker in main()
    out = []
    for i, ch in enumerate(chunks):
        s, e = float(ch["start"]), float(ch["end"])
        # the neighbours in the SOURCE, not in the list: a re-ordered list would
        # otherwise bound the search with a chunk from elsewhere and invert the window
        prev_end = max((float(x["end"]) for x in chunks if x is not ch
                        and float(x["end"]) <= s + 1e-6), default=0.0)
        next_start = min((float(x["start"]) for x in chunks if x is not ch
                          and float(x["start"]) >= e - 1e-6), default=s + 1e6)
        # explicit floor after a DROPPED false start: a discarded take is not in the
        # list, so nothing else stops the search locking onto its tail
        floor = max(prev_end + 0.005, float(ch.get("floor", 0.0)))
        lo = max(s - 0.60, floor)
        hi = min(e + 0.50, next_start - 0.005)
        on = rms_onset(src, lo, min(hi, s + 1.20), db)
        off = rms_offset(src, max(lo, e - 1.50), hi, db)
        if on is None:
            on = s
            print(f"  ! {ch['name']}: no onset above {db} dB in [{lo:.2f},{hi:.2f}] — using {s:.3f}")
        if off is None:
            off = e
        elif i + 1 < len(chunks) and off >= hi - 0.02 and hi < e + 0.5:
            print(f"  ! {ch['name']}: speech 'never stops' before the next chunk "
                  f"({off:.2f}s) — the gate ({db} dB) sits under the pause noise")
        shift = on - s
        if abs(shift + 0.600) < 0.002:
            print(f"  ! {ch['name']}: shift is exactly -0.600 — that is the search-window "
                  f"edge, not an onset. Raise the floor for this chunk.")
        out.append({**ch, "onset_raw": round(on, 4), "offset_raw": round(off, 4),
                    "orig_gap": round(s - prev_end, 4) if i else 0.0})
    return out


# Letters whose sound starts with quiet friction (sibilants, fricatives). The onset gate
# fires on the louder vowel AFTER them, so a one-frame lead can shave the hiss of a ש or
# ס. A segment whose first word starts with one gets a longer lead (≤ 0.15 s, so a
# breath is never pulled in). Not ה/פ/כ: in fast Hebrew a word-initial ה is usually not
# pronounced at all (a test take said "פעם" for "הפעם" even with 0.4 s of lead), and
# פ/כ are plosives with a loud onset. Other quiet openers (ל / ו / ת on a soft take) are
# handled per chunk: "lead" in chunks.json.
SOFT_ONSET = set("שסצזחfsvz")


def mark_soft_onsets(plan, words):
    """Tag each chunk whose first spoken word begins with a soft-onset letter."""
    if not words:
        return 0
    n = 0
    for p in plan:
        first = next((w for w in words if p["onset_raw"] - 0.35 <= w[0] <= p["onset_raw"] + 0.30), None)
        if first:
            ch = first[2].strip().lstrip("\"'“-(").lower()[:1]
            if ch and ch in SOFT_ONSET:
                p["soft_onset"] = first[2]
                n += 1
    return n


def lead_for(p, c):
    """Seconds of runway before the measured onset. An explicit per-chunk "lead" wins
    (a quiet initial consonant the gate cannot see), then the soft-onset lead, then
    cutting.speech_lead."""
    if p.get("lead") is not None:
        return float(p["lead"])
    if p.get("soft_onset"):
        return float(c.get("soft_onset_lead", 0.10))
    return float(c["speech_lead"])


def apply_merges(plan, merges):
    """A word clipped at its own segment head is fixed by MERGING, not by nudging the
    in-point: the -26 dB gate fires on a loud voiced consonant and drops the quieter
    fricative in front of it. A transcript missing a leading letter is the tell.
    Merging makes the audio continuous across the join — mid-segment audio is never
    gated — and the natural pause survives, which is what 'sounds like a complete
    sentence' means."""
    if not merges:
        return plan
    by_name = {p["name"]: p for p in plan}
    drop = set()
    for a, b in merges:
        if a in by_name and b in by_name:
            by_name[a]["offset_raw"] = by_name[b]["offset_raw"]
            by_name[a]["merged_with"] = b
            drop.add(b)
            print(f"  merged {a} + {b} → one continuous segment")
    return [p for p in plan if p["name"] not in drop]


def merge_touching(plan, cfg, margin=0.02):
    """Two neighbouring chunks whose SPEECH touches are one utterance: chunk i's speech
    runs (to within `margin`) into the runway of chunk i+1. That happens when --plan
    split a sentence at a short dip (a quiet take planned at too high a level), or when
    two chunk bounds were typed too close. Clamping one against the other would cut a
    word in half, and cutting the dip out stutters; merging keeps the raw's own
    continuous audio — the fix references/cutting.md prescribes for a clipped head.
    Only neighbours in SOURCE order are merged; a re-ordered list is left alone."""
    c = cfg["cutting"]
    out = []
    for p in plan:
        if out:
            a = out[-1]
            b_start = p["onset_raw"] - lead_for(p, c)
            if a["onset_raw"] <= p["onset_raw"] and a["offset_raw"] + margin >= b_start:
                print(f"  ! {a['name']} + {p['name']}: no pause between them "
                      f"(speech to {a['offset_raw']:.2f}s, next onset {p['onset_raw']:.2f}s) "
                      f"→ merged into one continuous segment")
                a["offset_raw"] = max(a["offset_raw"], p["offset_raw"])
                a.setdefault("auto_merged", []).append(p["name"])
                continue
        out.append(dict(p))
    return out


def layout(plan, cfg, trim=None):
    """Every segment's source window, as whole frames — no encoding.

    trim maps segment name -> extra seconds to shave off src_start (the refine pass);
    the frame COUNT does not depend on it, so refining never moves a boundary.

    OVERLAP CLAMP: a segment may never run into the source audio of the segment after
    it. On a quiet take the offset gate can run to the next island and the TAIL then
    plays the next chunk's first syllable — which plays AGAIN at the start of the next
    segment. The earlier segment is shortened to the last whole frame before the next
    one starts, and the clamp is reported. (Chunks whose SPEECH touches were already
    merged by merge_touching, so a clamp only ever removes tail air.)"""
    c = cfg["cutting"]
    trim = trim or {}
    segs = []
    for i, p in enumerate(plan):
        if i == len(plan) - 1:
            tail = c["tail_last"]              # the closing word needs decay + a beat of air
        elif p.get("topic_boundary"):
            tail = c["gap_topic_boundary"] - c["lead"]
        else:
            gap = min(max(c["gap_ratio"] * max(p["orig_gap"], 0.0), c["gap_min"]), c["gap_max"])
            tail = max(gap - c["lead"], c["tail_min"])
        lead = lead_for(p, c)
        start0 = max(p["onset_raw"] - lead, 0.0)
        src_start = max(start0 + trim.get(p["name"], 0.0), 0.0)
        # QUANTISE TO WHOLE FRAMES. A non-frame-aligned duration encodes LONGER than
        # requested, so planned boundaries drift later than the real concat and every
        # layout cut fires before its sentence starts.
        nframes = int(math.ceil((p["offset_raw"] + tail - start0) / FRAME - 1e-9))
        segs.append({"name": p["name"], "src_start": round(src_start, 4), "frames": nframes,
                     "lead": lead, "speech_end": p["offset_raw"], "vf": p.get("vf")})
    for a, b in zip(segs, segs[1:]):
        a_end = a["src_start"] + a["frames"] * FRAME
        if b["src_start"] >= a["src_start"] and a_end > b["src_start"] + 1e-6:
            n = int(math.floor((b["src_start"] - a["src_start"]) / FRAME + 1e-9))
            a["clamped"] = round(a_end - b["src_start"], 3)
            a["frames"] = max(n, 1)
            cut_speech = a["src_start"] + a["frames"] * FRAME < a["speech_end"]
            print(f"  ! {a['name']}: ran {a['clamped']:.3f}s into {b['name']}'s source audio — "
                  f"clamped to {a['frames']} frames"
                  + ("  (this cuts into its own last word — merge the two chunks in "
                     "config cutting.merge, or move the chunk bounds)" if cut_speech else ""))
    cum = 0.0
    for s in segs:
        s["dur"] = round(s["frames"] * FRAME, 4)
        s["src_end"] = round(s["src_start"] + s["dur"], 4)
        s["start"] = round(cum, 4)
        cum = round(cum + s["dur"], 4)
    return segs, cum


# -------------------------------------------------------------------- encoding

def _fades(seg, c):
    dur = seg["dur"]
    fo = max(0.0, dur - c["fade_out"])
    if fo <= seg["speech_end"] - seg["src_start"] + 0.035:
        fo = max(0.0, dur - min(c["fade_out"], 0.03))
    return (f"afade=t=in:st=0:d={c['fade_in']},"
            f"afade=t=out:st={fo:.4f}:d={c['fade_out']}")


def encode_audio(src, seg, cfg, outdir="segments"):
    """The segment's audio as PCM with EXACTLY frames × 0.04 × 48000 samples.

    WHY PCM: an AAC encode prepends ~1024 samples of priming and pads its last frame.
    Each segment encoded to AAC and joined with the concat demuxer (-c copy) carries
    that priming into the join: ~21 ms more audio delay per boundary, so the speaker's
    lips drift further from the voice with every cut (0.3-1 s by the end of a reel —
    while the video frame count still looks perfect). PCM has no priming and an exact
    length, so a PCM concat is sample-exact; AAC is encoded once, at the very end."""
    os.makedirs(outdir, exist_ok=True)
    n = int(round(seg["dur"] * SR))
    out = os.path.join(outdir, f"{seg['name']}.wav")
    af = (f"aresample={SR},asetpts=N/SR/TB,{_fades(seg, cfg['cutting'])},"
          f"apad=whole_len={n},atrim=end_sample={n}")
    r = hfcfg.run(["ffmpeg", "-nostdin", "-v", "error", "-y",
                   "-ss", f"{seg['src_start']:.4f}", "-t", f"{seg['dur'] + 0.25:.4f}", "-i", src,
                   "-map", "0:a:0", "-vn", "-af", af, "-ac", "2", "-c:a", "pcm_s16le", out])
    if r.returncode:
        sys.exit(f"audio encode failed for {seg['name']}:\n{r.stderr}")
    with wave.open(out, "rb") as w:
        got = w.getnframes()
    if got != n:
        sys.exit(f"  ✗ {seg['name']}.wav holds {got} samples, expected exactly {n}")
    seg["wav"] = out
    return out


def encode_video(src, seg, outdir="segments"):
    """The segment's picture, frame-exact, VIDEO ONLY (segments/_video/<name>.mp4) — the
    A-roll is concatenated from these. Plus segments/<name>.mp4 = that picture + its PCM
    as AAC (a remux), a standalone clip for anything that wants one segment.

    Why the A-roll never concatenates the with-audio clips: the concat demuxer starts
    each file at its EARLIEST packet, and an AAC track's first packet is the priming
    packet at -21.3 ms. The whole picture then lands 21 ms late against any audio laid
    under it (measured: video start_time 0.021 s, a constant -21 ms lip-sync error)."""
    vdir = os.path.join(outdir, "_video")
    os.makedirs(vdir, exist_ok=True)
    vout = os.path.join(vdir, f"{seg['name']}.mp4")
    out = os.path.join(outdir, f"{seg['name']}.mp4")
    vf = seg.get("vf") or f"scale=1080:1920:flags=lanczos,fps={round(1 / FRAME)},setsar=1"
    # RE-TIME EVERY SEGMENT TO PTS 0. After an input seek on a 30 fps (or VFR phone)
    # source, the first 25 fps frame can land at pts 0.04; `-t` then drops the last
    # frame and the concat demuxer opens a one-frame gap at that join. setpts by count
    # makes every segment start at 0 and hold exactly `frames` frames.
    vf += f",setpts=N/({round(1 / FRAME)}*TB)"
    r = hfcfg.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-ss", f"{seg['src_start']:.4f}", "-t", f"{seg['dur'] + 0.25:.4f}", "-i", src,
        "-map", "0:v:0", "-an", "-frames:v", str(seg["frames"]), "-vf", vf,
        "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p", vout])
    if r.returncode:
        sys.exit(f"encode failed for {seg['name']}:\n{r.stderr}")
    r = hfcfg.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", vout, "-i", seg["wav"],
                   "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                   "-c:a", "aac", "-b:a", "256k", out])
    if r.returncode:
        sys.exit(f"remux failed for {seg['name']}:\n{r.stderr}")
    seg["video"], seg["file"] = vout, out
    return out


def concat_pcm(segs, out):
    """Sample-exact join: the frames of each wav, back to back, through `wave`."""
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        for s in segs:
            with wave.open(s["wav"], "rb") as r:
                if (r.getnchannels(), r.getsampwidth(), r.getframerate()) != (2, 2, SR):
                    sys.exit(f"  ✗ {s['wav']}: unexpected PCM format")
                w.writeframes(r.readframes(r.getnframes()))
    return out


def concat_and_prove(segs, planned_total, out="assets/aroll.mp4", outdir="segments"):
    """Video: concat demuxer over the VIDEO-ONLY segments, -c copy, every file's duration
    given explicitly (without a `duration` line the demuxer offsets each next file by
    its probed length). Audio: the PCM concat, encoded to AAC once.

    Then PROVE it: frames by counting PACKETS (format=duration reports the AAC tail and
    makes a perfect concat look 0.02 s long), and the audio stream as long as the video
    to within one frame."""
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    lst = os.path.join(outdir, "list.txt")
    with open(lst, "w") as f:
        for s in segs:
            f.write(f"file '{os.path.relpath(s['video'], outdir)}'\nduration {s['dur']:.4f}\n")
    wav = concat_pcm(segs, os.path.join(outdir, "aroll.wav"))
    r = hfcfg.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                   "-i", lst, "-i", wav, "-map", "0:v:0", "-map", "1:a:0",
                   "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-ar", str(SR), "-ac", "2",
                   "-movflags", "+faststart", out])
    if r.returncode:
        sys.exit(f"concat failed:\n{r.stderr}")

    seg_frames = sum(hfcfg.frames(s["video"]) for s in segs)
    cat_frames = hfcfg.frames(out)
    real = round(cat_frames * FRAME, 4)
    print(f"\n  segment frames {seg_frames}  concat frames {cat_frames}  → {real:.3f}s")
    if seg_frames != cat_frames:
        sys.exit(f"  ✗ FRAME DRIFT: segments sum to {seg_frames}, concat holds {cat_frames}")
    if abs(real - planned_total) > 0.001:
        sys.exit(f"  ✗ TIMELINE DRIFT: planned {planned_total:.3f}s vs real {real:.3f}s")
    print("  ✓ frame-exact: planned == real")
    return real


# ------------------------------------------------------------------ sync proof

def _pcm(path, start=None, dur=None, sr=SYNC_SR):
    """Mono s16 samples of `path` (optionally a window), as an array."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{max(start, 0.0):.4f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.4f}"]
    cmd += ["-i", path, "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    r = hfcfg.run(cmd, text=False)
    a = array.array("h")
    a.frombytes(r.stdout[:len(r.stdout) // 2 * 2])
    if sys.byteorder == "big":
        a.byteswap()
    return a


def _best_lag(ref, tgt, dec=8):
    """(lag, normalised correlation): where `ref` sits inside `tgt`. Coarse search on an
    8x-decimated copy, then sample-exact around the coarse peak. Pure python, no numpy:
    it runs on a fresh install."""
    def decim(x):
        return [sum(x[i:i + dec]) / dec for i in range(0, len(x) - dec + 1, dec)]

    def search(r, t, lags):
        n = len(r)
        er = sum(v * v for v in r) or 1.0
        sq = [0.0]
        for v in t:
            sq.append(sq[-1] + v * v)
        best = (0, -2.0)
        for lag in lags:
            et = sq[lag + n] - sq[lag]
            if et <= 0:
                continue
            c = sum(map(mul, r, t[lag:lag + n])) / math.sqrt(er * et)
            if c > best[1]:
                best = (lag, c)
        return best

    rc, tc = decim(ref), decim(tgt)
    lag_c, _ = search(rc, tc, range(0, len(tc) - len(rc) + 1))
    lo = max(0, lag_c * dec - 2 * dec)
    hi = min(len(tgt) - len(ref), lag_c * dec + 2 * dec)
    return search(list(ref), list(tgt), range(lo, hi + 1))


def stream_info(path):
    """{'v_start', 'a_start', 'fps', 'v_dur', 'a_dur'} — a_dur from the DECODED samples,
    the length a player actually plays."""
    def f(x, d=0.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return d
    # one field per probe: ffprobe prints fields in ITS order, not the order asked for
    v_s = hfcfg.probe(path, "stream=start_time", "v:0")
    a_s = hfcfg.probe(path, "stream=start_time", "a:0")
    fps = probe_fps(path)
    n = hfcfg.frames(path)
    return {"v_start": f(v_s), "a_start": f(a_s), "fps": fps, "frames": n,
            "v_dur": n / fps if fps else 0.0}


def probe_fps(path):
    """The video stream's nominal frame rate (r_frame_rate) as a float, or 0.0."""
    fr = (hfcfg.probe(path, "stream=r_frame_rate", "v:0") or "0/1").split("/")
    try:
        return float(fr[0]) / float(fr[1] if len(fr) > 1 else 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def sync_offsets(aroll, src, segs, win=0.5, skip=0.05, search=1.0, verbose=True):
    """Audio offset of the A-roll against the RAW at the start of every segment.

    For segment i, A-roll time (start_i + skip) must carry the source audio at
    (src_start_i + skip). A 0.5 s window of the raw is located inside the A-roll's
    audio (± `search` s) by cross-correlation. Positive = the A-roll's voice is LATE
    against its picture. Drift accumulates at joins, so measuring right after each
    join measures every boundary. Times are taken relative to the VIDEO stream's
    start, which is what the player syncs to.

    Returns [(name, offset_s or None, corr)]."""
    info = stream_info(aroll)
    full = _pcm(aroll)
    out = []
    for s in segs:
        L = min(win, s["dur"] - skip - 0.08)
        if L < 0.2:
            out.append((s["name"], None, 0.0))
            continue
        ref = _pcm(src, s["src_start"] + skip, L)
        want = info["v_start"] + s["start"] + skip            # where it should play
        t0 = max(want - search, info["a_start"])
        i0 = int(round((t0 - info["a_start"]) * SYNC_SR))
        i1 = i0 + int(round((L + 2 * search) * SYNC_SR))
        tgt = full[i0:i1]
        if len(ref) < SYNC_SR * 0.15 or len(tgt) < len(ref):
            out.append((s["name"], None, 0.0))
            continue
        lag, c = _best_lag(ref, tgt)
        got = info["a_start"] + (i0 + lag) / SYNC_SR
        out.append((s["name"], round(got - want, 4), round(c, 3)))
    if verbose:
        print("\n  lip sync: A-roll audio vs the raw at every boundary "
              f"(gate |offset| < {SYNC_TOL * 1000:.0f} ms):")
        for name, off, c in out:
            if off is None:
                print(f"    {name:>6s}   (too short to measure)")
            else:
                flag = "" if abs(off) < SYNC_TOL and c >= 0.5 else "   ← OUT OF SYNC" if c >= 0.5 \
                    else "   ← no match (wrong source, or bounds.json is stale)"
                print(f"    {name:>6s}  {off * 1000:+7.1f} ms  (corr {c:.2f}){flag}")
    return out


def av_length_gap(aroll):
    """(video seconds, decoded audio seconds, fps)."""
    info = stream_info(aroll)
    a = _pcm(aroll)
    return info["v_dur"], info["a_start"] + len(a) / SYNC_SR - info["v_start"], info["fps"]


def sync_problems(aroll, src, segs, verbose=True):
    """The two A/V gates, as a list of failure strings (empty = in sync):
    audio length == video length within one frame, and |offset| < 10 ms at every
    boundary with a real match (corr ≥ 0.5)."""
    bad = []
    v, a, fps = av_length_gap(aroll)
    if verbose:
        print(f"\n  A-roll length: video {v:.3f}s, audio {a:.3f}s")
    if abs(a - v) > 1.0 / fps + 1e-4:
        bad.append(f"audio is {a:.3f}s, video {v:.3f}s — {abs(a - v) * 1000:.0f} ms apart "
                   f"(more than one frame)")
    if src and os.path.exists(src) and segs:
        res = sync_offsets(aroll, src, segs, verbose=verbose)
        off = [(n, o) for n, o, c in res if o is not None and c >= 0.5 and abs(o) >= SYNC_TOL]
        nomatch = [n for n, o, c in res if o is not None and c < 0.5]
        if off:
            bad.append("audio out of sync at " + ", ".join(f"{n} {o * 1000:+.0f} ms" for n, o in off[:6])
                       + (f" (+{len(off) - 6} more)" if len(off) > 6 else ""))
        if nomatch:
            bad.append(f"A-roll audio does not match the raw at {', '.join(nomatch[:6])} — "
                       f"wrong --src, or src/bounds.json is stale")
    return bad


# ----------------------------------------------------------- overlap / repeat gate

def overlap_problems(segs, words=None):
    """GATE: no two segments share source audio, and no spoken word plays twice.

    Ranges are compared pairwise (a re-ordered chunk list can overlap non-neighbours).
    With the raw transcript each shared stretch is named by the word in it: a word is
    heard twice when the SAME source instant of it (≥ 10 ms) lies in two segments — the
    duplicated syllable at a join. A word that merely straddles a seamless join (the
    next segment continues where this one stopped) plays once and is fine.
    Returns (errors, warnings); a warning names a segment that ENDS inside a word and
    jumps elsewhere (whisper times are ±0.1 s, so that one is advice, not a failure)."""
    errs, warns = [], []
    rng = [(s["name"], float(s["src_start"]), float(s["src_end"])) for s in segs]
    for i in range(len(rng)):
        for j in range(i + 1, len(rng)):
            a, b = rng[i], rng[j]
            lo, hi = max(a[1], b[1]), min(a[2], b[2])
            if hi - lo > 1e-3:
                errs.append(f"{a[0]} and {b[0]} share {hi - lo:.3f}s of source audio "
                            f"({lo:.2f}-{hi:.2f}s)")
                for ws, we, w in (words or []):
                    if min(we, hi) - max(ws, lo) >= 0.01:
                        errs.append(f"'{w}' ({ws:.2f}s) plays twice: in {a[0]} and {b[0]}")
    for k, (n, a, b) in enumerate(rng[:-1]):
        nxt = rng[k + 1]
        seamless = b - 1e-3 <= nxt[1] <= b + FRAME + 0.005
        for ws, we, w in (words or []):
            if a < ws < b - 0.03 and we > b + 0.06 and not seamless:
                warns.append(f"{n} ends inside '{w}' ({ws:.2f}-{we:.2f}s, cut at {b:.2f}s)")
    return errs, warns


# ------------------------------------------------------------------ onset report

def measure_onsets(segs, db=-26.0):
    """Where speech actually lands inside each segment — measured on its PCM, which has
    no codec priming, so what is measured is what the A-roll plays.

    Measure in the segment, from 0 forward. Do NOT onset-detect in the finished
    A-roll from `start - 0.25`: segments are butt-joined with only ~0.03 s of tail, so
    that window is still inside the PREVIOUS sentence and every boundary falsely reads
    as ~0.25 s of drift.
    """
    out = {}
    for s in segs:
        t = rms_onset(s["wav"], 0.0, 0.60, db)
        out[s["name"]] = t if t is not None else float("nan")
    return out


def report_onsets(onsets, targets, default, tol=LEAD_TOL):
    """Each segment is judged against ITS OWN lead (cutting.speech_lead, the soft-onset
    lead, or the chunk's "lead"), accepted within tol = (-25 ms, +15 ms) of it. A fixed
    0.015-0.055 s window flagged every segment as soon as speech_lead was not 0.04."""
    print(f"\n  onset inside each segment (target = the segment's lead, accept "
          f"{tol[0] * 1000:+.0f}/{tol[1] * 1000:+.0f} ms):")
    bad = 0
    for name, d in onsets.items():
        t = targets.get(name, default)
        ok = d == d and t + tol[0] - 1e-9 <= d <= t + tol[1] + 1e-9
        if not ok:
            bad += 1
        print(f"    {name:>6s}  {d:+.3f}s{'' if abs(t - default) < 1e-9 else f'  (lead {t:.2f})'}"
              f"{'' if ok else '   ← out of range'}")
    return bad


# ------------------------------------------------------------------ whole takes

def keep_whole(src, out, cfg):
    """An AI-avatar render (or a take that needs no cutting) keeps every frame. Render at
    the source's native fps (25 for most avatar renders, 30 for phone footage) — resampling
    to another rate is a needless re-time. Writes the same bounds.json the cutter writes,
    with one segment, so every later step runs unchanged. One AAC encode, no joins, so
    there is nothing to drift; the A/V length gate still runs."""
    fr = hfcfg.probe(src, "stream=r_frame_rate", "v:0") or "25/1"
    n, d = (fr.split("/") + ["1"])[:2]
    fps = round(float(n) / float(d or 1))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    r = hfcfg.run(["ffmpeg", "-v", "error", "-y", "-i", src,
                   "-vf", f"scale=1080:1920:flags=lanczos,fps={fps},setsar=1,setpts=N/({fps}*TB)",
                   "-af", "asetpts=N/SR/TB", "-c:v", "libx264", "-crf", "14", "-preset", "medium",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-ar", "48000", out])
    if r.returncode:
        sys.exit(f"copy failed:\n{r.stderr}")
    frames = hfcfg.frames(out)
    total = round(frames / fps, 4)
    os.makedirs("src", exist_ok=True)
    seg = {"name": "c01", "file": out, "src_start": 0.0, "src_end": total, "dur": total,
           "frames": frames, "start": 0.0}
    json.dump({"mode": "whole", "src": os.path.abspath(src), "bounds": [0.0], "total": total,
               "segments": [seg], "fps": fps},
              open("src/bounds.json", "w"), indent=1)
    if fps != int(cfg["project"].get("fps", 25)):
        print(f"  ! native fps is {fps}: set config project.fps = {fps} (and FRAME = "
              f"{1 / fps:.4f} in scripts/beats.py), and render with --fps {fps}")
    v, a, _ = av_length_gap(out)
    if abs(a - v) > 1.0 / fps + 1e-4:
        print(f"  ! audio {a:.3f}s vs video {v:.3f}s — the raw itself has a longer audio track")
    print(f"  {out}  {total:.3f}s at {fps} fps (kept whole) — src/bounds.json written")
    return 0


# ---------------------------------------------------------------------- main

def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("--src", required=True, help="raw recording")
    ap.add_argument("--chunks", help="chunks.json — the takes you are keeping, in order")
    ap.add_argument("--plan", action="store_true", help="print the speech islands and exit")
    ap.add_argument("--noise", type=float, default=None,
                    help="island level for --plan (dB). Default: -33, or 13 dB under the "
                         "voice on a quiet take")
    ap.add_argument("--refine", type=int, default=2,
                    help="onset refinement passes (0 disables). Each pass re-cuts the audio "
                         "with the measured residual folded in; the frame count never changes.")
    ap.add_argument("--out", default="assets/aroll.mp4")
    ap.add_argument("--words", default="src/raw_words.json",
                    help="raw transcript words — soft-letter openers, and the duplicated-word gate")
    ap.add_argument("--whole", action="store_true",
                    help="keep the take as it is (an AI avatar or a clean single take): copy it "
                         "to the A-roll at its NATIVE fps and write a one-segment bounds.json")
    ap.add_argument("--verify", metavar="AROLL",
                    help="only re-run the A/V sync proof of AROLL against --src and src/bounds.json")
    a = ap.parse_args()
    cfg = hfcfg.load(a.config)
    hfcfg.require("ffmpeg", "ffprobe")

    if a.whole:
        return keep_whole(a.src, a.out, cfg)

    if a.verify:
        b = json.load(open("src/bounds.json"))
        bad = sync_problems(a.verify, a.src, b.get("segments", []))
        for x in bad:
            print(f"  ✗ {x}")
        print("  ✓ in sync" if not bad else "")
        return 1 if bad else 0

    if a.plan or not a.chunks:
        if a.noise is None:
            # -33 dB was tuned on a voice peaking around -20 dB. On a take 18 dB quieter
            # it splits every sentence at its softest syllables (measured: 16 islands
            # became 44), and every split is a join that can stutter or repeat a word.
            # The island level therefore follows the voice, as the onset gate does — but
            # stays 12 dB over the room's RMS floor: silencedetect reads PEAKS, and room
            # noise peaks ~10 dB over its RMS (measured: a noisy quiet take read as ONE
            # island at the voice-relative level).
            ref, floor = level_stats(a.src)
            if ref is None:
                a.noise = -33.0
            else:
                a.noise = round(max(min(-33.0, ref - 13.0), floor + 12.0), 1)
        isl, total = islands(a.src, a.noise)
        print(f"{len(isl)} speech islands in {total:.2f}s at {a.noise} dB\n")
        print("  copy the ones you want into chunks.json, dropping false starts.")
        print("  the LAST take of a repeated line wins.")
        print("  cross-check at -40 dB too: a quietly-spoken take can read as silence\n")
        for i, (s, e) in enumerate(isl):
            print(f'    {{"name":"c{i+1:02d}", "start":{s:.3f}, "end":{e:.3f}}},'
                  f'    # {e - s:5.2f}s')
        return 0

    if int(cfg["project"].get("fps", 25)) != round(1 / FRAME):
        print(f"  ! config project.fps is {cfg['project'].get('fps')}, but a cut A-roll is "
              f"{round(1 / FRAME)} fps — set project.fps = {round(1 / FRAME)} "
              f"(preflight fails on the mismatch)")
    c = cfg["cutting"]
    chunks = json.load(open(a.chunks, encoding="utf-8"))
    eff, ref, floor = effective_onset_db(a.src, c)
    if ref is not None:
        why = []
        if eff < c["onset_db"]:
            why.append("lowered for a quiet recording")
        if abs(eff - (floor + c.get("floor_margin_db", 8.0))) < 0.05:
            why.append("held above the room noise")
        print(f"voice {ref:.1f} dB, room {floor:.1f} dB → onset gate {eff:.1f} dB"
              + (f"  ({'; '.join(why)})" if why else ""))
    c["onset_db"] = eff
    print(f"planning {len(chunks)} chunks…")
    plan = plan_chunks(a.src, chunks, cfg)
    plan = apply_merges(plan, c.get("merge") or [])
    drop = set(c.get("drop") or [])
    plan = [p for p in plan if p["name"] not in drop]
    words = []
    if a.words and os.path.exists(a.words):
        from captions import load_words
        words = load_words(a.words)
    n_soft = mark_soft_onsets(plan, words)
    if n_soft:
        print(f"  {n_soft} chunk(s) open on a soft letter → lead "
              f"{c.get('soft_onset_lead', 0.10):.2f}s: "
              + ", ".join(f"{p['name']} '{p['soft_onset']}'" for p in plan if p.get("soft_onset")))
    plan = merge_touching(plan, cfg)
    own = [p for p in plan if p.get("lead") is not None]
    if own:
        print("  per-chunk lead: " + ", ".join(f"{p['name']} {float(p['lead']):.2f}s" for p in own))
    targets = {p["name"]: lead_for(p, c) for p in plan}
    db = c["onset_db"]

    # ITERATE ON THE AUDIO ONLY: measure the onset in each segment's PCM, shift the
    # start by (onset - target), re-cut, re-measure. PCM carries no codec priming, so
    # the residual is only the 10 ms window grid (pass 0 is normally already within
    # 8 ms). The frame COUNT is held constant across passes — no boundary ever moves.
    trim, segs, total = {}, None, None
    for it in range(a.refine + 1):
        segs, total = layout(plan, cfg, trim)
        for s in segs:
            encode_audio(a.src, s, cfg)
        onsets = measure_onsets(segs, db)
        residual = {n: (d - targets.get(n, c["speech_lead"])) for n, d in onsets.items() if d == d}
        worst = max((abs(v) for v in residual.values()), default=0.0)
        print(f"  pass {it}: worst onset residual {worst * 1000:.0f} ms")
        if worst <= 0.008 or it == a.refine:
            break
        for n, v in residual.items():
            trim[n] = round(trim.get(n, 0.0) + v, 4)

    # GATE: no shared source audio, no word heard twice
    errs, owarn = overlap_problems(segs, words)
    for w in owarn:
        print(f"  ! {w}")
    if errs:
        for e in errs:
            print(f"  ✗ {e}")
        sys.exit("  ✗ OVERLAP: segments share source audio — fix the chunk bounds (or merge "
                 "the chunks); the clamp could not resolve it")
    print(f"  ✓ no shared source audio{', no word plays twice' if words else ''} "
          f"({len(segs)} segments)")

    print(f"  encoding {len(segs)} video segments…")
    for s in segs:
        encode_video(a.src, s)
    real = concat_and_prove(segs, total, a.out)
    bad = report_onsets(measure_onsets(segs, db), targets, c["speech_lead"])

    # GATE: lip sync at every boundary, and the audio as long as the video
    sync_bad = sync_problems(a.out, a.src, segs)
    for x in sync_bad:
        print(f"  ✗ A/V SYNC: {x}")
    if not sync_bad:
        print(f"  ✓ A/V sync: every boundary within {SYNC_TOL * 1000:.0f} ms of the raw")

    os.makedirs("src", exist_ok=True)
    keep = ("name", "file", "video", "wav", "src_start", "src_end", "dur", "frames", "start", "lead",
            "clamped")
    json.dump({"mode": "cut", "src": os.path.abspath(a.src), "fps": round(1 / FRAME),
               "bounds": [s["start"] for s in segs], "total": real,
               "segments": [{k: s[k] for k in keep if k in s} for s in segs]},
              open("src/bounds.json", "w"), indent=1)
    json.dump(plan, open("cuts.json", "w"), ensure_ascii=False, indent=1)

    print(f"\n  {a.out}  {real:.3f}s  ({len(segs)} segments)")
    print("  src/bounds.json written — beats.py and the caption builder read it")
    if sync_bad:
        print("\n  ✗ the A-roll is out of sync — do not build on it")
        return 1
    if bad:
        print(f"\n  ✗ {bad} segment(s) outside the onset target — re-check those chunks")
        return 1
    print("\n  next: map the raw words onto the cut (map_words.py) — captions live in the "
          "final timebase")
    return 0


if __name__ == "__main__":
    sys.exit(main())
