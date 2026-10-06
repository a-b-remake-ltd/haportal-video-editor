#!/usr/bin/env python3
"""Filmstrip + waveform + word labels for one time range, as ONE PNG you can look at.

    python3 scripts/timeline_view.py raw.mp4 12.0 18.5
    python3 scripts/timeline_view.py renders/final.mp4 21.3 24.3 --marks 22.8 -o verify/cut07.png
    python3 scripts/timeline_view.py raw.mp4 12 18.5 --transcript src/transcripts/raw.json

WHY: Claude cannot watch a video or hear it. This turns a span into one image that shows
what matters at a decision point: N frames across the span (jump cuts, flashes, a caption
hidden behind an overlay), the audio envelope in dBFS with the -26 dB speech gate and the
-33 dB silence line drawn in (where speech really starts — references/cutting.md), real
silences shaded (measured with silencedetect, not guessed from Whisper's stretched word
stamps) and every word at its time.

Use it at DECISION POINTS, not as a scan loop: on SOURCES to settle an ambiguous take or
pause, and on the RENDERED output at every cut boundary (±1.5 s, `--marks <boundary>`) plus
the first and last 2 s during self-evaluation (references/workflow.md).

Hebrew labels render right-to-left: PIL without libraqm draws characters in logical
order, so each label is reordered for display — python-bidi when installed, otherwise a
built-in run reorder that is exact for Hebrew words and Hebrew+Latin/number tokens such
as "ב-AI". The font is a Hebrew-capable face from the skill's assets/fonts (Heebo after
setup), then the system's.

Needs numpy + pillow (`python3 scripts/doctor.py --install`); re-runs itself under the
skill venv when the system python lacks them.

Ported from browser-use/video-use helpers/timeline_view.py (MIT, Copyright (c) the
video-use authors); adapted: dBFS envelope with gate lines, measured silences, staggered
RTL word labels, frame times, boundary marks, 9:16-aware frame size.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import tempfile
import unicodedata
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

BG = (18, 18, 22)
FG = (235, 235, 235)
DIM = (120, 120, 130)
WAVE = (140, 180, 255)
SILENCE = (60, 95, 150, 110)
GATE = (255, 140, 60)
MARK = (255, 70, 90)
LOWP = (255, 200, 90)
DB_FLOOR = -60.0

HEBREW = re.compile(r"[\u0590-\u05FF\uFB1D-\uFB4F]")


# ================================================================== RTL text

def _strong(ch):
    """'R' for right-to-left letters, 'L' for left-to-right letters and digits, None for
    neutrals (spaces, punctuation, hyphen, apostrophes)."""
    bd = unicodedata.bidirectional(ch)
    if bd in ("R", "AL"):
        return "R"
    if bd in ("L", "EN", "AN"):
        return "L"
    return None


def visual_rtl(text):
    """Logical → visual order for a short RTL-context label.

    python-bidi does the full Unicode algorithm when it is installed. The fallback splits
    the string into runs of the same direction (a neutral joins an LTR run only when LTR
    sits on both sides of it — "GPT-5" stays one run, "ב-AI" splits into "ב-" + "AI"),
    lays the runs out right-to-left and mirrors only the RTL runs. That is exact for
    everything a word label holds: a Hebrew word, a prefixed Latin token, a number.
    """
    if not HEBREW.search(text):
        return text
    try:
        from bidi.algorithm import get_display
        return get_display(text, base_dir="R")
    except Exception:
        pass
    dirs = [_strong(c) for c in text]
    n = len(text)
    for i in range(n):                      # resolve neutrals
        if dirs[i] is None:
            left = next((dirs[j] for j in range(i - 1, -1, -1) if dirs[j]), None)
            right = next((dirs[j] for j in range(i + 1, n) if dirs[j]), None)
            dirs[i] = "L" if (left == "L" and right == "L") else "R"
    runs, cur, cd = [], "", None
    for c, d in zip(text, dirs):
        if d != cd and cur:
            runs.append((cd, cur))
            cur = ""
        cur, cd = cur + c, d
    if cur:
        runs.append((cd, cur))
    mirror = {"(": ")", ")": "(", "[": "]", "]": "[", "<": ">", ">": "<"}
    out = []
    for d, r in reversed(runs):
        out.append("".join(mirror.get(c, c) for c in reversed(r)) if d == "R" else r)
    return "".join(out)


# ===================================================================== fonts

def _has_hebrew(font):
    try:
        a, b = font.getmask("א"), font.getmask("\uffff")      # \uffff → .notdef box
        return a.size != b.size or bytes(a) != bytes(b)
    except Exception:
        return False


def load_font(size, bold=False):
    """A Hebrew-capable face: the skill's assets/fonts first (Heebo/Rubik after setup,
    or the user's brand face), then common system faces."""
    from PIL import ImageFont
    fdir = os.path.join(hfcfg.SKILL_DIR, "assets", "fonts")
    own = sorted(glob.glob(os.path.join(fdir, "*.ttf")) + glob.glob(os.path.join(fdir, "*.otf")),
                 key=lambda p: (0 if "heebo" in p.lower() else 1 if "rubik" in p.lower() else 2, p))
    system = ["/System/Library/Fonts/SFHebrew.ttf", "/System/Library/Fonts/ArialHB.ttc",
              "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/noto/NotoSansHebrew-Regular.ttf",
              "C:/Windows/Fonts/arial.ttf"]
    for fp in own + system:
        if not os.path.exists(fp):
            continue
        try:
            f = ImageFont.truetype(fp, size)
            if bold and "heebo" in fp.lower():
                try:
                    f.set_variation_by_axes([700])
                except Exception:
                    pass
            if _has_hebrew(f):
                return f
        except Exception:
            continue
    print("  ! no Hebrew-capable font found — run scripts/setup_assets.py", file=sys.stderr)
    return ImageFont.load_default()


# ================================================================ extraction

def extract_frames(video, times, dest, height):
    paths = []
    for i, t in enumerate(times):
        out = os.path.join(dest, f"f{i:03d}.jpg")
        hfcfg.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{max(t, 0):.3f}",
                   "-i", video, "-frames:v", "1", "-q:v", "3", "-vf", f"scale=-2:{height}", out])
        paths.append(out if os.path.exists(out) else None)
    return paths


def envelope_db(video, start, end, samples):
    """Windowed RMS of mono 16 kHz audio, in dBFS, `samples` long. Absolute dB (not
    normalised to the loudest point) so the -26 dB speech gate means the same thing on
    every image."""
    import numpy as np
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "a.wav")
        r = hfcfg.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{start:.3f}",
                       "-i", video, "-t", f"{end - start:.3f}", "-vn", "-ac", "1",
                       "-ar", "16000", "-c:a", "pcm_s16le", wav])
        if r.returncode or not os.path.exists(wav):
            return np.full(samples, DB_FLOOR)
        with wave.open(wav, "rb") as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if pcm.size == 0:
        return np.full(samples, DB_FLOOR)
    x = pcm.astype(np.float32) / 32768.0
    win = max(1, x.size // samples)
    x = x[: (x.size // win) * win].reshape(-1, win)
    rms = np.sqrt(np.mean(x ** 2, axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-6))
    if db.size < samples:
        db = np.pad(db, (0, samples - db.size), constant_values=DB_FLOOR)
    return np.clip(db[:samples], DB_FLOOR, 0.0)


def find_transcript(video):
    """src/transcripts/<stem>.json, else src/aroll.json — if it was made from this file."""
    stem = os.path.splitext(os.path.basename(video))[0]
    for cand in (os.path.join("src", "transcripts", stem + ".json"),
                 os.path.join("src", "aroll.json")):
        if not os.path.exists(cand):
            continue
        try:
            src = json.load(open(cand, encoding="utf-8")).get("source", "")
        except (ValueError, AttributeError):
            continue
        if src and os.path.basename(src) == os.path.basename(video):
            return cand
    return None


# ================================================================= composite

def render(video, start, end, out, n_frames=10, transcript=None, marks=(), silence=0.3,
           noise=-33.0):
    from PIL import Image, ImageDraw
    import pack_transcript as pk

    dur = end - start
    w_, h_ = (hfcfg.probe(video, "stream=width,height", "v:0") or "16,9").split(",")[:2]
    portrait = int(h_) > int(w_)
    frame_h = 300 if portrait else 200
    times = [start + dur * (i + 0.5) / n_frames for i in range(n_frames)]

    toks = []
    if transcript:
        got = pk.load_tokens(transcript)
        if got:
            toks = [t for t in got[1] if t["end"] > start and t["start"] < end]
    sil = pk.measured_silences(video, silence, [start, end], noise) or []

    with tempfile.TemporaryDirectory() as tmp:
        frames = [Image.open(p).convert("RGB") if p else None
                  for p in extract_frames(video, times, tmp, frame_h)]
    fw = max((f.width for f in frames if f), default=int(frame_h * 16 / 9))
    gap = 6
    strip_w = n_frames * fw + (n_frames - 1) * gap
    W = max(1500, strip_w + 100)
    scale = min(1.0, (W - 100) / strip_w)
    fw_s, fh_s = int(fw * scale), int(frame_h * scale)

    strip_y = 56
    label_rows, row_h = 3, 24
    labels_y = strip_y + fh_s + 36
    wave_y = labels_y + label_rows * row_h + 8
    wave_h = 230
    ruler_y = wave_y + wave_h
    H = ruler_y + 70
    canvas = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(canvas, "RGBA")
    f_head, f_lab, f_small = load_font(22, True), load_font(17), load_font(13)

    x0 = 50
    x1 = x0 + n_frames * fw_s + (n_frames - 1) * gap

    def tx(t):
        return int(x0 + (t - start) / max(dur, 1e-6) * (x1 - x0))

    title = (f"{visual_rtl(os.path.basename(video))}   {start:.2f}s - {end:.2f}s   "
             f"({dur:.2f}s, {n_frames} frames)")
    d.text((x0, 16), title, fill=FG, font=f_head)

    # filmstrip, each frame captioned with its time
    for i, f in enumerate(frames):
        fx = x0 + i * (fw_s + gap)
        if f:
            canvas.paste(f.resize((fw_s, fh_s)), (fx, strip_y))
        else:
            d.rectangle((fx, strip_y, fx + fw_s, strip_y + fh_s), outline=DIM)
        d.text((fx + 4, strip_y + fh_s + 4), f"{times[i]:.2f}", fill=DIM, font=f_small)

    # waveform panel: silences, dB envelope, gate lines
    d.rectangle((x0, wave_y, x1, ruler_y), fill=(28, 28, 34))
    for a, b in sil:
        d.rectangle((tx(max(a, start)), wave_y, tx(min(b, end)), ruler_y), fill=SILENCE)

    def ty(db):
        return int(ruler_y - (db - DB_FLOOR) / -DB_FLOOR * (wave_h - 6))

    env = envelope_db(video, start, end, max(200, x1 - x0))
    pts = [(x0 + i, ty(v)) for i, v in enumerate(env)]
    d.polygon(pts + [(x1, ruler_y), (x0, ruler_y)], fill=(*WAVE, 90))
    d.line(pts, fill=WAVE, width=1)
    for db, col, name in ((-26.0, GATE, "-26 dB speech"), (-33.0, DIM, "-33 dB silence")):
        y = ty(db)
        for xx in range(x0, x1, 14):
            d.line((xx, y, min(xx + 7, x1), y), fill=col, width=1)
        d.text((x1 + 4, y - 8), name.split()[0], fill=col, font=f_small)

    # word labels, staggered over three rows so neighbours do not hide each other
    row_end = [-10 ** 9] * label_rows
    hidden = 0
    for t in toks:
        txt = t["text"] if t["kind"] == "word" else t["text"]
        if not txt:
            continue
        a, b = tx(max(t["start"], start)), tx(min(t["end"], end))
        d.line((a, wave_y, a, wave_y + 10), fill=DIM, width=1)
        lab = visual_rtl(txt)
        lw = d.textlength(lab, font=f_lab)
        cx = (a + b) / 2.0
        lx = int(min(max(cx - lw / 2, x0), x1 - lw))
        row = next((r for r in range(label_rows) if lx > row_end[r] + 6), None)
        if row is None:
            hidden += 1
            continue
        ly = labels_y + row * row_h
        col = LOWP if t.get("p", 1.0) < 0.5 else FG
        d.line((int(cx), ly + row_h - 4, int(cx), wave_y), fill=(*DIM, 90), width=1)
        d.text((lx, ly), lab, fill=col, font=f_lab)
        row_end[row] = lx + lw

    # boundary marks (cut points) across everything
    for m in marks:
        if start <= m <= end:
            xm = tx(m)
            d.line((xm, strip_y - 6, xm, ruler_y), fill=MARK, width=2)
            d.text((xm + 4, strip_y - 22), f"cut {m:.2f}", fill=MARK, font=f_small)

    # ruler + legend
    for i in range(7):
        t = start + dur * i / 6
        xi = tx(t)
        d.line((xi, ruler_y, xi, ruler_y + 6), fill=DIM, width=1)
        d.text((xi - 18, ruler_y + 9), f"{t:.2f}s", fill=DIM, font=f_small)
    legend = (f"blue bands = silences >= {silence:.2f}s measured at {noise:.0f} dB "
              f"({len(sil)})    orange dashes = -26 dB speech gate    "
              f"yellow word = low confidence")
    if hidden:
        legend += f"    ({hidden} word label(s) hidden for space)"
    if not toks:
        legend += "    (no transcript — pass --transcript for word labels)"
    d.text((x0, ruler_y + 36), legend, fill=DIM, font=f_small)

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    canvas.save(out, "PNG", optimize=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("video")
    ap.add_argument("start", type=float)
    ap.add_argument("end", type=float)
    ap.add_argument("-o", "--out", help="PNG path (default verify/<name>_<start>-<end>.png)")
    ap.add_argument("--n-frames", type=int, default=10)
    ap.add_argument("--transcript", help="transcript json (default: auto-find in src/)")
    ap.add_argument("--marks", default="", help="comma-separated times to draw as cut lines")
    ap.add_argument("--silence", type=float, default=0.3,
                    help="shade silences at least this long (s). Default 0.3")
    ap.add_argument("--noise", type=float, default=-33.0, help="silence level in dB")
    a = ap.parse_args()

    hfcfg.ensure_deps(["numpy", "PIL"])
    hfcfg.require("ffmpeg", "ffprobe")
    if not os.path.exists(a.video):
        sys.exit(f"not found: {a.video}")
    if a.end <= a.start:
        sys.exit("end must be after start")
    total = float(hfcfg.probe(a.video) or 0)
    start, end = max(0.0, a.start), min(a.end, total) if total else a.end
    tr = a.transcript or find_transcript(a.video)
    marks = [float(x) for x in a.marks.split(",") if x.strip()]
    stem = re.sub(r"[^\w\-]+", "_", os.path.splitext(os.path.basename(a.video))[0])
    out = a.out or os.path.join("verify", f"{stem}_{start:.2f}-{end:.2f}.png")
    render(a.video, start, end, out, a.n_frames, tr, marks, a.silence, a.noise)
    print(f"saved {out}" + (f"  (words from {tr})" if tr else "  (no transcript)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
