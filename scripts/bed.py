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
* NEVER silence at the end. A from-zero test shipped a reel whose music audibly ended
  1.1 s before the last frame, so the outro lift had nothing to lift. When the trimmed
  track's audible end (last 50 ms frame within 20 dB of its median) is more than 0.25 s
  before END, the track is extended with a natural tail (`extend_tail`, auto):
    loop   — the stretch BEFORE the track's ending (its final hit / ring, where the level
             first falls 6 dB under the median) is lengthened by repeating its last bar
             (4 beats, measured by autocorrelation; 2 s if no pulse) with equal-power
             crossfades, then the track's OWN ending plays, landing just after END;
    reverb — the fallback with no steady bar to loop: a synthetic hall wash of the last
             1.5 s, faded in as the dry chord dies away, 6 dB under the median level,
             RT60 = max(4 s, 3 × the span) so it is ≈ −20 dB at END.
  It is printed and written to build/bed_report.json ("tail"). `"extend_tail": false`
  in bed.json turns it off (then the gap is padded with silence and flagged ✗).

    python3 scripts/bed.py selftest        # negative tests of the tail extension
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
            "end_fade": 0.3, "extend_tail": "auto"}
HOUSE_GAP = 14.0
LIFT_GAP = 13.0
TAIL_SLACK = 0.25          # an audible end this close to END needs no extension
BAR = 2.0                  # loop length when no beat can be measured (1 bar at 120 bpm)
XFADE = 0.25


def audible_end(x, hop=0.05, below=20.0):
    """Seconds: the end of the last 50 ms frame within `below` dB of the track's median
    level (non-silent frames). Same rule as music.py's natural end, finer grain."""
    import numpy as np
    m = x.mean(1) if x.ndim == 2 else x
    e = ak.rms_frames(m, SR, hop)
    loud = e[e > -60]
    if not loud.size:
        return 0.0
    alive = np.nonzero(e > float(np.median(loud)) - below)[0]
    return float((alive[-1] + 1) * hop) if alive.size else 0.0


def bar_length(x, b):
    """Seconds of one 4-beat bar, from the pulse of the 8 s before sample `b`
    (autocorrelation of the log-energy onset envelope). A loop one bar long repeats in
    phase; a fixed 2 s loop on a 90 bpm groove stumbles on every repeat. The strongest
    pulse (often hats, 8ths or 16ths) is doubled into the 0.4-0.8 s beat range, × 4.
    Falls back to BAR when there is no clear pulse."""
    import numpy as np
    m = x[max(0, b - int(8 * SR)):b]
    m = m.mean(1) if m.ndim == 2 else m
    if len(m) < 4 * SR:
        return BAR
    on = np.maximum(0, np.diff(ak.rms_frames(m, SR, 0.01)))
    on = on - on.mean()
    ac = np.correlate(on, on, "full")[len(on) - 1:]
    if ac[0] <= 0:
        return BAR
    k = 20 + int(np.argmax(ac[20:100]))             # pulse lag in 0.2-1.0 s
    if ac[k] / ac[0] < 0.12:
        return BAR
    beat = k * 0.01
    while beat < 0.4:
        beat *= 2
    return round(4 * beat, 3)


def extend_tail(x, end, mode="auto"):
    """x (n, 2) trimmed track → (exactly END long, info or None). See the docstring:
    a track that audibly ends before END gets a natural tail instead of silence.

    loop:   the stretch before the track's ending (where the level first falls 6 dB
            under its median, i.e. the final hit / ring) is extended by repeating its
            last bar with equal-power crossfades, then the track's OWN ending plays —
            so the real final hit and ring land just after END, not in mid-outro.
    reverb: a synthetic hall wash of the last 1.5 s, faded in as the dry chord fades
            out; the fallback when there is no steady bar to loop."""
    import numpy as np
    n = int(round(end * SR))

    def fit(y):
        return y[:n] if len(y) >= n else np.pad(y, ((0, n - len(y)), (0, 0)))
    ae = min(audible_end(x), len(x) / SR)
    if ae >= end - TAIL_SLACK:
        return fit(x), None
    gap = end - ae
    info = {"audible_end": round(ae, 2), "gap": round(gap, 2), "seconds_added": round(gap, 2)}
    if not mode:
        return fit(x), dict(info, mode="none")
    hop = 0.05
    m = x[:int(ae * SR)].mean(1)
    e = ak.rms_frames(m, SR, hop)
    med = float(np.median(e[e > -60])) if (e > -60).any() else -60.0
    full = np.nonzero(e > med - 6)[0]
    b0 = int((full[-1] + 1) * hop * SR) if full.size else int(ae * SR)   # the ending starts
    bar = bar_length(x, b0)
    if mode in ("auto", "loop") and b0 >= int((bar + 0.5) * SR):
        seg = x[b0 - int(bar * SR):b0].astype(np.float64)
        ending = x[b0:].astype(np.float64)
        xf = int(min(XFADE, bar / 4) * SR)
        up = np.sqrt(np.linspace(0, 1, xf))[:, None]
        down = np.sqrt(np.linspace(1, 0, xf))[:, None]
        # the ending should start where its audible end lands 0.3 s after END
        target = n - int(round((ae - b0 / SR) * SR)) + int(0.3 * SR)
        y, reps = x[:b0].astype(np.float64), 0
        while len(y) < target:
            y = np.concatenate([y[:-xf], y[-xf:] * down + seg[:xf] * up, seg[xf:]])
            reps += 1
        y = y[:max(b0, target)]
        if len(ending) > xf:              # a cut-off track has no ending left to play
            y = np.concatenate([y[:-xf], y[-xf:] * down + ending[:xf] * up, ending[xf:]])
        return fit(y), dict(info, mode="loop", bar=bar, repeats=reps,
                            ending_kept=round(ae - b0 / SR, 2))
    # reverb: the wash fades in from the start of the ending as the dry chord dies away
    b = int(ae * SR)
    rng = np.random.default_rng(11)
    span = (n - b0) / SR
    rt60 = max(4.0, 3.0 * span)                 # ≈ −20 dB at END: still under the logo
    L = int(min(rt60, span + 1.0) * SR)
    t = np.arange(L) / SR
    ir = rng.standard_normal((L, 2)) * np.exp(-6.9 * t / rt60)[:, None]
    for _ in range(3):                                                  # darker wash
        ir = (ir + np.roll(ir, 1, 0)) / 2
    src = x[max(0, b0 - int(1.5 * SR)):max(b0, b)].astype(np.float64)
    M = len(src) + L
    F = 1 << (M - 1).bit_length()
    wet = np.fft.irfft(np.fft.rfft(src, F, axis=0) * np.fft.rfft(ir, F, axis=0), F, axis=0)[:M]
    wet = wet[len(src) - (max(b0, b) - b0):]        # aligned so wet[0] sits at b0
    wet *= 10 ** ((med - 6.0 - ak.db(wet[:int(0.5 * SR)])) / 20)
    y = fit(x).astype(np.float64)
    w = wet[:n - b0]
    ramp = np.minimum(1.0, np.arange(len(w)) / (0.5 * SR))[:, None]
    y[b0:b0 + len(w)] += w * ramp
    return y, dict(info, mode="reverb", rt60=round(rt60, 1))


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

    # 1. trim by offset, extend an early ending with a natural tail, cut to END, fade in
    x = ak.read_audio(cfg["music"], mono=False, start=OFF)
    n = int(round(END * SR))
    x, tail = extend_tail(x, END, cfg.get("extend_tail", "auto"))
    if tail and tail["mode"] != "none":
        how = (f"{tail['repeats']} crossfaded repeat(s) of its last {tail['bar']:.2f}s bar, "
               f"then its own {tail['ending_kept']:.2f}s ending" if tail["mode"] == "loop"
               else f"a reverb wash of the final chord (RT60 {tail['rt60']}s)")
        print(f"  ↳ the music audibly ended at {tail['audible_end']:.2f}s, {tail['gap']:.2f}s "
              f"before END — extended to END with {how}")
    elif tail:
        print(f"  ✗ the music audibly ends at {tail['audible_end']:.2f}s, {tail['gap']:.2f}s "
              f"before END, and extend_tail is off — the end is padded with SILENCE")
    x = x.astype(np.float64)
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
    # GATE: the bed must still sound just before the last frame (the outro lift is at
    # ×0.6 there; with no outro the 0.3 s end fade starts after this window)
    w0, w1 = (END - 1.0, END - 0.5) if O is not None else (END - 0.8, END - 0.3)
    end_db, body_db = ak.db(d[int(max(0, w0) * SR):int(w1 * SR)]), ak.db(d[:int(w0 * SR)])
    silent_end = end_db < body_db - 30
    print(f"  end        {w0:6.2f}-{w1:6.2f}s  bed {end_db:.1f} dBFS RMS (body {body_db:.1f})"
          + ("   ✗ SILENT before the last frame" if silent_end else "   ✓ music to the end"))
    entry = {"id": "bgm", "src": a.out, "start": 0, "duration": round(END, 3),
             "volume": 1.0, "baked": True}
    json.dump({"offset": OFF, "end": END, "outro": O, "sections": rows,
               "drops": drops, "peak": round(pk, 4), "tail": tail,
               "end_db": round(end_db, 1), "silent_end": silent_end, "media_entry": entry},
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
    return 1 if silent_end else 0


def selftest():
    """Negative tests of the tail extension — `bed.py selftest`. Synthetic buffers, no
    files, no credits. Exit 1 on failure."""
    import numpy as np
    fails = []
    t = np.arange(int(20 * SR)) / SR
    tone = (np.sin(2 * np.pi * 220 * t) * 0.3)[:, None] * np.ones((1, 2))

    def level(y, a, b):
        return ak.db(y[int(a * SR):int(b * SR)])
    # 1. a steady track physically 1.5 s too short → loop, still at full level near END
    y, info = extend_tail(tone[:int(18.5 * SR)], 20.0)
    if not info or info["mode"] != "loop" or len(y) != int(20 * SR):
        fails.append(f"steady short track: expected a loop to 20 s, got {info}, {len(y) / SR:.2f}s")
    elif abs(level(y, 19.0, 19.9) - level(tone, 10, 12)) > 3:
        fails.append("steady short track: the looped tail is not at the track's level")
    # 2. a track that rings out 3.4 s before END → loop the body, keep its OWN ending
    #    (the ring), which now lands at END: full level just before it
    env = np.ones(len(t))
    env[int(16 * SR):] = np.exp(-(t[int(16 * SR):] - 16) * 4)
    ringing = tone * env[:, None]
    y, info = extend_tail(ringing, 20.0)
    if not info or info["mode"] != "loop" or not info.get("ending_kept", 0) > 0.2:
        fails.append(f"ringing track: expected loop + its own ending, got {info}")
    elif abs(level(y, 19.0, 19.5) - level(tone, 10, 12)) > 3:
        fails.append(f"ringing track: not at full level before END ({level(y, 19.0, 19.5):.1f})")
    # 3. reverb (forced): still audible (> −45 dBFS) just before END
    y, info = extend_tail(ringing, 20.0, mode="reverb")
    if not info or info["mode"] != "reverb" or level(y, 19.5, 19.8) < -45:
        fails.append(f"reverb tail: silent at the end or wrong mode ({info})")
    # 4. a track that plays to END needs nothing and is returned at exactly END
    y, info = extend_tail(tone, 19.0)
    if info is not None or len(y) != int(19 * SR):
        fails.append(f"long-enough track: should be untouched, got {info}")
    # 5. extend_tail off → padded with silence, and SAID so (mode none)
    y, info = extend_tail(tone[:int(18 * SR)], 20.0, mode=False)
    if not info or info["mode"] != "none" or level(y, 19.0, 19.9) > -100:
        fails.append(f"extend_tail off: expected silence + mode none, got {info}")
    for f in fails:
        print("  ✗ " + f)
    print(f"  bed selftest: {5 - len(fails)}/5 passed")
    return 1 if fails else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["selftest"]:
        hfcfg.ensure_deps(["numpy"])
        sys.exit(selftest())
    sys.exit(main())
