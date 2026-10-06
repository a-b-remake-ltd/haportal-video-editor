#!/usr/bin/env python3
"""Analyse one or more reference reels: measure what can be measured, and lay out the
frames Claude needs to judge the rest by eye.

The user hands over a reel they like (a creator, an agency piece) or their own previous
videos ("my style"). This script never decides the style. It produces:

  style/<ref>/analysis.json   every number, tagged by how it was measured
  style/<ref>/REPORT.md       the STYLE.md skeleton: [M] filled in, [E] left as TODO
  style/<ref>/*.png           sheets to READ with the image viewer (order in REPORT.md)
  style/<ref>/stems/          Demucs vocals / no_vocals split (reused on re-runs)
  style/combined.json         several references: medians + where they disagree
  style/style.draft.json      the measured half of style.json — finish it, save as style.json

Then Claude reads the sheets, writes style/STYLE.md and style/style.json, and runs
scripts/apply_style.py. Method: references/reference-analysis.md.

Measured
  probe          w / h / fps / duration
  cuts           ffmpeg scdet per-frame score → spikes ≥ --threshold (default 8), merged
                 within --min-gap (0.30 s). Shot-length stats, cuts per 30 s, first cut.
  loudness       ebur128 integrated LUFS, LRA, true peak
  sound          Demucs two-stem split (never judge "no music" from pause levels), then
                 voice vs music RMS per 0.5 s, the resting bed level under the voice,
                 swells / drops, the music stem's spectral balance, transient peaks on
                 the no-vocals stem (SFX candidates) and how many cuts carry one
  look           palettegen palette ranked by pixel share, brightness / saturation,
                 per-shot outliers (likely graphics / B-roll)
  captions       heuristic: the row band where bright, edge-dense, CHANGING text lives
                 → centre (% height), glyph height, width, plate-vs-shadow guess
  words / s      --transcribe: runs scripts/transcribe.py if present (else skipped)

Usage
  python3 scripts/analyze_reference.py ref.mp4 [ref2.mp4 ...] [--out style]
          [--no-stems] [--transcribe | --transcript words.json]
          [--threshold 8] [--min-gap 0.30] [--strips 6]

Optional deps: numpy (sound, palette ranking, caption heuristic) and Pillow (labelled
sheets). Without them the script still runs and says what it skipped.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import statistics
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_W, BASE_H = 1080, 1920
SR = 16000                       # analysis sample rate for every audio measurement

try:                             # optional — degrade, never crash
    import numpy as np           # noqa: F401
except ImportError:              # pragma: no cover
    np = None
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:              # pragma: no cover
    Image = ImageDraw = ImageFont = None


def log(msg):
    print(msg, flush=True)


def jdump(obj, path, **kw):
    """json.dump that survives numpy scalars."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False,
                  default=lambda o: o.item() if hasattr(o, "item") else str(o), **kw)


def r(x, n=2):
    return None if x is None else round(float(x), n)


# ======================================================================== probe
def probe_video(path):
    out = hfcfg.run(["ffprobe", "-v", "error", "-show_entries",
                     "stream=codec_type,width,height,r_frame_rate,duration:"
                     "stream_tags=rotate:format=duration", "-of", "json", path]).stdout
    j = json.loads(out or "{}")
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        sys.exit(f"{path}: no video stream")
    a = any(s.get("codec_type") == "audio" for s in j.get("streams", []))
    num, den = (v.get("r_frame_rate") or "25/1").split("/")
    fps = float(num) / float(den or 1)
    w, h = int(v["width"]), int(v["height"])
    if str(v.get("tags", {}).get("rotate", "0")) in ("90", "270", "-90"):
        w, h = h, w
    dur = float(j.get("format", {}).get("duration") or v.get("duration") or 0)
    return {"width": w, "height": h, "fps": r(fps, 3), "duration": r(dur, 3),
            "aspect": f"{w}:{h}", "is_9x16": abs(w / h - 9 / 16) < 0.01, "has_audio": a}


# ========================================================================= cuts
def scene_scores(path, work):
    """Per-frame scdet score (0-100). One decode pass."""
    f = os.path.join(work, "scd.txt")
    hfcfg.run(["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", path, "-an", "-vf",
               f"scdet=threshold=100:sc_pass=0,metadata=print:file={f}", "-f", "null", "-"])
    rows, t = [], None
    if not os.path.exists(f):
        return rows
    for line in open(f):
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
            continue
        m = re.search(r"lavfi\.scd\.score=([\d.]+)", line)
        if m and t is not None:
            rows.append((t, float(m.group(1))))
    return rows


def detect_cuts(scores, thr, min_gap):
    """Spikes above thr, merged within min_gap (the strongest frame of a cluster wins).

    A hard cut is one frame with a big score. Fast motion and whip transitions give a
    run of medium scores; merging keeps one cut per event so the count matches what an
    editor would call "a cut". Medium-score spikes are kept separately as `soft` —
    dissolves, flashes and fast pushes worth a look on the strips.
    """
    def nms(cands):
        cands = sorted(cands, key=lambda x: -x[1])
        kept = []
        for t, s in cands:
            if all(abs(t - k[0]) >= min_gap for k in kept):
                kept.append((t, s))
        return sorted(kept)
    hard = nms([(t, s) for t, s in scores if s >= thr and t > 0.0])
    soft = [x for x in nms([(t, s) for t, s in scores if thr * 0.45 <= s < thr and t > 0.0])
            if all(abs(x[0] - h[0]) >= min_gap for h in hard)]
    return hard, soft


def shot_stats(cuts, dur):
    b = [0.0] + [c for c in cuts] + [dur]
    shots = [{"i": i + 1, "start": r(b[i]), "end": r(b[i + 1]), "dur": r(b[i + 1] - b[i])}
             for i in range(len(b) - 1) if b[i + 1] - b[i] > 0.01]
    d = [s["dur"] for s in shots]
    return shots, {
        "cuts": len(cuts), "shots": len(shots),
        "cuts_per_30s": r(len(cuts) / dur * 30 if dur else 0),
        "shot_mean_s": r(statistics.mean(d)) if d else None,
        "shot_median_s": r(statistics.median(d)) if d else None,
        "shot_min_s": r(min(d)) if d else None,
        "shot_max_s": r(max(d)) if d else None,
        "first_cut_s": r(cuts[0]) if cuts else None,
        "cuts_in_first_3s": sum(1 for c in cuts if c < 3.0),
    }


# ===================================================================== loudness
def loudness(path):
    p = hfcfg.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn",
                   "-af", "ebur128=peak=true", "-f", "null", "-"])
    s = p.stderr
    tail = s[s.rfind("Summary:"):] if "Summary:" in s else s

    def grab(key):
        m = re.findall(key + r":\s+(-?[\d.]+|-inf)", tail)
        try:
            return r(float(m[-1]), 1) if m else None
        except ValueError:
            return None
    return {"integrated_lufs": grab("I"), "lra_lu": grab("LRA"), "true_peak_dbfs": grab("Peak")}


# ======================================================================== audio
def pcm(path, af=None):
    """Mono float PCM at SR. numpy array, or a python list without numpy."""
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vn"]
    if af:
        cmd += ["-af", af]
    cmd += ["-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    out = hfcfg.run(cmd, text=False).stdout
    if np is not None:
        return np.frombuffer(out, np.float32).astype(np.float64)
    import array
    a = array.array("f")
    a.frombytes(out[: len(out) // 4 * 4])
    return list(a)


def env_db(x, win_s):
    """RMS in dB per window."""
    hop = max(1, int(SR * win_s))
    if np is not None:
        n = len(x) // hop
        if n == 0:
            return np.array([])
        e = (x[: n * hop].reshape(n, hop) ** 2).mean(axis=1)
        return 10 * np.log10(e + 1e-12)
    out = []
    for i in range(0, len(x) - hop + 1, hop):
        seg = x[i:i + hop]
        out.append(10 * math.log10(sum(v * v for v in seg) / hop + 1e-12))
    return out


def ranges(flags, step, min_len, values=None):
    """Contiguous True runs → [{start, end, dur, peak_db}]."""
    out, start = [], None
    for i, f in enumerate(list(flags) + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if (i - start) * step >= min_len:
                d = {"start": r(start * step), "end": r(i * step), "dur": r((i - start) * step)}
                if values is not None:
                    seg = [float(v) for v in values[start:i]]
                    d["rel_db"] = r(max(seg, key=abs), 1)
                out.append(d)
            start = None
    return out


def run_demucs(audio_wav, stems_dir):
    """vocals.wav / no_vocals.wav, cached. Returns (vocals, music) paths or None."""
    want = [os.path.join(stems_dir, "vocals.wav"), os.path.join(stems_dir, "no_vocals.wav")]
    if all(os.path.exists(p) for p in want):
        log("  stems: reusing " + stems_dir)
        return want
    if not hfcfg.which("uvx"):
        log("  stems: SKIPPED — uvx not found (install uv). Music level NOT measured.")
        return None
    tmp = tempfile.mkdtemp(prefix="demucs_")
    log("  stems: Demucs two-stem split (≈20-60 s; first run downloads the model) …")
    p = hfcfg.run(["uvx", "--from", "demucs", "--with", "soundfile", "demucs",
                   "--two-stems=vocals", "-o", tmp, audio_wav])
    found = {}
    for root, _, files in os.walk(tmp):
        for f in files:
            if f in ("vocals.wav", "no_vocals.wav"):
                found[f] = os.path.join(root, f)
    if len(found) != 2:
        log("  stems: Demucs FAILED — music level NOT measured.\n" + p.stderr[-600:])
        return None
    os.makedirs(stems_dir, exist_ok=True)
    for f, src in found.items():
        shutil.move(src, os.path.join(stems_dir, f))
    shutil.rmtree(tmp, ignore_errors=True)
    return want


def spectral_balance(x):
    if np is None or len(x) < SR:
        return None
    n = SR                                          # 1 s frames
    k = len(x) // n
    spec = np.abs(np.fft.rfft(x[: k * n].reshape(k, n) * np.hanning(n), axis=1)) ** 2
    p = spec.sum(axis=0)
    f = np.fft.rfftfreq(n, 1 / SR)
    tot = p[f >= 20].sum() + 1e-12

    def share(a, b):
        return r(p[(f >= a) & (f < b)].sum() / tot, 3)
    return {"sub_20_80": share(20, 80), "low_80_250": share(80, 250),
            "mid_250_2k": share(250, 2000), "high_2k_8k": share(2000, 8000)}


def transients(x, cuts, step=0.01):
    """Onset peaks on the no-vocals stem: ≥10 dB over a ±1.5 s running median and ≥8 dB
    rise within 100 ms. Periodic runs (≥3 at near-equal spacing) are the music's own
    pulse, not SFX, and are split out."""
    if np is None:
        return None
    from numpy.lib.stride_tricks import sliding_window_view as sw
    e = env_db(x, step)
    if len(e) < 50:
        return None
    pad = 150
    rm = np.median(sw(np.pad(e, (pad, pad), mode="edge"), 2 * pad + 1), axis=1)
    on, last = [], -9.0
    for i in range(10, len(e)):
        t = i * step
        if e[i] >= rm[i] + 10 and e[i] - e[i - 10:i].min() >= 8 and t - last > 0.4:
            on.append(round(t, 2))
            last = t
    periodic = set()
    for i in range(len(on) - 2):
        a, b = on[i + 1] - on[i], on[i + 2] - on[i + 1]
        if max(a, b) < 0.9 and abs(a - b) <= 0.12 * max(a, b):
            periodic.update((i, i + 1, i + 2))
    iso = [t for i, t in enumerate(on) if i not in periodic]
    pul = [t for i, t in enumerate(on) if i in periodic]
    dur = len(e) * step
    on_cut = [c for c in cuts if any(abs(c - t) <= 0.2 for t in iso)]
    return {"isolated": iso, "pulse": pul,
            "isolated_per_min": r(len(iso) / dur * 60, 1),
            "all_per_min": r(len(on) / dur * 60, 1),
            "cuts_with_transient": len(on_cut),
            "cuts_with_transient_share": r(len(on_cut) / len(cuts), 2) if cuts else None}


def sound(path, out_dir, use_stems, cuts):
    res = {"method": None}
    work = os.path.join(out_dir, "stems")
    os.makedirs(work, exist_ok=True)
    wav = os.path.join(work, "mix.wav")
    if not os.path.exists(wav):
        hfcfg.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-ac", "2", "-ar", "44100", wav])
    stems = run_demucs(wav, work) if use_stems else None
    if np is None:
        res["method"] = "skipped — numpy missing (pip install numpy)"
        return res, None
    step = 0.5
    if not stems:
        mix = env_db(pcm(wav), step)
        res.update({
            "method": "mix only (no stems)",
            "music_present": None,
            "warning": "NOT measured. Never conclude 'no music' from pause levels — rerun "
                       "without --no-stems (Demucs) before deciding anything about music.",
            "mix_rms_db_median": r(np.median(mix), 1),
        })
        return res, None
    v, m = pcm(stems[0]), pcm(stems[1])
    V, M = env_db(v, step), env_db(m, step)
    n = min(len(V), len(M))
    V, M = V[:n], M[:n]
    act = V > max(-45.0, float(np.percentile(V, 95)) - 18)
    if act.sum() < 2:
        act = V > float(np.percentile(V, 50))
    under = V - M
    music_med = float(np.median(M[act])) if act.any() else float(np.median(M))
    # smooth 1.5 s, compare against the bed's own median during speech
    from numpy.lib.stride_tricks import sliding_window_view as sw
    sm = np.median(sw(np.pad(M, (1, 1), mode="edge"), 3), axis=1)
    rel = sm - music_med
    swell, drop = rel >= 4.0, rel <= -6.0
    rest = act & ~swell
    hook = np.arange(n) * step < 3.0
    under_rest = float(np.median(under[rest])) if rest.any() else float(np.median(under[act]))
    present = bool(music_med > -50 and np.median(under[act]) < 30)
    res.update({
        "method": "demucs htdemucs two-stem (vocals / no_vocals), RMS per 0.5 s",
        "voice_rms_db_speech": r(np.median(V[act]), 1),
        "music_rms_db_speech": r(music_med, 1),
        "music_rms_db_pauses": r(np.median(M[~act]), 1) if (~act).any() else None,
        "music_present": present,
        "music_db_under_voice_rest": r(under_rest, 1),
        "music_db_under_voice_median": r(np.median(under[act]), 1),
        "music_db_under_voice_p25_p75": [r(np.percentile(under[act], 25), 1),
                                         r(np.percentile(under[act], 75), 1)],
        "music_db_under_voice_hook_0_3s": r(np.median(under[act & hook]), 1) if (act & hook).any() else None,
        "music_db_under_voice_by_third": [
            r(np.median(under[rest & (np.arange(n) * 3 // n == k)]), 1)
            if (rest & (np.arange(n) * 3 // n == k)).any() else None for k in range(3)],
        "music_swells": ranges(swell, step, 1.0, rel) if present else [],
        "music_drops": ranges(drop, step, 1.0, rel) if present else [],
        "music_spectral_share": spectral_balance(m) if present else None,
        "speech_share": r(act.mean(), 2),
        "first_voice_s": None,
        "transients_no_vocals": transients(m, cuts),
    })
    fine = env_db(v, 0.02)
    thr = float(np.median(V[act])) - 15
    idx = np.where(fine > thr)[0]
    res["first_voice_s"] = r(idx[0] * 0.02) if len(idx) else None
    series = {"step_s": step, "voice_db": [r(x, 1) for x in V], "music_db": [r(x, 1) for x in M]}
    return res, series


# ======================================================================= frames
def frame_size(info, w):
    h = int(round(w * info["height"] / info["width"] / 2)) * 2
    return w, h


def grab(path, t, w, h, info):
    """One RGB frame at t (accurate seek) → PIL image, or raw bytes without PIL."""
    t = max(0.0, min(t, info["duration"] - 1.0 / max(info["fps"], 1)))
    out = hfcfg.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
                     "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                    text=False).stdout
    if len(out) < w * h * 3:
        out = bytes(w * h * 3)
    if Image is None:
        return out[: w * h * 3]
    return Image.frombytes("RGB", (w, h), out[: w * h * 3])


def _font(size):
    if ImageFont is None:
        return None
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                                  # Pillow < 10.1
        return ImageFont.load_default()


def label(img, text, size=18, pos="tl"):
    if ImageDraw is None or not text:
        return img
    d = ImageDraw.Draw(img)
    f = _font(size)
    x0, y0, x1, y1 = d.textbbox((0, 0), text, font=f)
    tw, th = x1 - x0, y1 - y0
    x = 4 if pos[1] == "l" else img.width - tw - 12
    y = 4 if pos[0] == "t" else img.height - th - 12
    d.rectangle([x, y, x + tw + 8, y + th + 8], fill=(0, 0, 0))
    d.text((x + 4 - x0, y + 4 - y0), text, fill=(255, 255, 0), font=f)
    return img


def sheet(tiles, cols, out, gap=4, labels=None, size=18, w=None, h=None, title=None):
    """Tile frames into one PNG. PIL → labelled; otherwise ffmpeg tile, unlabelled."""
    if not tiles:
        return None
    if Image is None:
        tmp = tempfile.mkdtemp(prefix="sheet_")
        for i, raw in enumerate(tiles):
            open(os.path.join(tmp, f"f{i:04d}.rgb"), "wb").write(raw)
            hfcfg.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                       "-s", f"{w}x{h}", "-i", os.path.join(tmp, f"f{i:04d}.rgb"),
                       os.path.join(tmp, f"f{i:04d}.png")])
        rows = math.ceil(len(tiles) / cols)
        hfcfg.run(["ffmpeg", "-v", "error", "-y", "-framerate", "1", "-i",
                   os.path.join(tmp, "f%04d.png"), "-vf", f"tile={cols}x{rows}:padding={gap}",
                   "-frames:v", "1", out])
        shutil.rmtree(tmp, ignore_errors=True)
        return out
    tw, th = tiles[0].size
    rows = math.ceil(len(tiles) / cols)
    top = 34 if title else 0
    canvas = Image.new("RGB", (cols * tw + (cols - 1) * gap, top + rows * th + (rows - 1) * gap),
                       (40, 40, 40))
    for i, im in enumerate(tiles):
        im = im.copy()
        if labels:
            label(im, labels[i], size)
        canvas.paste(im, ((i % cols) * (tw + gap), top + (i // cols) * (th + gap)))
    if title:
        label(canvas, title, 20)
    canvas.save(out)
    return out


def paged(tiles, labels, cols, per_page, base, **kw):
    outs = []
    for p in range(0, len(tiles), per_page):
        n = p // per_page + 1
        out = base if n == 1 else base.replace(".png", f"_{n}.png")
        outs.append(sheet(tiles[p:p + per_page], cols, out,
                          labels=labels[p:p + per_page] if labels else None, **kw))
    return outs


def decode_lowres(path, info, fps=4, w=180):
    """All frames at fps, w-wide, as a uint8 array (N, h, w, 3)."""
    if np is None:
        return None
    w, h = frame_size(info, w)
    out = hfcfg.run(["ffmpeg", "-v", "error", "-i", path, "-an", "-vf", f"fps={fps},scale={w}:{h}",
                     "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], text=False).stdout
    n = len(out) // (w * h * 3)
    return np.frombuffer(out[: n * w * h * 3], np.uint8).reshape(n, h, w, 3)


# ===================================================================== captions
def caption_estimate(fr, fps):
    """Where the captions live, from bright edge-dense pixels that CHANGE over time.

    Captions swap every ~0.5-1.5 s; a white wall or a logo does not. Rows where the
    bright-edge mask keeps changing peak at the caption band. Heuristic — confirm on
    caption_band.png before using the number.
    """
    if fr is None or len(fr) < 4:
        return None
    N, H, W, _ = fr.shape
    a = fr.astype(np.int16)
    mn, mx = a.min(axis=3), a.max(axis=3)
    white = (mn > 215) & (mx - mn < 30)
    gx = np.zeros(white.shape, bool)
    gx[:, :, 1:] = np.abs(np.diff(a.mean(axis=3), axis=2)) > 60
    txt = white & gx
    c0, c1 = int(W * 0.10), int(W * 0.90)
    y0, y1 = int(H * 0.12), int(H * 0.92)
    t = txt[:, :, c0:c1]
    change = (t[1:] != t[:-1]).sum(axis=2).sum(axis=0).astype(float)
    change[:y0] = 0
    change[y1:] = 0
    k = np.ones(3) / 3
    prof = np.convolve(change, k, mode="same")
    peak = int(prof.argmax())
    if prof[peak] <= 0:
        return None
    half = prof[peak] / 2
    lo = peak
    while lo > 0 and prof[lo - 1] >= half:
        lo -= 1
    hi = peak
    while hi < H - 1 and prof[hi + 1] >= half:
        hi += 1
    rows = np.arange(lo, hi + 1)
    centre = float((prof[rows] * rows).sum() / prof[rows].sum())
    base = float(np.median(prof[y0:y1])) + 1e-6
    ratio = prof[peak] / base
    conf = "high" if ratio >= 6 else "medium" if ratio >= 3 else "low"
    band = txt[:, lo:hi + 1, :]
    per_frame = band.sum(axis=(1, 2))
    on = per_frame >= max(4, np.percentile(per_frame, 60) * 0.5)
    widths, fills = [], []
    wb = white[:, lo:hi + 1, :]
    for i in np.where(on)[0]:
        xs = np.where(band[i].any(axis=0))[0]
        if len(xs) >= 2:
            x0, x1 = np.percentile(xs, 3), np.percentile(xs, 97)
            widths.append((x1 - x0) / W)
            seg = wb[i][:, int(x0):int(x1) + 1]
            fills.append(seg.mean() if seg.size else 0)
    fill = float(np.median(fills)) if fills else None
    glyph_px = (hi - lo + 1) / H * BASE_H
    style_guess = None
    if fill is not None:
        style_guess = "plate" if fill > 0.55 else "shadow"
    # times with a caption on screen, spread out — for the grid overlays
    on_t = [round(i / fps, 2) for i in np.where(on)[0]]
    return {
        "method": "heuristic: rows where bright, edge-dense pixels change between frames",
        "confidence": conf, "peak_to_median": r(ratio, 1),
        "center_pct": r(centre / H * 100, 1),
        "center_y_1920": int(round(centre / H * BASE_H)),
        "band_pct": [r(lo / H * 100, 1), r((hi + 1) / H * 100, 1)],
        "glyph_height_px_1920": int(round(glyph_px)),
        "width_pct_median": r(statistics.median(widths) * 100, 1) if widths else None,
        "width_pct_range": [r(min(widths) * 100, 1), r(max(widths) * 100, 1)] if widths else None,
        "white_fill_in_band": r(fill, 2),
        "style_guess": style_guess,
        "on_screen_share": r(on.mean(), 2),
        "_on_times": on_t,
    }


# ========================================================================= look
def palette(path, info, fr, work):
    """palettegen on 1 fps thumbnails, ranked by how many pixels each colour wins."""
    pal_png = os.path.join(work, "palette.png")
    hfcfg.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-an", "-vf",
               "fps=1,scale=160:-2,palettegen=max_colors=32:stats_mode=full:reserve_transparent=0",
               "-frames:v", "1", pal_png])
    if not os.path.exists(pal_png):
        return None
    raw = hfcfg.run(["ffmpeg", "-v", "error", "-i", pal_png, "-f", "rawvideo", "-pix_fmt", "rgb24",
                     "-"], text=False).stdout
    cols = []
    for i in range(0, len(raw) - 2, 3):
        c = (raw[i], raw[i + 1], raw[i + 2])
        if c not in cols:
            cols.append(c)
    import colorsys

    def tags(c):
        h, l, s = colorsys.rgb_to_hls(*[x / 255 for x in c])
        _, _, v = colorsys.rgb_to_hsv(*[x / 255 for x in c])
        _, s2, _ = colorsys.rgb_to_hsv(*[x / 255 for x in c])
        out = []
        if l < 0.10:
            out.append("near-black")
        elif l > 0.92 and s2 < 0.15:
            out.append("near-white")
        elif s2 < 0.15:
            out.append("neutral")
        if 0.01 <= h <= 0.12 and 0.18 <= s2 <= 0.65 and 0.35 <= v <= 0.97:
            out.append("skin-ish")
        if s2 >= 0.45 and v >= 0.35:
            out.append("accent")
        return out, s2, v
    share = {}
    if np is not None and fr is not None and len(fr):
        px = fr[:: max(1, len(fr) // 60), ::3, ::3].reshape(-1, 3).astype(np.int32)
        P = np.array(cols, np.int32)
        best = np.zeros(len(px), int)
        bd = np.full(len(px), 1 << 30)
        for j, c in enumerate(P):
            d = ((px - c) ** 2).sum(axis=1)
            m = d < bd
            bd[m], best[m] = d[m], j
        cnt = np.bincount(best, minlength=len(P))
        share = {tuple(cols[j]): cnt[j] / cnt.sum() for j in range(len(P))}
    entries = []
    for c in cols:
        tg, s2, v = tags(c)
        entries.append({"hex": "#%02X%02X%02X" % c, "share": r(share.get(c, 0), 3),
                        "sat": r(s2, 2), "val": r(v, 2), "tags": tg})
    entries.sort(key=lambda e: -e["share"])
    dominant = [e for e in entries if not ({"near-black", "near-white"} & set(e["tags"]))][:6]
    accents = hue_accents(fr)
    if accents is None:                               # no numpy: palettegen's saturated entries
        accents = sorted([e for e in entries if "accent" in e["tags"] and "skin-ish" not in e["tags"]],
                         key=lambda e: -(e["sat"] * e["val"]))[:6]
    return {"method": "dominant: palettegen(32) on 1 fps thumbs, ranked by nearest-colour pixel "
                      "share; accents: hue clusters of bright saturated pixels (S, V >= 0.55)",
            "dominant": dominant, "accents": accents, "all": entries}


def hue_accents(fr):
    """Graphic colours hide in a few % of the pixels, so palettegen averages them away.
    Cluster only the bright, saturated pixels by hue (24 bins); represent each cluster by
    the median of its brightest half. Skin and sky still show up — they are tagged."""
    if np is None or fr is None or not len(fr):
        return None
    px = fr[:, ::2, ::2].reshape(-1, 3).astype(np.float32) / 255
    mx, mn = px.max(axis=1), px.min(axis=1)
    s = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    sel = (s >= 0.55) & (mx >= 0.55)
    if sel.sum() < 50:
        return []
    P, S = px[sel], s[sel]
    R, G, B = P[:, 0], P[:, 1], P[:, 2]
    M, d = P.max(axis=1), P.max(axis=1) - P.min(axis=1) + 1e-6
    h = np.where(M == R, ((G - B) / d) % 6, np.where(M == G, (B - R) / d + 2, (R - G) / d + 4)) / 6
    bins = (h * 24).astype(int) % 24
    cnt = np.bincount(bins, minlength=24)
    out = []
    for k in np.argsort(-cnt):
        share = cnt[k] / sel.sum()
        if share < 0.02 or len(out) >= 6:
            break
        sub = P[bins == k]
        top = sub[sub.max(axis=1) >= np.median(sub.max(axis=1))]
        c = np.median(top, axis=0)
        hue = int(k) * 15
        tags = ["skin-ish"] if hue <= 45 and float(np.median(S[bins == k])) < 0.75 else []
        out.append({"hex": "#%02X%02X%02X" % tuple(int(round(x * 255)) for x in c),
                    "hue_deg": hue, "share_of_saturated": r(share, 3),
                    "share_of_frame": r(cnt[k] / len(px), 4), "tags": tags})
    return out


def brightness(fr, shots, fps):
    if np is None or fr is None or not len(fr):
        return None, []
    a = fr.astype(np.float32) / 255
    mx, mn = a.max(axis=3), a.min(axis=3)
    luma = (0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]).mean(axis=(1, 2))
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0).mean(axis=(1, 2))
    rgb = a.mean(axis=(1, 2))
    contrast = (np.percentile(a.mean(axis=3).reshape(len(a), -1), 95, axis=1)
                - np.percentile(a.mean(axis=3).reshape(len(a), -1), 5, axis=1))
    prof = {"luma_mean": r(luma.mean()), "luma_p10_p90": [r(np.percentile(luma, 10)), r(np.percentile(luma, 90))],
            "sat_mean": r(sat.mean()), "sat_p10_p90": [r(np.percentile(sat, 10)), r(np.percentile(sat, 90))],
            "contrast_p5_p95_mean": r(contrast.mean()),
            "scale": "0-1 (luma Rec.709 on RGB, HSV saturation)"}
    per = []
    for s in shots:
        i0, i1 = int(s["start"] * fps), max(int(s["start"] * fps) + 1, int(s["end"] * fps))
        i1 = min(i1, len(a))
        if i0 >= len(a):
            continue
        per.append((s["i"], float(luma[i0:i1].mean()), float(sat[i0:i1].mean()), rgb[i0:i1].mean(axis=0)))
    out = []
    if per:
        L = np.array([p[1] for p in per])
        S = np.array([p[2] for p in per])
        C = np.array([p[3] for p in per])
        mc = np.median(C, axis=0)
        for (i, l, s_, c) in per:
            dist = float(np.sqrt(((c - mc) ** 2).sum()))
            why = []
            if abs(l - np.median(L)) > 0.18:
                why.append("brighter" if l > np.median(L) else "darker")
            if abs(s_ - np.median(S)) > 0.15:
                why.append("more saturated" if s_ > np.median(S) else "less saturated")
            if dist > 0.16:
                why.append("different colour")
            if why:
                out.append({"shot": i, "why": ", ".join(why), "luma": r(l), "sat": r(s_)})
    return prof, out


# =================================================================== transcribe
def find_words(obj, acc):
    if isinstance(obj, dict):
        if ("word" in obj or "text" in obj) and "start" in obj and "end" in obj \
                and not isinstance(obj.get("words"), list):
            acc.append(obj)
        for v in obj.values():
            find_words(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            find_words(v, acc)
    return acc


def transcribe(audio, out_json, cfg_path=None):
    """Call the skill's own transcriber if it exists. Never fatal."""
    tr = os.path.join(HERE, "transcribe.py")
    if not os.path.exists(tr):
        log("  transcribe: SKIPPED — scripts/transcribe.py not found")
        return None
    hp = hfcfg.run([sys.executable, tr, "--help"]).stdout
    cmd = [sys.executable, tr, audio]
    if "--out" in hp:
        cmd += ["--out", out_json]
    elif re.search(r"(^|\s)-o[\s,]", hp):
        cmd += ["-o", out_json]
    if cfg_path and "--config" in hp:
        cmd += ["--config", cfg_path]
    log("  transcribe: " + " ".join(os.path.basename(c) if i < 2 else c for i, c in enumerate(cmd)))
    p = hfcfg.run(cmd)
    if not os.path.exists(out_json):
        cand = os.path.splitext(audio)[0] + ".json"
        if os.path.exists(cand):
            shutil.copy(cand, out_json)
    if not os.path.exists(out_json):
        log("  transcribe: FAILED (no json) — skipped\n" + (p.stderr or "")[-400:])
        return None
    return out_json


def words_stats(js_path, dur, speech_share):
    try:
        words = find_words(json.load(open(js_path, encoding="utf-8")), [])
    except (OSError, ValueError):
        return None
    if not words:
        return None
    n = len(words)
    first = min(float(w["start"]) for w in words)
    speech = (speech_share or 1) * dur
    return {"words": n, "words_per_s_overall": r(n / dur), "words_per_s_speech": r(n / speech) if speech else None,
            "words_first_3s": sum(1 for w in words if float(w["start"]) < 3),
            "first_word_s": r(first),
            "text_first_3s": " ".join(str(w.get("word", w.get("text", ""))).strip()
                                      for w in words if float(w["start"]) < 3)}


# ======================================================================= sheets
def make_sheets(path, info, shots, cuts, cap, out_dir, n_strips):
    made = {}
    fps = info["fps"] or 25
    dur = info["duration"]
    # one mid-frame per shot
    w, h = frame_size(info, 180)
    tiles = [grab(path, (s["start"] + s["end"]) / 2, w, h, info) for s in shots]
    labs = [f"#{s['i']} {s['start']:.1f}s ({s['dur']:.1f})" for s in shots]
    made["sheet_shots"] = paged(tiles, labs, 7, 35, os.path.join(out_dir, "sheet_shots.png"),
                                w=w, h=h, size=16)
    # one frame per second
    w, h = frame_size(info, 160)
    ts = [i + 0.5 for i in range(int(dur))]
    tiles = [grab(path, t, w, h, info) for t in ts]
    made["sheet_1s"] = paged(tiles, [f"{t:.1f}s" for t in ts], 8, 40,
                             os.path.join(out_dir, "sheet_1s.png"), w=w, h=h, size=16)
    # the hook, every 0.25 s
    w, h = frame_size(info, 200)
    ts = [i * 0.25 for i in range(13) if i * 0.25 < dur]
    tiles = [grab(path, t, w, h, info) for t in ts]
    made["hook"] = sheet(tiles, 7, os.path.join(out_dir, "hook_0-3s.png"),
                         labels=[f"{t:.2f}s" for t in ts], w=w, h=h, size=18)
    # transitions: 7 consecutive frames around the first cuts
    sd = os.path.join(out_dir, "strips")
    os.makedirs(sd, exist_ok=True)
    made["strips"] = []
    w, h = frame_size(info, 200)
    for k, c in enumerate(cuts[:n_strips]):
        ts = [c + j / fps - 0.4 / fps for j in range(-3, 4)]
        tiles = [grab(path, max(0, t), w, h, info) for t in ts]
        labs = [("CUT " if j == 0 else f"{j:+d}f ") + f"{c + j / fps:.2f}" for j in range(-3, 4)]
        made["strips"].append(sheet(tiles, 7, os.path.join(sd, f"cut_{k + 1:02d}.png"),
                                    labels=labs, w=w, h=h, size=16))
    # caption band over time
    lo, hi = 0.50, 0.80
    if cap and cap.get("center_pct"):
        cp = cap["center_pct"] / 100
        lo, hi = max(0.05, min(lo, cp - 0.12)), min(0.98, max(hi, cp + 0.12))
    if Image is not None:
        W, H = frame_size(info, 360)
        y0, y1 = int(H * lo), int(H * hi)
        ts = [i + 0.5 for i in range(int(dur))]
        tiles = []
        for t in ts:
            im = grab(path, t, W, H, info).crop((0, y0, W, y1))
            d = ImageDraw.Draw(im)
            f = _font(12)
            for pct in range(int(math.ceil(lo * 20)) * 5, int(hi * 100) + 1, 5):
                y = int(H * pct / 100) - y0
                if not 9 <= y < im.height - 4:
                    continue
                d.line([(0, y), (14, y)], fill=(255, 0, 255), width=2)
                d.line([(W - 14, y), (W, y)], fill=(255, 0, 255), width=2)
                x0_, y0_, x1_, y1_ = d.textbbox((0, 0), f"{pct}%", font=f)
                d.rectangle([16, y - 8, 20 + x1_ - x0_, y - 8 + y1_ - y0_ + 4], fill=(0, 0, 0))
                d.text((18 - x0_, y - 6 - y0_), f"{pct}%", fill=(255, 0, 255), font=f)
            tiles.append(label(im, f"{t:.1f}s", 14, "tr"))
        made["caption_band"] = paged(tiles, None, 4, 32, os.path.join(out_dir, "caption_band.png"),
                                     gap=3, title=f"frame rows {lo * 100:.0f}-{hi * 100:.0f}% · "
                                                  "magenta ticks every 5% of height")
    # Reels grid over key frames (frames letterboxed to 1080x1920 first)
    made["grid"] = grid_sheets(path, info, shots, cap, out_dir)
    return made


def grid_sheets(path, info, shots, cap, out_dir):
    try:
        import grid
    except Exception as e:                            # pragma: no cover
        log(f"  grid: SKIPPED ({e})")
        return []
    g = grid.profile("reels", BASE_W, BASE_H)
    times = []
    on = (cap or {}).get("_on_times") or []
    if on:
        for q in (0.15, 0.4, 0.65, 0.9):
            times.append(on[min(len(on) - 1, int(len(on) * q))])
    step = max(1, len(shots) // 4)
    for s in shots[::step][:4]:
        times.append(round((s["start"] + s["end"]) / 2, 2))
    times = sorted(set(times))
    tmp = tempfile.mkdtemp(prefix="refgrid_")
    stills = []
    for i, t in enumerate(times):
        src = os.path.join(tmp, f"s{i:02d}.png")
        hfcfg.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
                   "-vf", f"scale={BASE_W}:{BASE_H}:force_original_aspect_ratio=decrease,"
                          f"pad={BASE_W}:{BASE_H}:(ow-iw)/2:(oh-ih)/2:color=black", src])
        dst = os.path.join(tmp, f"g{i:02d}.png")
        try:
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                grid.overlay(src, [None], dst, g, thumb_w=360)
        except SystemExit:
            continue
        if os.path.exists(dst):
            stills.append((t, dst))
    outs = []
    for n in range(0, len(stills), 4):
        grp = stills[n:n + 4]
        out = os.path.join(out_dir, f"grid_{n // 4 + 1}.png")
        if Image is not None:
            ims = [label(Image.open(p).convert("RGB"), f"{t:.2f}s", 18) for t, p in grp]
            sheet(ims, len(ims), out)
        else:
            ins = []
            for _, p in grp:
                ins += ["-i", p]
            hfcfg.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex",
                       "".join(f"[{i}]" for i in range(len(grp))) + f"hstack={len(grp)}", out]
                      if len(grp) > 1 else ["ffmpeg", "-v", "error", "-y", "-i", grp[0][1], out])
        outs.append({"file": out, "times": [t for t, _ in grp]})
    shutil.rmtree(tmp, ignore_errors=True)
    return outs


# ======================================================================= report
TODO = "> **TODO [E]** "


def fmt_ranges(rs):
    return ", ".join(f"{x['start']:.1f}-{x['end']:.1f}s ({x['rel_db']:+.0f} dB)" for x in rs) or "none"


def report(name, src, a, made, out_dir):
    p, rh, cap, snd = a["probe"], a["rhythm"], a.get("captions") or {}, a.get("sound") or {}
    look, ws = a.get("look") or {}, a.get("words")
    L = []
    w = L.append
    w(f"# {name} — reference analysis (skeleton)\n")
    w(f"Reference: `{os.path.basename(src)}` — {p['width']}x{p['height']} @{p['fps']:g} fps, "
      f"{p['duration']:.1f} s.  ")
    w("[M] = measured by `analyze_reference.py` (numbers in `analysis.json`). "
      "[M~] = heuristic, confirm by eye. [E] = observed by eye — every TODO below is yours.\n")
    w("This file is a **skeleton**. Read the images in the order of §0, fill every TODO, then "
      "write the final `style/STYLE.md` (one per reel, merging references) and "
      "`style/style.json`. Method: `references/reference-analysis.md`.\n")
    w("**Style ≠ template.** Copy the editing LANGUAGE; every object, word and animation in "
      "the new edit comes from the new video's own script.\n")

    w("## 0. Look at these, in this order\n")
    order = [("hook_0-3s.png", "the first 3 s, every 0.25 s — fill §5")]
    order += [(os.path.basename(x), "one mid-frame per shot, `#index start (dur)` — mark each A/B/G in §2")
              for x in made.get("sheet_shots") or []]
    order += [(os.path.basename(x), "caption band over time, magenta ticks = % of height — fill §3")
              for x in made.get("caption_band") or []]
    order += [("strips/" + os.path.basename(x), "7 frames around a cut — how cuts are made, §2/§6")
              for x in made.get("strips") or []]
    order += [(os.path.basename(x), "one frame per second — overall feel, designed moments §1/§6")
              for x in made.get("sheet_1s") or []]
    order += [(os.path.basename(x["file"]), "Reels grid over key frames: red = hidden by the app, "
               "yellow = house caption band — does the reference fit the grid?")
              for x in made.get("grid") or []]
    for i, (f, why) in enumerate(order, 1):
        w(f"{i}. `{f}` — {why}")
    if Image is None:
        w("\n(Pillow missing: sheets are unlabelled. Tiles run left→right, top→bottom in the "
          "order of the tables below.)")
    w("")

    w("## 1. Overall feel [E]\n")
    w(TODO + "2-4 bullets: what kind of piece (creator reel / agency / UGC / tutorial), energy, "
      "density, restraint. What makes it feel like itself?\n")

    w("## 2. Rhythm [M]\n")
    w(f"- {rh['cuts']} cuts in {p['duration']:.1f} s → **{rh['cuts_per_30s']} cuts per 30 s**. "
      f"Shot length: mean {rh['shot_mean_s']} s, **median {rh['shot_median_s']} s**, "
      f"min {rh['shot_min_s']} s, max {rh['shot_max_s']} s.")
    w(f"- First cut at {rh['first_cut_s']} s; {rh['cuts_in_first_3s']} cut(s) inside the first 3 s.")
    if snd.get("first_voice_s") is not None:
        w(f"- First voice at {snd['first_voice_s']} s (vocals stem).")
    if ws:
        w(f"- Speech pace [M]: {ws['words_per_s_speech']} words/s while talking "
          f"({ws['words_per_s_overall']} overall), {ws['words_first_3s']} words in the first 3 s.")
    w(f"- Detector: scdet score ≥ {a['cuts']['threshold']}, merged within {a['cuts']['min_gap_s']} s. "
      f"Soft candidates (dissolves / flashes / fast pushes, not counted): "
      f"{', '.join(f'{t:.2f}' for t, _ in a['cuts']['soft'][:15]) or 'none'}.")
    w("\n| shot | start | end | dur | type A/B/G [E] | what is on screen [E] |\n|---|---|---|---|---|---|")
    outl = {o["shot"]: o["why"] for o in look.get("outlier_shots", [])}
    for s in a["shots"]:
        hint = f"(looks different: {outl[s['i']]})" if s["i"] in outl else ""
        w(f"| {s['i']} | {s['start']:.2f} | {s['end']:.2f} | {s['dur']:.2f} |  | {hint} |")
    w("")
    w(TODO + "A = speaker, B = B-roll, G = graphic / designed moment. Then "
      "`broll_share` = (B + G time) / duration. Do cuts land on phrase boundaries? Angle "
      "changes, punch-ins, jump cuts? (strips/)\n")

    w("## 3. Captions [M~ position, E style]\n")
    if cap:
        w(f"- Centre ≈ **{cap['center_pct']} % of height** (= y {cap['center_y_1920']} on 1920), "
          f"text rows {cap['band_pct'][0]}-{cap['band_pct'][1]} %, confidence {cap['confidence']} "
          f"(peak/median {cap['peak_to_median']}).")
        w(f"- Glyph-band height ≈ {cap['glyph_height_px_1920']} px at 1920 (FWHM, under-reads "
          f"ascenders). Line width median {cap['width_pct_median']} % of frame "
          f"(range {cap['width_pct_range']}). On screen ≈ {int((cap['on_screen_share'] or 0) * 100)} % of the time.")
        w(f"- White fill inside the text band: {cap['white_fill_in_band']} → guess "
          f"**{cap['style_guess']}** (plate > 0.55 > shadow). Verify on `caption_band.png`.")
    else:
        w("- Not measured (numpy missing or no captions found).")
    w("")
    w(TODO + "Words per card (1-4)? One line or two? Colour, weight (thin / medium / bold / black), "
      "plate / shadow / stroke, case, font family (closest FREE face), in/out animation "
      "(hard swap / pop / fade / word-by-word highlight), what happens to captions during "
      "full-screen graphics. Does the caption centre fit the grid (`grid_*.png`)?\n")

    w("## 4. Kinetic / headline type [E, timings M where possible]\n")
    w(TODO + "Is there a second type layer beyond captions (big headline words, keyword "
      "highlights)? How it builds (word by word on speech? typewriter?), weights and colours, "
      "size as % of frame width, alignment, how it exits.\n")

    w("## 5. Hook, second by second [M + E]\n")
    w(f"- [M] First cut {rh['first_cut_s']} s; {rh['cuts_in_first_3s']} cut(s) before 3 s; "
      f"first voice {snd.get('first_voice_s')} s.")
    if snd.get("music_db_under_voice_hook_0_3s") is not None:
        w(f"- [M] Music sits {snd['music_db_under_voice_hook_0_3s']} dB under the voice in 0-3 s "
          f"(rest of the video: {snd.get('music_db_under_voice_rest')} dB).")
    if ws and ws.get("text_first_3s"):
        w(f"- [M] Words in 0-3 s: “{ws['text_first_3s']}”")
    w("")
    w(TODO + "From `hook_0-3s.png`: one line per 0.25-0.5 s — what is on screen, what moves, "
      "how the first frame earns the stop.\n")

    w("## 6. Designed moments [E]\n")
    if look.get("outlier_shots"):
        w("- [M~] Shots that look different from the median shot (likely graphics / B-roll): " +
          ", ".join(f"#{o['shot']} ({o['why']})" for o in look["outlier_shots"]) + ".")
    w(TODO + "Each full-screen or overlay moment: time range, what it shows, how it enters / exits, "
      "what line it illustrates. Write them as EXAMPLES of a device (\"UI metaphor that types "
      "the spoken query\"), never as objects to copy.\n")

    w("## 7. Sound [M]\n")
    if snd.get("music_present") is None:
        w(f"- **Music NOT measured** ({snd.get('method')}). {snd.get('warning', '')}")
    else:
        w(f"- Method: {snd['method']}.")
        w(f"- Music present: **{'yes' if snd['music_present'] else 'no'}** "
          f"(music stem {snd['music_rms_db_speech']} dB RMS during speech, "
          f"{snd['music_rms_db_pauses']} dB in pauses; voice {snd['voice_rms_db_speech']} dB).")
        w(f"- Bed sits **{snd['music_db_under_voice_rest']} dB under the voice** at rest "
          f"(swells excluded); median over all speech {snd['music_db_under_voice_median']} dB, "
          f"IQR {snd['music_db_under_voice_p25_p75']}; at rest by thirds of the video "
          f"{snd['music_db_under_voice_by_third']}.")
        w(f"- Swells (≥ +4 dB over the bed): {fmt_ranges(snd['music_swells'])}.")
        w(f"- Drops (≤ −6 dB): {fmt_ranges(snd['music_drops'])}.")
        if snd.get("music_spectral_share"):
            sp = snd["music_spectral_share"]
            w(f"- Music spectrum (energy share): sub 20-80 Hz {sp['sub_20_80']}, low 80-250 {sp['low_80_250']}, "
              f"mid 250-2k {sp['mid_250_2k']}, high 2-8k {sp['high_2k_8k']}.")
        tr = snd.get("transients_no_vocals")
        if tr:
            w(f"- Transients on the no-vocals stem [M~]: {len(tr['isolated'])} isolated "
              f"(**{tr['isolated_per_min']}/min**, SFX candidates) at "
              f"{', '.join(f'{t:.1f}' for t in tr['isolated'])}; plus {len(tr['pulse'])} in "
              f"periodic runs (the music's own pulse). {tr['cuts_with_transient']} of "
              f"{rh['cuts']} cuts carry a transient within ±0.2 s.")
    lo = a.get("loudness") or {}
    w(f"- Loudness: {lo.get('integrated_lufs')} LUFS integrated, LRA {lo.get('lra_lu')} LU, "
      f"true peak {lo.get('true_peak_dbfs')} dBFS.")
    w("")
    w(TODO + "Music character (genre, tempo feel, melodic or bed-like) — listen to "
      "`stems/no_vocals.wav` if you can, otherwise infer from the spectrum + swells. "
      "Which transients are whooshes / hits / risers / clicks, and on what kind of beat? "
      "Is the voice gated between words?\n")

    w("## 8. Look [M + E]\n")
    if look.get("profile"):
        pr = look["profile"]
        w(f"- Brightness mean {pr['luma_mean']} (p10-p90 {pr['luma_p10_p90']}), saturation mean "
          f"{pr['sat_mean']} (p10-p90 {pr['sat_p10_p90']}), contrast {pr['contrast_p5_p95_mean']} ({pr['scale']}).")
    pal = look.get("palette") or {}
    if pal:
        w("- Dominant (by pixel share): " + ", ".join(f"{e['hex']} {e['share'] * 100:.0f}%"
                                                       f"{' ' + '/'.join(e['tags']) if e['tags'] else ''}"
                                                       for e in pal["dominant"]))
        w("- Accents (hue clusters of bright saturated pixels — graphics, wardrobe, sky; "
          "skin tagged): " +
          (", ".join(f"{e['hex']} ({e.get('share_of_saturated', e.get('share', 0)) * 100:.0f}%"
                     f"{' skin-ish' if 'skin-ish' in e.get('tags', []) else ''})"
                     for e in pal["accents"]) or "none"))
    w("")
    w(TODO + "Footage look (light, lens, depth of field, grade) and the GRAPHIC palette as hex "
      "(read it off the designed-moment frames, the measured accents are a starting point). "
      "Brand colours from a client logo always win over this palette.\n")

    w("## 9. How to apply [E]\n")
    w(TODO + "Keep the LANGUAGE (rhythm, caption treatment, kinetic type, motion quality, "
      "sound design). Invent NEW moments from the new script, with a light comic wink where "
      "it fits. List the concrete config values → `style/style.json` "
      "(start from `style/style.draft.json`).\n")
    open(os.path.join(out_dir, "REPORT.md"), "w", encoding="utf-8").write("\n".join(L))


# ======================================================================== draft
def draft_from(a):
    """The measured half of style.json. Everything [E] is null and listed in _todo."""
    rh, cap, snd = a["rhythm"], a.get("captions") or {}, a.get("sound") or {}
    tr = snd.get("transients_no_vocals") or {}
    pal = ((a.get("look") or {}).get("palette") or {})
    size = None
    if cap.get("glyph_height_px_1920"):
        gh = cap["glyph_height_px_1920"]
        size = int(round((gh - 46) if cap.get("style_guess") == "plate" else gh / 0.62))
    d = {
        "_about": "Draft from analyze_reference.py. Fill every null, delete _todo, "
                  "save as style/style.json, then run scripts/apply_style.py.",
        "_todo": ["captions.max_words", "captions.weight", "captions.style (verify guess)",
                  "brand.caption_size (verify)", "style.broll_share", "style.headline",
                  "style.transitions", "style.hook_notes", "style.notes",
                  "style.sfx_per_min (measured value is an UPPER bound — music hits count too)",
                  "audio.music_character", "style.palette (verify)"],
        "meta": {"references": [], "measured": True},
        "captions": {"style": cap.get("style_guess"), "max_words": None, "weight": None,
                     "center_y": cap.get("center_y_1920")},
        "brand": {"caption_size": size},
        "style": {"target_cuts_per_30s": rh.get("cuts_per_30s"),
                  "median_shot_s": rh.get("shot_median_s"),
                  "broll_share": None, "headline": None, "transitions": None,
                  "hook_notes": None, "sfx_per_min": tr.get("isolated_per_min"),
                  "notes": [], "palette": [e["hex"] for e in pal.get("accents", [])
                                           if "skin-ish" not in e.get("tags", [])][:5]},
        "audio": {"music_db_under_voice": snd.get("music_db_under_voice_rest")
                  if snd.get("music_present") else None,
                  "music_character": None},
    }
    return d


# ======================================================================= one ref
def analyse(src, out_dir, args):
    os.makedirs(out_dir, exist_ok=True)
    work = tempfile.mkdtemp(prefix="refan_")
    log(f"\n== {src}\n  → {out_dir}")
    info = probe_video(src)
    log(f"  probe: {info['width']}x{info['height']} @{info['fps']:g} fps, {info['duration']:.1f} s")
    if not info["is_9x16"]:
        log("  note: not 9:16 — grid overlays letterbox it; caption % are of THIS frame")
    scores = scene_scores(src, work)
    hard, soft = detect_cuts(scores, args.threshold, args.min_gap)
    cuts = [round(t, 3) for t, _ in hard]
    shots, stats = shot_stats(cuts, info["duration"])
    log(f"  cuts: {stats['cuts']} ({stats['cuts_per_30s']}/30 s), median shot {stats['shot_median_s']} s")
    a = {"source": os.path.abspath(src), "probe": info,
         "cuts": {"threshold": args.threshold, "min_gap_s": args.min_gap,
                  "times": cuts, "scores": [r(s, 1) for _, s in hard],
                  "soft": [(r(t), r(s, 1)) for t, s in soft]},
         "rhythm": stats, "shots": shots}
    if info["has_audio"]:
        a["loudness"] = loudness(src)
        a["sound"], series = sound(src, out_dir, not args.no_stems, cuts)
        if series:
            jdump(series, os.path.join(out_dir, "audio_series.json"))
        s = a["sound"]
        if s.get("music_present") is not None:
            log(f"  sound: music {'present' if s['music_present'] else 'absent'}, "
                f"{s['music_db_under_voice_rest']} dB under voice at rest")
    else:
        a["sound"] = {"method": "no audio stream"}
    fr = decode_lowres(src, info) if np is not None else None
    cap = caption_estimate(fr, 4) if fr is not None else None
    if cap:
        log(f"  captions: centre {cap['center_pct']}% ({cap['confidence']}), guess {cap['style_guess']}")
    a["captions"] = {k: v for k, v in (cap or {}).items() if not k.startswith("_")} or None
    prof, outl = brightness(fr, shots, 4)
    a["look"] = {"profile": prof, "outlier_shots": outl, "palette": palette(src, info, fr, work)}
    if (args.transcript or args.transcribe) and not info["has_audio"]:
        log("  transcribe: SKIPPED — no audio stream")
    elif args.transcript or args.transcribe:
        js = args.transcript
        if not js:
            sd = os.path.join(out_dir, "stems")
            wav = os.path.join(sd, "vocals.wav")          # cleaner than the mix
            if not os.path.exists(wav):
                wav = os.path.join(sd, "mix.wav")
            if not os.path.exists(wav):
                os.makedirs(sd, exist_ok=True)
                hfcfg.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000", wav])
            js = transcribe(wav, os.path.join(out_dir, "words.json"), args.config)
        if js:
            a["words"] = words_stats(js, info["duration"], (a.get("sound") or {}).get("speech_share"))
    log("  sheets …")
    made = make_sheets(src, info, shots, cuts, cap, out_dir, args.strips)
    a["images"] = {k: ([os.path.relpath(x["file"] if isinstance(x, dict) else x, out_dir) for x in v]
                       if isinstance(v, list) else (os.path.relpath(v, out_dir) if v else None))
                   for k, v in made.items()}
    jdump(a, os.path.join(out_dir, "analysis.json"), indent=1)
    report(os.path.basename(out_dir), src, a, made, out_dir)
    shutil.rmtree(work, ignore_errors=True)
    log(f"  wrote analysis.json, REPORT.md, {sum(len(v) if isinstance(v, list) else 1 for v in made.values() if v)} image(s)")
    return a


# ====================================================================== combine
def flatten(d, pre=""):
    out = {}
    for k, v in d.items():
        if k.startswith("_") or k in ("shots", "images", "source", "all", "times", "scores", "soft",
                                      "probe", "method", "confidence", "peak_to_median", "scale"):
            continue
        key = f"{pre}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        elif isinstance(v, bool) or isinstance(v, (int, float)) or isinstance(v, str):
            out[key] = v
    return out


def combine(analyses, names):
    flats = [flatten(a) for a in analyses]
    keys = sorted(set().union(*flats))
    med, disagree = {}, {}
    for k in keys:
        vals = [f.get(k) for f in flats]
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if nums and len(nums) == len([v for v in vals if v is not None]):
            med[k] = r(statistics.median(nums), 3)
            lo, hi = min(nums), max(nums)
            spread = (hi - lo) / (abs(statistics.median(nums)) + 1e-9)
            if len(nums) > 1 and spread > 0.30 and (hi - lo) > 0.05:
                disagree[k] = dict(zip(names, vals))
        else:
            present = [v for v in vals if v is not None]
            if present and len(set(map(str, present))) > 1:
                disagree[k] = dict(zip(names, vals))
            elif present:
                med[k] = present[0]
    return {"references": names, "median": med, "disagree": disagree,
            "note": "medians of numeric fields; 'disagree' = spread > 30 % of the median or "
                    "differing labels. Decide those by eye and say which reference wins in STYLE.md."}


def main():
    ap = hfcfg.arg_parser(__doc__.split("\n\n")[0])
    ap.add_argument("refs", nargs="+", help="reference video(s)")
    ap.add_argument("--out", default="style", help="output folder (default style/)")
    ap.add_argument("--no-stems", action="store_true", help="skip Demucs (music NOT measured)")
    ap.add_argument("--transcribe", action="store_true", help="words/s via scripts/transcribe.py")
    ap.add_argument("--transcript", help="existing word-timestamp json instead of transcribing")
    ap.add_argument("--threshold", type=float, default=8.0, help="scdet cut score (default 8)")
    ap.add_argument("--min-gap", type=float, default=0.30, help="merge cuts closer than this (s)")
    ap.add_argument("--strips", type=int, default=6, help="transition strips for the first N cuts")
    args = ap.parse_args()
    hfcfg.require("ffmpeg", "ffprobe")
    if np is None:
        log("note: numpy missing — sound, palette ranking and the caption heuristic are skipped "
            "(pip install numpy)")
    if Image is None:
        log("note: Pillow missing — sheets are unlabelled (pip install pillow)")
    os.makedirs(args.out, exist_ok=True)
    analyses, names = [], []
    for src in args.refs:
        if not os.path.exists(src):
            sys.exit(f"not found: {src}")
        name = re.sub(r"[^\w.-]+", "_", os.path.splitext(os.path.basename(src))[0]) or "ref"
        base, k = name, 2
        while name in names:
            name, k = f"{base}_{k}", k + 1
        names.append(name)
        analyses.append(analyse(src, os.path.join(args.out, name), args))
    draft = draft_from(analyses[0])
    if len(analyses) > 1:
        comb = combine(analyses, names)
        jdump(comb, os.path.join(args.out, "combined.json"), indent=1)
        m = comb["median"]
        for path, key in (("style.target_cuts_per_30s", "rhythm.cuts_per_30s"),
                          ("style.median_shot_s", "rhythm.shot_median_s"),
                          ("captions.center_y", "captions.center_y_1920"),
                          ("audio.music_db_under_voice", "sound.music_db_under_voice_rest"),
                          ("style.sfx_per_min", "sound.transients_no_vocals.isolated_per_min")):
            if key in m:
                sec, fld = path.split(".")
                draft[sec][fld] = int(m[key]) if fld == "center_y" else m[key]
        log(f"\n  combined.json: {len(comb['disagree'])} field(s) disagree across references")
    draft["meta"]["references"] = names
    jdump(draft, os.path.join(args.out, "style.draft.json"), indent=2)
    log(f"\nnext: READ the images listed in {args.out}/<ref>/REPORT.md §0, fill the TODOs, write "
        f"{args.out}/STYLE.md and {args.out}/style.json (from style.draft.json), then\n"
        f"      python3 scripts/apply_style.py --style {args.out}/style.json --dry-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
