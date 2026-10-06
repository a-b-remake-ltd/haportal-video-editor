#!/usr/bin/env python3
"""Composite the caption layer onto the render and write the SDR master.

Three jobs:
  1. overlay assets/captions.webm (VP9 + alpha) onto the HyperFrames render
  2. force the master to 8-bit bt709 SDR H.264 — and ASSERT it
  3. master the audio to −14 LUFS ± 0.4, true peak ≈ −1.3 dBTP, AAC 320k — measured on
     the encoded file and iterated (references/sound.md §Mastering). A quiet, peaky
     AI-avatar render gets pre-gain + compression before the limiter; a normal voice a
     gentle chain. `--audio-only mix.wav` runs (3) alone, to test a mix without a render.

(2) matters because phone B-roll is HLG/bt2020, and when any HDR media is in the
composition HyperFrames renders HEVC 10-bit bt2020/HLG. Every player then applies an
HDR→SDR transform and the whole video looks washed out. The B-roll is tone-mapped at
build time (prep_broll.py) and this pass pins the container tags so nothing can
reintroduce it.
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

HDR_TAGS = ("bt2020", "arib-std-b67", "smpte2084")


def loudness(src, chain):
    """Integrated loudness (LUFS) and true peak (dBTP) of src's audio through `chain`.
    Never pass -v error: it suppresses the ebur128 summary."""
    af = (chain + "," if chain else "") + "ebur128=peak=true"
    out = hfcfg.run(["ffmpeg", "-nostdin", "-i", src, "-vn", "-af", af, "-f", "null", "-"]).stderr
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", out)
    tp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", out)
    return (float(i[-1]) if i else None), (float(tp[-1]) if tp else None)


# ------------------------------------------------------------------ the master (§8)
# Two chains, picked by how loud the render comes in:
#   QUIET / PEAKY (≈ −30 LUFS or below — AI-avatar voices often arrive near −36 LUFS with
#   a −40 dB mean): pre-gain, then a compressor that tames the peaks BEFORE the limiter.
#   Without it the limiter clamps the voice peaks and the master sticks near −16 LUFS no
#   matter how much gain goes in (measured: −15.9 with the old one-stage chain).
#   NORMAL (≈ −20 LUFS): a gentle compressor, no pre-gain.
# Then gain to a pre-limiter target, a 4× oversampled (192 kHz) limiter at 0.84 (−1.5 dBFS,
# true peak ≈ −1.3) with auto-level OFF (level=true puts the peak straight back), AAC 320k.
# The pre-limiter target starts at −12.8 (quiet) / −13.4 (normal) — the limiter shaves
# about a dB — and is iterated on the MEASURED master until −14 ± 0.4 LUFS.
QUIET_BELOW = -25.0          # integrated LUFS of the render: at or below → the quiet chain
LIMITER = "alimiter=limit=0.84:attack=2:release=60:level=false"


def comp_chain(i0):
    """(name, compressor chain, starting pre-limiter offset vs the target) for a render
    measuring i0 LUFS. The quiet chain's pre-gain is 20 dB for the reference (−36 LUFS)
    and scales down for less quiet input, so the compressor always sees ~−16 LUFS."""
    if i0 is not None and i0 <= QUIET_BELOW:
        pre = max(0.0, min(20.0, round(-16.0 - i0)))
        return (f"quiet ({i0:.1f} LUFS → +{pre:.0f} dB pre-gain + compressor)",
                f"volume={pre:.0f}dB,acompressor=threshold=0.06:ratio=3:attack=4:release=140:makeup=1",
                1.2)
    return (f"normal ({i0:.1f} LUFS → gentle compressor)",
            "acompressor=threshold=0.08:ratio=2:attack=4:release=140:makeup=1", 0.6)


def master_audio(src, out, target=-14.0, tol=0.4, work="build"):
    """Master src's audio to an AAC 320k file at `target` LUFS. Returns a dict with the
    chain, the measured loudness and true peak of the ENCODED file, and the passes."""
    os.makedirs(work, exist_ok=True)
    i0, tp0 = loudness(src, "")
    if i0 is None:
        return None
    name, comp, lead = comp_chain(i0)
    c = os.path.join(work, "master_c.wav")
    # float intermediate: the pre-gained signal can exceed 0 dBFS before the limiter, and a
    # fixed-point wav would clip it right there
    r = hfcfg.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-af", comp,
                   "-c:a", "pcm_f32le", c])
    if r.returncode:
        sys.exit(f"compressor pass failed:\n{r.stderr}")
    ic, _ = loudness(c, "")
    pre_target = target + lead
    passes = []
    for _ in range(5):
        gain = pre_target - ic
        chain = f"aresample=192000,volume={gain:.2f}dB,{LIMITER},aresample=48000"
        r = hfcfg.run(["ffmpeg", "-v", "error", "-y", "-i", c, "-af", chain,
                       "-c:a", "aac", "-b:a", "320k", "-ar", "48000", out])
        if r.returncode:
            sys.exit(f"master encode failed:\n{r.stderr}")
        im, tp = loudness(out, "")          # measure the ENCODED master, not the chain
        passes.append((round(pre_target, 2), im, tp))
        if im is None or abs(im - target) <= min(tol, 0.15):
            break
        pre_target += target - im
    ok = im is not None and abs(im - target) <= tol
    return {"input_lufs": i0, "input_tp": tp0, "chain_name": name, "comp": comp,
            "limiter": chain, "lufs": im, "tp": tp, "passes": passes, "ok": ok}


def report_master(m, target):
    print(f"  master: render {m['input_lufs']:.1f} LUFS / {m['input_tp']:.1f} dBTP, "
          f"chain {m['chain_name']}")
    for i, (pt, im, tp) in enumerate(m["passes"], 1):
        print(f"    pass {i}: pre-limiter {pt:.2f} → {im:.1f} LUFS, true peak {tp:.1f} dBTP")
    mark = "✓" if m["ok"] else "✗ OUTSIDE ±0.4"
    print(f"  {mark} {m['lufs']:.1f} LUFS (target {target:.0f}), true peak {m['tp']:.1f} dBTP, AAC 320k")


def newest_render(d="renders"):
    c = [f for f in glob.glob(os.path.join(d, "*.mp4"))
         if os.path.basename(f) not in ("final.mp4", "base.mp4", "proof.mp4")]
    if not c:
        sys.exit(f"no HyperFrames render found in {d}/")
    return max(c, key=os.path.getmtime)


def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("base", nargs="?", help="the HyperFrames render (default: newest in renders/)")
    ap.add_argument("--captions", default="assets/captions.webm")
    ap.add_argument("--out", default="renders/final.mp4")
    ap.add_argument("--outro", default="build/outro.json",
                    help="outro timing from build_index.py (captions stop at its start)")
    ap.add_argument("--no-loudness", action="store_true",
                    help="copy the render's audio untouched (skip the -14 LUFS master)")
    ap.add_argument("--no-captions", action="store_true",
                    help="captions already live in the composition (verified non-ghosting)")
    ap.add_argument("--audio-only", metavar="MIX",
                    help="master just this file's audio to --out (.m4a): test a mix "
                         "without a render")
    a = ap.parse_args()
    cfg = hfcfg.load(a.config)
    hfcfg.require("ffmpeg", "ffprobe")
    target = float(cfg["render"].get("target_lufs", -14.0))

    if a.audio_only:
        out = a.out if a.out.endswith((".m4a", ".mp4", ".aac")) and a.out != "renders/final.mp4" \
            else os.path.splitext(a.audio_only)[0] + "_master.m4a"
        m = master_audio(a.audio_only, out, target)
        if not m:
            sys.exit("no audio measured")
        report_master(m, target)
        print(f"  → {out}")
        return 0 if m["ok"] else 1

    base = a.base or newest_render()
    br = cfg["render"]["video_bitrate"]
    print(f"  base: {base}")

    cmd = ["ffmpeg", "-v", "error", "-y", "-i", base]
    if not a.no_captions:
        if not os.path.exists(a.captions):
            sys.exit(f"{a.captions} not found — run scripts/caption_layer.py "
                     f"(or pass --no-captions)")
        # The caption layer ends when the speech does. overlay's default eof_action=repeat
        # would hold its LAST card over everything after it (an outro, a tail), so pass the
        # base through once the layer ends — and with an outro, switch the layer off at the
        # outro's start (build/outro.json, written by build_index.py) so no caption sits on
        # the shrinking speaker circle. The output keeps the base's length (shortest=0).
        ov = "overlay=0:0:format=auto:eof_action=pass"
        if os.path.exists(a.outro):
            o = json.load(open(a.outro, encoding="utf-8"))
            ov += f":enable='lt(t,{float(o['start']):.3f})'"
            print(f"  outro: captions off from {float(o['start']):.2f}s "
                  f"({o.get('style')}, composition ends {float(o['end']):.2f}s)")
        cmd += ["-c:v", "libvpx-vp9", "-i", a.captions,
                "-filter_complex",
                "[0:v]format=yuv420p[b];[1:v]format=yuva420p[c];"
                f"[b][c]{ov},format=yuv420p[v]",
                "-map", "[v]", "-map", "0:a"]
    else:
        cmd += ["-vf", "format=yuv420p", "-map", "0:v", "-map", "0:a"]

    acodec = ["-c:a", "copy"]
    if not a.no_loudness:
        # Master the audio on its own first (cheap to iterate: no video re-encode), then mux
        # the measured AAC into the video untouched.
        m = master_audio(base, "build/master_audio.m4a", target)
        if m:
            report_master(m, target)
            ai = cmd.count("-i")                # index of the next input
            last = max(i for i, x in enumerate(cmd) if x == "-i") + 2
            cmd[last:last] = ["-i", "build/master_audio.m4a"]   # inputs before output options
            amap = f"{ai}:a"
            cmd = [amap if x == "0:a" else x for x in cmd]
            acodec = ["-c:a", "copy"]

    maxrate = str(int(float(br.rstrip("Mm")) * 1.125)) + "M"
    bufsize = str(int(float(br.rstrip("Mm")) * 2)) + "M"
    cmd += ["-c:v", "libx264", "-preset", "slow", "-b:v", br,
            "-maxrate", maxrate, "-bufsize", bufsize, "-pix_fmt", "yuv420p",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-color_range", "tv", *acodec, "-movflags", "+faststart", a.out]

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    r = hfcfg.run(cmd)
    if r.returncode:
        sys.exit(f"master encode failed:\n{r.stderr}")

    q = hfcfg.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                   "-show_entries",
                   "stream=codec_name,pix_fmt,color_space,color_transfer,color_primaries",
                   "-show_entries", "format=duration,bit_rate",
                   "-of", "default=noprint_wrappers=1", a.out]).stdout
    print("\n" + q.strip())
    bd = float(hfcfg.probe(base, "format=duration") or 0)
    od = float(hfcfg.probe(a.out, "format=duration") or 0)
    if bd and abs(od - bd) > 0.08:
        sys.exit(f"\n  ✗ MASTER IS {od:.2f}s BUT THE RENDER IS {bd:.2f}s — the overlay truncated it")

    bad = [k for k in HDR_TAGS if k in q]
    if bad:
        sys.exit(f"\n  ✗ MASTER IS STILL HDR-TAGGED: {bad}")

    vol = hfcfg.run(["ffmpeg", "-nostdin", "-i", a.out, "-af", "volumedetect",
                     "-f", "null", "-"]).stderr
    m = re.search(r"max_volume: ([-0-9.]+) dB", vol)
    peak = float(m.group(1)) if m else None
    bit = re.search(r"bit_rate=(\d+)", q)
    mbps = int(bit.group(1)) / 1e6 if bit else 0

    print(f"\n  ✓ SDR bt709 — {a.out}")
    print(f"  bitrate {mbps:.1f} Mbps"
          f"{'' if mbps >= 28 else '   ← LOW, render with --video-bitrate ' + br}")
    if peak is not None:
        ok = -3.0 <= peak <= -0.6
        print(f"  audio peak {peak:+.1f} dBFS"
              f"{'' if ok else '   ← target -1 to -2 dB'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
