#!/usr/bin/env python3
"""Composite the caption layer onto the render and write the SDR master.

Two jobs:
  1. overlay assets/captions.webm (VP9 + alpha) onto the HyperFrames render
  2. force the master to 8-bit bt709 SDR H.264 — and ASSERT it

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


def master_chain(src, target, ceiling=-1.5):
    """The house master: a GENTLE compressor first, then gain to the target, then a
    limiter with its auto-makeup OFF (level=disabled — with makeup on, alimiter puts the
    peak straight back). Loudness-normalising in dynamic mode pumps; this does not.
    Reels normalise to about -14 LUFS, so a -22 LUFS master just plays quieter than the
    video before it. Two measured passes, because the limiter shaves a little."""
    comp = "acompressor=threshold=-24dB:ratio=2.5:attack=8:release=180:makeup=1"
    lim = f"alimiter=limit={10 ** (ceiling / 20):.4f}:level=disabled:attack=5:release=60"
    i0, _ = loudness(src, comp)
    if i0 is None:
        return None, None, None
    gain = target - i0
    for _ in range(2):
        chain = f"{comp},volume={gain:.2f}dB,{lim}"
        i1, tp = loudness(src, chain)
        if i1 is None or abs(i1 - target) <= 0.3:
            break
        gain += target - i1
    return f"{comp},volume={gain:.2f}dB,{lim}", i1, tp


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
    a = ap.parse_args()
    cfg = hfcfg.load(a.config)
    hfcfg.require("ffmpeg", "ffprobe")

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
        target = float(cfg["render"].get("target_lufs", -14.0))
        before, _ = loudness(base, "")
        chain, after, tp = master_chain(base, target)
        if chain:
            acodec = ["-af", chain, "-c:a", "aac", "-b:a", "320k", "-ar", "48000"]
            print(f"  loudness {before:.1f} → {after:.1f} LUFS (target {target:.0f}), "
                  f"true peak {tp:.1f} dBTP")

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
