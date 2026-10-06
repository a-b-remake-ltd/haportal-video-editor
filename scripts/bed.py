#!/usr/bin/env python3
"""Build the music BED: the chosen track trimmed to its offset, shaped per story section
and calibrated against the RAW voice.  references/sound.md §The bed.

    python3 scripts/bed.py --init          # bed.json from music_plan.json + music_report.json
    python3 scripts/bed.py                 # → assets/bgm/bed.wav, per-section gaps printed
    python3 scripts/bed.py --apply         # …and write it into media.json audio.music

The result is ONE file covering the whole composition (0 → END, the outro tail included)
that plays at volume 1.0: every level decision is baked into it, measured, instead of
being spread over data-volume numbers that mean nothing without the track they were
tuned on.

bed.json (every key optional except sections; null = derived):
  music        "assets/bgm/music.mp3"           the untrimmed chosen track (music.py)
  offset       null → music_report.json          seconds trimmed off the track's start
  voice        null → media.json aroll, else assets/aroll.mp4   the RAW voice
  end          null → build/outro.json end, else music_report end, else the voice length
  outro        null → build/outro.json start, else music_report outro_start
  sections     [[name, start, end, target_gap_dB], ...]   voice-over-music, per section
  drops        [[a, b, ratio], ...]     dip to section gain × ratio (0.08-0.15) before a punchline
  swells       [[a, b, ratio], ...]     a lift to × ratio peaking mid-window (a graphic beat)
  iterations   6         gain_cap 6.0      start_gain 0.25
  eq           [[1800, -5, 1], [450, -3, 1]]   [freq Hz, gain dB, Q] — the voice pocket
  sidechain    {"key_db": -6, "threshold": 0.03, "ratio": 3, "attack": 15, "release": 260}
  end_fade     0.3       fade to 0 over the last seconds when there is NO outro

WHY each rule (each one is a bug that shipped once):
* Section keys sit at start+0.12 and end−0.12 — never two keys on one timestamp: tied
  keys make np.interp ramp across the WHOLE section instead of 0.24 s at the boundary.
* Drops are RELATIVE to the containing section (gain × ratio) with a 60 ms ramp in: a
  fixed drop level once RAISED the music under a quiet voice.
* The outro lift is relative to the last section (×2.0 at O+0.5, ×1.6 at O+2.2, ×0.6 at
  END−0.5, 0 at END): the voice is gone, the bed carries the logo, then rings out.
* EQ pocket (−5 dB @1.8 kHz, −3 dB @450 Hz) clears the voice's intelligibility band, and
  a sidechain compressor keyed by the voice (−6 dB into the key, threshold .03, ratio 3,
  attack 15 ms, release 260 ms) ducks the bed on every syllable.
* Calibration measures gap = RMS_dB(voice) − RMS_dB(processed bed) per section and moves
  gain by (gap − target), 6 times (the sidechain is non-linear, one pass is not enough),
  capped at ×6.0. Against the RAW voice, so a quiet AI-avatar voice gets a quiet bed and
  the master lifts both together.
* Targets 12-16 dB (house: 14; 13 where the story lifts/peaks). Lower = louder music.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402
import audiokit as ak  # noqa: E402

CFG = "bed.json"
REPORT = "assets/bgm/music_report.json"
OUT = "assets/bgm/bed.wav"
SR = ak.SR
DEFAULTS = {"music": "assets/bgm/music.mp3", "offset": None, "voice": None, "end": None,
            "outro": None, "sections": [], "drops": [], "swells": [], "iterations": 6,
            "gain_cap": 6.0, "start_gain": 0.25, "eq": [[1800, -5, 1], [450, -3, 1]],
            "sidechain": {"key_db": -6, "threshold": 0.03, "ratio": 3, "attack": 15,
                          "release": 260},
            "end_fade": 0.3}
HOUSE_GAP = 14.0
LIFT_GAP = 13.0


def load_report():
    return json.load(open(REPORT, encoding="utf-8")) if os.path.exists(REPORT) else {}


def resolve(cfg, rep, voice):
    """(offset, END, O) with the documented precedence."""
    off = cfg.get("offset")
    if off is None:
        off = rep.get("offset", 0.0)
    o_start, o_end = ak.outro_info()
    end = cfg.get("end") or o_end or rep.get("end") or ak.duration(voice)
    O = cfg.get("outro")
    if O is None:
        O = o_start if o_start is not None else rep.get("outro_start")
    if O is not None and O >= end - 0.5:
        O = None
    return float(off), float(end), (float(O) if O is not None else None)


def init(rep, plan_path="music_plan.json"):
    """bed.json from the story sections: the plan's sections up to the outro (the outro
    gets the lift instead), targets from each section's gap_db or the house values, and
    one relative drop right before the turn word."""
    plan = json.load(open(plan_path, encoding="utf-8")) if os.path.exists(plan_path) else {}
    secs, prev = [], 0.0
    turn = rep.get("turn_time") or plan.get("turn_time")
    O = rep.get("outro_start") or plan.get("outro_start")
    for s in plan.get("sections", []):
        a, b = prev, float(s["end"])
        prev = b
        if O is not None and a >= O - 0.01:
            continue
        tgt = s.get("gap_db")
        if tgt is None:
            lift = (turn is not None and abs(a - turn) < 0.05) or s["name"] in ("peak", "lift")
            tgt = LIFT_GAP if lift else HOUSE_GAP
        secs.append([s["name"], round(a, 2), round(b, 2), float(tgt)])
    if not secs:
        sys.exit("no sections: write music_plan.json (music.py --init) or bed.json by hand")
    drops = [[round(turn - 0.36, 2), round(turn - 0.10, 2), 0.15]] if turn else []
    cfg = {"_comment": "see the docstring of scripts/bed.py for every key. Add a drop "
                       "[a, b, 0.08-0.15] just before each punchline; lower a section's "
                       "target (12-13) where the story peaks.",
           "music": rep.get("music", DEFAULTS["music"]), "offset": None, "end": None,
           "outro": None, "sections": secs, "drops": drops, "swells": []}
    json.dump(cfg, open(CFG, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return cfg


def keyframes(g, sec, drops, swells, END, O):
    """Gain keyframes (t, gain) — sorted, strictly increasing in t."""
    K = []
    for n, a, b, _ in sec:
        K += [(a + (0.12 if a > 0 else 0), g[n]), (b - (0.12 if b < END else 0), g[n])]

    def base_at(a):
        return next((g[n] for n, s0, s1, _ in sec if s0 - 0.5 <= a <= s1 + 0.5), min(g.values()))
    for a, b, r in drops:
        base = base_at(a)
        K += [(a - 0.06, base), (a, base * r), (b, base * r), (b + 0.02, base)]
    for a, b, r in swells:
        base = base_at(a)
        K += [(a, base), ((a + b) / 2, base * r), (b, base)]
    if O is not None:
        last = g[sec[-1][0]]
        K += [(O, last), (O + 0.5, last * 2.0), (O + 2.2, last * 1.6),
              (END - 0.5, last * 0.6), (END, 0.0)]
    K.sort(key=lambda k: k[0])
    out = []
    for t, v in K:                    # never two keys on one timestamp (see docstring)
        if out and t <= out[-1][0] + 1e-4:
            t = out[-1][0] + 0.001
        out.append((t, v))
    return out


def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("--bed", default=CFG)
    ap.add_argument("--init", action="store_true", help="write bed.json from the music plan")
    ap.add_argument("--force", action="store_true", help="with --init: overwrite bed.json")
    ap.add_argument("--voice", help="raw voice (default media.json aroll / assets/aroll.mp4)")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--apply", action="store_true",
                    help="write the entry into media.json audio.music (replaces the list)")
    a = ap.parse_args()
    hfcfg.load(a.config)
    hfcfg.require("ffmpeg", "ffprobe")
    hfcfg.ensure_deps(["numpy"])
    import numpy as np

    rep = load_report()
    if a.init:
        if os.path.exists(a.bed) and not a.force:
            sys.exit(f"{a.bed} exists — edit it, or --force to rebuild it from the plan")
        c = init(rep)
        print(f"  wrote {a.bed}:")
        for s in c["sections"]:
            print(f"    {s[0]:10s} {s[1]:6.2f} → {s[2]:6.2f}   {s[3]:.0f} dB under the voice")
        for d in c["drops"]:
            print(f"    drop {d[0]:.2f}-{d[1]:.2f} × {d[2]}  (before the turn)")
        print("  add drops before punchlines, then run bed.py")
        return 0
    if not os.path.exists(a.bed):
        sys.exit(f"{a.bed} not found — run bed.py --init (after music.py)")
    cfg = dict(DEFAULTS, **json.load(open(a.bed, encoding="utf-8")))
    voice = ak.voice_path(a.voice or cfg.get("voice"))
    for f in (cfg["music"], voice):
        if not os.path.exists(f):
            sys.exit(f"{f} not found")
    OFF, END, O = resolve(cfg, rep, voice)
    SEC = [(str(n), float(s), float(e), float(t)) for n, s, e, t in cfg["sections"]]
    for n, s, e, _ in SEC:
        if e - s < 0.5:
            sys.exit(f"section {n} is shorter than 0.5 s")
    if O is not None and SEC[-1][2] > O + 0.01:
        print(f"  ! the last section ends at {SEC[-1][2]:.2f}s, after the outro start "
              f"{O:.2f}s — the outro lift starts at the outro")

    # 1. trim by offset, cut to END, 12 ms fade-in
    x = ak.read_audio(cfg["music"], mono=False, start=OFF)
    n = int(round(END * SR))
    have = len(x) / SR
    if len(x) < n:
        print(f"  ! the track (after the {OFF:.2f}s trim) ends at {have:.2f}s — "
              f"{END - have:.2f}s of silence padded to END {END:.2f}s")
        x = np.pad(x, ((0, n - len(x)), (0, 0)))
    x = x[:n].astype(np.float64)
    f = int(0.012 * SR)
    x[:f] *= np.linspace(0, 1, f)[:, None]
    if O is None and cfg.get("end_fade"):
        k = int(float(cfg["end_fade"]) * SR)
        x[-k:] *= np.linspace(1, 0, k)[:, None]
    v = ak.read_audio(voice)
    v = np.pad(v, (0, max(0, n - len(v))))[:n]

    os.makedirs("build", exist_ok=True)
    env_wav = "build/_bed_env.wav"
    eq = ",".join(f"equalizer=f={fr}:t=q:w={q}:g={gdb}" for fr, gdb, q in cfg["eq"]) or "anull"
    sc = cfg["sidechain"]
    fc = (f"[0:a]{eq}[m];[1:a]aresample={SR},aformat=channel_layouts=stereo,"
          f"volume={sc['key_db']}dB,apad=whole_dur={END}[k];"
          f"[m][k]sidechaincompress=threshold={sc['threshold']}:ratio={sc['ratio']}:"
          f"attack={sc['attack']}:release={sc['release']}[o]")
    drops = [tuple(map(float, d)) for d in cfg["drops"]]
    swells = [tuple(map(float, d)) for d in cfg["swells"]]
    t = np.arange(n) / SR

    def render(g):
        K = keyframes(g, SEC, drops, swells, END, O)
        gg = np.interp(t, [k[0] for k in K], [k[1] for k in K])
        ak.write_wav(env_wav, x * gg[:, None], codec="pcm_f32le")
        ak.ff("-i", env_wav, "-i", voice, "-filter_complex", fc, "-map", "[o]",
              "-t", f"{END:.3f}", "-ar", str(SR), "-c:a", "pcm_s16le", a.out)
        d = ak.read_audio(a.out)
        gaps = {nm: ak.db(v[int(s * SR):int(e * SR)]) - ak.db(d[int(s * SR):int(e * SR)])
                for nm, s, e, _ in SEC}
        return gaps, float(np.abs(d).max()), d

    g = {nm: float(cfg["start_gain"]) for nm, *_ in SEC}
    cap = float(cfg["gain_cap"])
    for _ in range(int(cfg["iterations"])):
        gaps, pk, _ = render(g)
        g = {nm: min(cap, g[nm] * 10 ** ((gaps[nm] - tgt) / 20)) for nm, s, e, tgt in SEC}
    gaps, pk, d = render(g)
    os.remove(env_wav)

    print(f"\n  bed: {cfg['music']} trimmed {OFF:.3f}s, 0 → {END:.2f}s"
          f"{f', outro lift from {O:.2f}s' if O is not None else ''}")
    print(f"  {'section':10s} {'span':>15s} {'target':>7s} {'gap':>6s} {'gain':>7s}")
    rows = []
    for nm, s, e, tgt in SEC:
        flag = "" if abs(gaps[nm] - tgt) <= 0.5 else ("   ← capped" if g[nm] >= cap - 1e-6
                                                      else "   ← off target")
        print(f"  {nm:10s} {s:6.2f}-{e:6.2f}s {tgt:6.1f} {gaps[nm]:6.1f} {g[nm]:7.3f}{flag}")
        rows.append({"name": nm, "start": s, "end": e, "target": tgt,
                     "gap": round(gaps[nm], 2), "gain": round(g[nm], 4)})
    if O is not None:
        o_db, body_db = ak.db(d[int(O * SR):int(min(END, O + 2.2) * SR)]), ak.db(d[:int(O * SR)])
        print(f"  outro      {O:6.2f}-{END:6.2f}s  lift ×2.0 / ×1.6 / ×0.6 → 0 "
              f"(bed {o_db:.1f} dBFS RMS over O..O+2.2, body {body_db:.1f})")
        if o_db < body_db - 15:
            print("  ! the music is (nearly) SILENT under the outro: the track ended before "
                  "it. Regenerate with a longer last section (music_plan.json \"tail\"), or "
                  "pick a variant with a smaller offset — the logo should not land in silence")
    print(f"  peak {20 * np.log10(pk + 1e-9):.1f} dBFS"
          f"{'   ← near clipping: lower the targets gap or start_gain' if pk > 0.97 else ''}")
    entry = {"id": "bgm", "src": a.out, "start": 0, "duration": round(END, 3),
             "volume": 1.0, "baked": True}
    json.dump({"offset": OFF, "end": END, "outro": O, "sections": rows,
               "drops": drops, "peak": round(pk, 4), "media_entry": entry},
              open("build/bed_report.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("\n  media.json → audio.music:\n  " + json.dumps([entry], ensure_ascii=False))
    print("  (\"baked\": the level and the outro lift are inside the file — play it at 1.0 "
          "and do not automate it again)")
    if a.apply:
        if not os.path.exists("media.json"):
            sys.exit("  media.json not found — paste the entry above when you write it")
        m = json.load(open("media.json", encoding="utf-8"))
        old = m.setdefault("audio", {}).get("music", [])
        m["audio"]["music"] = [entry]
        json.dump(m, open("media.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        print(f"  ✓ media.json audio.music set (replaced {len(old)} entr{'y' if len(old) == 1 else 'ies'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
