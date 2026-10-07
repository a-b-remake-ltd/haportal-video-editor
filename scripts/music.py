#!/usr/bin/env python3
"""Score the reel: generate (or take) a music track, analyse it, and align its drop to the
story's turn.  references/sound.md §Music.

    python3 scripts/music.py --init --turn-word "so I decided"     # write music_plan.json
    python3 scripts/music.py --dry-run                              # show requests + credit estimate
    python3 scripts/music.py                                        # generate 2 variants, analyse, pick
    python3 scripts/music.py --file my_licensed_track.mp3           # no key: analyse + align a track
    python3 scripts/music.py --procedural                           # no key, no track: quiet synth bed
    python3 scripts/music.py --reanalyse                            # re-pick from existing variants
    python3 scripts/music.py --force --stronger-turn                # ONE retry after a weak turn
    python3 scripts/music.py --credits                              # measured spend, once it settled
    python3 scripts/music.py selftest                               # negative tests (no credits)

Outputs  assets/bgm/music.mp3 (the chosen track, UNTRIMMED) and assets/bgm/music_report.json
(offset, drop, natural ending, the turn gate, the analysis of every variant). bed.py reads
the offset from the report — the trim is applied there, once.

WHY each step:
* Music is generated per video from a composition_plan whose sections follow the story,
  so the track's own structure (tension → hit → lift → peak → resolve) happens where the
  story does. A stock bed never turns where the speaker turns.
* The TURN is built into the plan, not hoped for: a from-zero test got a track whose
  only real drop sat 16 s before the turn, because the section styles described moods,
  not structure. composition() now adds concrete musical instructions around the turn —
  the section BEFORE it is sparse and filtered and ends on a sudden stop, the turn
  section "begins with a sudden full drop on the very first beat" — and names both
  sections for what they are. "EDM drop" is no longer a global negative (it told the
  model to avoid the one thing the plan needs); the cheesy genre is excluded by name.
* TWO variants with contrasting global styles, requested SEQUENTIALLY (parallel requests
  return HTTP 429). The model does not put a drop exactly on a section boundary; having
  two to choose from is what makes landing it on the turn word reliable.
* Credits are checked BEFORE the first request; the run stops with a clear message
  instead of failing halfway through. AFTER it, the spend is reported as an ESTIMATE from
  the generated length: the balance endpoint lags minutes, and reading it straight away
  printed "credits used: 0" while ~1,700 had gone. `--credits` reads the measured change
  later (build/credits_ledger.json).
* Analysis: RMS per 0.5 s (to see the structure); candidate changes at 20 ms; each one
  scored by its MAGNITUDE (2 s after vs 2 s before), its CONTRAST (a real drop follows a
  quieter / sparser stretch, a rise inside a plateau does not) and its suddenness. In the
  same test the old detector called a +4.5 dB rise in the middle of a plateau "the drop"
  while the real +13.8 dB lift sat elsewhere — now the report names the strongest change
  and where it would land.
* TURN GATE: the change that lands on the turn must lift ≥ 6 dB. Below that the run says
  so loudly, exits 3, and suggests ONE regeneration with --stronger-turn. It never loops
  on its own: every round costs credits.
* offset = dropTime − (turnWordStart − 0.06): the track is trimmed by `offset` so the drop
  hits 60 ms before the turn word — the ear hears the hit, then the word.
* The first plan section is `lead` (0.5 s) longer than the story section, so the drop
  is asked for slightly AFTER the turn and the offset comes out positive (you can trim a
  track's start, you cannot un-trim it).
* LENGTH: a ring-out section of ≥ 3.5 s is requested after END, so even after a late
  drop's trim the track keeps sounding ≥ 2 s past the last frame (a track that ran out
  1 s early left the logo in silence). bed.py still extends an early ending, and says so.
* No ElevenLabs key: `--file` takes a track the user is licensed to use and runs the same
  analysis + alignment; `--procedural` synthesises a quiet, royalty-free bed with a hit on
  the turn; or score nothing (bed.py is simply not run). The script says which.
"""
import json
import math
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402
import audiokit as ak  # noqa: E402

PLAN = "music_plan.json"
BGM = "assets/bgm"
REPORT = os.path.join(BGM, "music_report.json")
MUSIC_URL = "/v1/music?output_format=mp3_44100_192"
# Measured: 2 × 55 s = 1,512 and 2 × 62.9 s ≈ 1,714 credits, i.e. ~820 per generated
# minute. The GATE estimates high so a batch never starts that it cannot finish; the
# REPORT uses the measured rate so the number it prints is the real cost.
CREDITS_PER_MIN = 1000
CREDITS_PER_MIN_EST = 820
TURN_MIN_DB = 6.0          # the change landing on the turn must lift at least this much
TAIL_MIN = 3.5             # ring-out requested past END (late drop ≤ lead+1.5 s, + 2 s spare)
MAX_OFFSET_GEN = 8.0       # largest trim for a generated track (see the analyse loop)

# Two contrasting global styles are the default pair; "tech" is a third, darker option.
# Every preset keeps "leaves space for a spoken voice" and "starts immediately": the bed
# sits under speech, and a silent intro is the most common generated-music defect.
PRESETS = {
    "trap": ["instrumental modern cinematic trap hybrid", "deep 808 sub bass",
             "crisp percussion that builds", "hopeful synth pads", "inspiring and powerful",
             "leaves space for a spoken voice", "starts immediately"],
    "score": ["instrumental cinematic motivational score", "deep sub bass pulse",
              "modern electronic textures", "emotional felt piano motif",
              "leaves space for a spoken voice", "starts immediately"],
    "tech": ["modern cinematic electronic underscore", "deep sub-bass",
             "minimal clean percussion", "sleek premium tech commercial", "dark and confident",
             "leaves space for a spoken voice", "starts immediately"],
}
# NOT "EDM drop": as a global negative it told the model to avoid the very drop the plan
# asks for on the turn. The cheesy genre is excluded by name instead.
NEGATIVE = ["vocals", "singing", "lyrics", "choir", "rap", "vocal chops", "cheesy",
            "festival EDM", "dubstep wobble", "aggressive distortion", "long silence",
            "fade in from silence"]

# The emotional arc of a motivational / call-to-action monologue. --init maps it onto
# the real timeline (turn word, end of speech, outro); the editor then rewrites the
# styles for THIS story. The STRUCTURE around the turn (filtered build → stop → full
# drop) is added by composition() whatever the styles say — see TURN_BUILD / TURN_DROP.
ARC = {
    "hook": ["starts on the first beat, light pulse and a muted plucked motif, sparse"],
    "tension": ["restrained, tension grows slowly, darker"],
    "turn": ["hope and confidence, warm piano chords over the full beat"],
    "drive": ["driving and uplifting, steady momentum, full groove"],
    "peak": ["emotional peak, big and free, soaring"],
    "outro": ["one final deep hit, then the last chord rings"],
}
MIN_SECTION = 3.0          # ElevenLabs rejects composition-plan sections under 3 s

# Concrete musical instructions for the two sections around the turn, by strength
# (1 = default, 2 = --stronger-turn, the one retry after a weak turn). Moods ("hope
# enters") gave the model nothing to place; instruments, filters and a stop do.
TURN_BUILD = {
    1: (["sparse and filtered: low-pass filtered drums, no kick drum, no sub bass",
         "builds tension towards the end of this section",
         "ends with a sudden stop: a hard cut to near silence in its last half second"],
        ["full drums", "kick drum", "drop", "climax", "long silence"]),
    2: (["very minimal and quiet: only a filtered pad and a soft ticking pulse, no drums at all",
         "a rising filter sweep towards the end",
         "ends with a hard stop: half a second of complete silence before the next section"],
        ["drums", "kick drum", "sub bass", "drop", "climax", "full band", "long silence"]),
}
TURN_DROP = {
    1: (["begins with a sudden full drop on the very first beat: kick drum, 808 sub bass "
         "and the full beat enter all at once",
         "loud and full from its first second"],
        ["gradual build", "slow fade in", "soft intro", "riser", "quiet start", "silence"]),
    2: (["THE DROP: the hardest-hitting, loudest moment of the whole track lands on the "
         "very first beat of this section",
         "kick drum, 808 sub bass, full drums and chords all hit together at once, no lead-in"],
        ["gradual build", "build-up", "slow fade in", "soft intro", "riser", "filtered",
         "quiet start", "silence"]),
}
RING_OUT = (["the final chord keeps ringing: sustained, a slow natural decay, no new elements"],
            ["abrupt ending", "sudden silence", "new melody"])
KEEP_GOING = "the music keeps sounding to the very end of this section, no early fade-out"


# ------------------------------------------------------------------ timeline context
def norm(w):
    return re.sub(r"[\"'.,!?;:()\[\]״׳–—-]", "", w).strip().lower()


def find_phrase(words, phrase, occurrence=1):
    """Start time of the `occurrence`-th match of `phrase` in the word table."""
    toks = [norm(t) for t in phrase.split() if norm(t)]
    seq = [norm(w[2]) for w in words]
    hits = [i for i in range(len(seq) - len(toks) + 1) if seq[i:i + len(toks)] == toks]
    if len(hits) < occurrence:
        return None
    return words[hits[occurrence - 1]][0]


def timeline(cfg, plan, words, voice):
    """(speech_end, outro_start, END) of the composition, from the best source available."""
    o_start, o_end = ak.outro_info()
    dur = ak.duration(voice) if os.path.exists(voice) else (words[-1][1] if words else 0)
    if o_start is not None:
        return dur, o_start, o_end
    end = plan.get("end")
    if end:
        o = plan.get("outro_start")
        return dur, (float(o) if o else None), float(end)
    if cfg.get("outro", {}).get("enabled"):       # an outro is planned but not built yet
        o = round(dur + 0.25, 2)
        return dur, o, round(o + 4.5, 2)
    return dur, None, round(dur, 2)


def turn_time(a, plan, words):
    if a.turn_time is not None:
        return float(a.turn_time), f"{a.turn_time:.2f}s (given)"
    phrase = a.turn_word or plan.get("turn_word")
    if plan.get("turn_time") is not None and not a.turn_word:
        return float(plan["turn_time"]), f"{float(plan['turn_time']):.2f}s (plan)"
    if phrase:
        t = find_phrase(words, phrase, int(plan.get("turn_occurrence", 1)))
        if t is None:
            sys.exit(f"turn word '{phrase}' not found in src/words.json — pass --turn-time "
                     f"or fix the phrase (spelling as transcribed)")
        return t, f"'{phrase}' at {t:.2f}s"
    return None, "none"


# ------------------------------------------------------------------ plan
def default_plan(words, speech_end, outro_start, end, turn, turn_label):
    """Map ARC onto this video: hook to the first sentence end after ~4 s, tension to the
    turn, then turn / drive / peak through the speech, then the outro."""
    def sentence_end(lo, hi, fallback):
        for s, e, w in words:
            if lo <= e <= hi and re.search(r"[.!?]$", w):
                return round(e + 0.08, 2)
        return fallback
    T = turn if turn else round(speech_end * 0.35, 2)
    hook = sentence_end(4.0, min(8.0, T - MIN_SECTION), min(6.0, T - MIN_SECTION))
    body_end = outro_start if outro_start else end
    rest = body_end - T
    cut1, cut2 = round(T + rest * 0.40, 2), round(T + rest * 0.72, 2)
    secs = [("hook", hook), ("tension", T), ("turn", cut1), ("drive", cut2), ("peak", body_end)]
    if end - body_end >= MIN_SECTION:
        secs.append(("outro", end))
    elif end > body_end:
        secs[-1] = ("peak", end)
    out, prev = [], 0.0
    for name, e in secs:
        if e - prev < MIN_SECTION and out:          # too short → merge into the previous
            out[-1]["end"] = round(e, 2)
            prev = e
            continue
        out.append({"name": name, "end": round(e, 2), "styles": ARC[name], "gap_db": None})
        prev = e
    return {
        "_comment": "Sections follow the STORY in video time (each starts where the previous "
                    "ends). Rewrite the styles for this video's emotional arc. variants = "
                    "preset names (" + ", ".join(PRESETS) + ") or {\"name\", \"styles\"}. "
                    "gap_db = music under voice for bed.py (null = house default). "
                    "turn_structure adds the filtered build + stop before the turn and the "
                    "full drop on its first beat (keep it true); turn_strength 2 = the "
                    "emphatic version (--stronger-turn). tail = ring-out past END (≥ 3.5).",
        "variants": ["trap", "score"],
        "extra_global_styles": [],
        "negative_global_styles": NEGATIVE,
        "turn_word": None, "turn_time": T if turn else None, "turn_label": turn_label,
        "turn_structure": True, "turn_strength": 1,
        "lead": 0.5, "tail": TAIL_MIN,
        "outro_start": outro_start, "end": end,
        "sections": out,
    }


def section_bounds(plan):
    """[(name, start, end)] in video time."""
    out, prev = [], 0.0
    for s in plan["sections"]:
        e = float(s["end"])
        out.append((s["name"], prev, e))
        prev = e
    return out


def turn_index(plan):
    """Index of the section that STARTS on the turn (the drop section), or None. Found by
    time, so a plan whose sections were renamed still gets its structure."""
    t = plan.get("turn_time")
    if t is None:
        return None
    for i, (_, a, _) in enumerate(section_bounds(plan)):
        if i > 0 and abs(a - float(t)) < 0.6:
            return i
    return None


def _merge(first, rest):
    """first + rest without duplicates (case-insensitive), order kept."""
    out, seen = [], set()
    for s in list(first) + list(rest):
        if s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def composition(plan, styles, strength=None):
    """The ElevenLabs composition_plan for one variant.

    The editor's section styles carry the MOOD; this adds the STRUCTURE the alignment
    depends on: the section before the turn is sparse/filtered and ends on a stop, the
    turn section opens on a full drop (TURN_BUILD / TURN_DROP), the last story section
    keeps going to the end, and a ring-out section of ≥ TAIL_MIN s follows END."""
    lead = float(plan.get("lead", 0.5))
    # Ring-out past END: the chosen drop may need an offset up to ~lead + 1.5 s, and a
    # track that decays before the last frame leaves the logo in silence. A separate
    # section puts the decay AFTER END instead of inside the outro.
    tail = max(float(plan.get("tail", TAIL_MIN)), TAIL_MIN)
    strength = int(strength or plan.get("turn_strength") or 1)
    strength = 2 if strength >= 2 else 1
    ti = turn_index(plan) if plan.get("turn_structure", True) else None
    bounds = section_bounds(plan)
    n_last = len(bounds) - 1
    secs = []
    for i, (name, a, b) in enumerate(bounds):
        d = b - a + (lead if i == 0 else 0)
        if d < MIN_SECTION:
            sys.exit(f"section '{name}' is {d:.1f}s — ElevenLabs needs ≥ {MIN_SECTION:.0f}s; "
                     f"merge it into a neighbour in {PLAN}")
        src = plan["sections"][i]
        pos = list(src.get("styles") or ["steady"])
        neg = list(src.get("negative") or ["silence"])
        title = name.title()
        if ti is not None and i == ti - 1:
            p2, n2 = TURN_BUILD[strength]
            pos, title = _merge(pos, p2), f"{title} - filtered build, ends on a stop"
            neg = _merge([x for x in neg if x.lower() != "silence"], n2)
        elif ti is not None and i == ti:
            p2, n2 = TURN_DROP[strength]
            pos, title = _merge(p2, pos), f"{title} - DROP on the first beat"
            neg = _merge(neg, n2)
        if i == n_last:
            pos = _merge(pos, [KEEP_GOING])
        secs.append({"section_name": title[:100], "duration_ms": int(round(d * 1000)),
                     "positive_local_styles": pos, "negative_local_styles": neg, "lines": []})
    secs.append({"section_name": "Ring out", "duration_ms": int(round(tail * 1000)),
                 "positive_local_styles": list(RING_OUT[0]),
                 "negative_local_styles": list(RING_OUT[1]), "lines": []})
    gneg = [x for x in (plan.get("negative_global_styles") or NEGATIVE)
            if x.strip().lower() != "edm drop"]           # see NEGATIVE: it vetoes the drop
    return {"positive_global_styles": _merge(styles, plan.get("extra_global_styles") or []),
            "negative_global_styles": gneg, "sections": secs}


def plan_seconds(comp):
    return sum(s["duration_ms"] for s in comp["sections"]) / 1000.0


def variant_list(plan, cli):
    names = cli.split(",") if cli else plan.get("variants") or ["trap", "score"]
    out = []
    for v in names:
        if isinstance(v, dict):
            out.append((v["name"], list(v["styles"])))
        elif v in PRESETS:
            out.append((v, PRESETS[v]))
        else:
            sys.exit(f"unknown variant '{v}' — presets: {', '.join(PRESETS)}, or "
                     f"{{\"name\", \"styles\"}} in {PLAN}")
    return out


# ------------------------------------------------------------------ analysis
def analyse(path, verbose=True):
    """Structure, drops and defects of one track.

    drops = candidate structural changes: rises (the 20 ms frames where the next 1.5 s
    most exceed the previous 1.5 s, ≥ 3 s apart) and breaks (a short stop and the return
    after it), each refined to the sharpest 60 ms jump nearby (the actual hit). Every
    candidate is then SCORED, because "the biggest local rise" is not "the drop":
      lift_db   = level of the 2 s after − the 2 s before        (magnitude)
      pre_db    = the 2 s before, relative to the body's level   (contrast: a real drop
                  follows a quieter / sparser stretch; a rise inside a plateau doesn't)
      jump_db   = the 60 ms step at the hit                      (suddenness)
      strength  = lift + 0.5 × min(12, max(0, −pre)) + 0.2 × min(15, jump)
    `strongest` is the best change in the whole track, wherever it sits."""
    import numpy as np
    x = ak.read_audio(path)
    sr = ak.SR
    L = len(x) / sr
    r05 = ak.rms_frames(x, sr, 0.5)
    loud = r05[r05 > -60]
    med = float(np.median(loud)) if loud.size else -60.0
    if verbose:
        print(f"\n  {os.path.basename(path)}  {L:.1f}s  median {med:.1f} dB  (RMS per 0.5 s)")
        for i in range(0, len(r05), 12):
            print("   " + " ".join(f"{i * 0.5 + j * 0.5:5.1f}:{v:5.1f}"
                                   for j, v in enumerate(r05[i:i + 12])))
    # intro: first 20 ms frame above SILENCE — absolute (−45 dBFS) or 35 dB under the
    # median, whichever is higher. A soft swell-in is music; dead air is not.
    hop = 0.02
    e = ak.rms_frames(x, sr, hop)
    sil = max(-45.0, med - 35)
    on = np.nonzero(e > sil)[0]
    intro = float(on[0] * hop) if on.size else L
    # natural end: last 0.5 s window within 20 dB of the median
    alive = np.nonzero(r05 > med - 20)[0]
    nat_end = float((alive[-1] + 1) * 0.5) if alive.size else 0.0
    # dead gaps inside the body (≥ 1 s of silence)
    gaps, s = [], None
    for i, v in enumerate(r05):
        t = i * 0.5
        quiet = v < sil and intro + 0.5 < t < nat_end - 2.0
        if quiet and s is None:
            s = t
        elif not quiet and s is not None:
            if t - s >= 1.0:
                gaps.append([s, t])
            s = None
    # drops at 20 ms
    p = 10 ** (e / 10)
    c = np.concatenate([[0], np.cumsum(p)])
    w = int(1.5 / hop)
    idx = np.arange(w, len(e) - w)
    after = (c[idx + w] - c[idx]) / w
    before = (c[idx] - c[idx - w]) / w
    rise = 10 * np.log10((after + 1e-12) / (before + 1e-12))
    order = np.argsort(-rise)
    picks = []
    for k in order:
        t = idx[k] * hop
        if rise[k] < 2.0 or len(picks) >= 8:
            break
        if t < intro + 1.0 or any(abs(t - q["t"]) < 3.0 for q in picks):
            continue
        # refine to the sharpest 60 ms jump in [t − 0.5, t + 1.0]. Asymmetric on purpose:
        # under a riser the 1.5 s window peaks BEFORE the hit (a real drop measured at
        # 30.02 s was refined to a riser step at 29.32 s with a ±0.4 s window).
        lo, hi = max(3, int((t - 0.5) / hop)), min(len(e) - 4, int((t + 1.0) / hop))
        best, bj = t, -1e9
        for i in range(lo, hi):
            j = e[i:i + 3].mean() - e[i - 3:i].mean()
            if j > bj:
                bj, best = j, i * hop
        picks.append({"t": round(best, 2), "rise_db": round(float(rise[k]), 1),
                      "jump_db": round(float(bj), 1), "kind": "rise"})
    # BREAKS: a short stop (0.3-1 s, ≥ 5 dB under both sides) and the return after it.
    # A generated track often marks a section change with a brief stop rather than a
    # level change — the 1.5 s rise window averages it away, so look for it directly.
    def pdb(i0, i1):
        i0, i1 = max(0, i0), min(len(p), i1)
        return 10 * np.log10(p[i0:i1].mean() + 1e-12) if i1 > i0 else -120.0
    one = int(1.0 / hop)
    breaks = []
    for d in (0.3, 0.5, 0.7, 1.0):
        n = int(d / hop)
        for i in range(n + one, len(e) - one, 2):
            t = i * hop
            if t < intro + 1.5 or t > nat_end - 1.5:
                continue
            dip = pdb(i - n, i)
            depth = min(pdb(i - n - one, i - n), pdb(i, i + one)) - dip
            if depth >= 5.0:
                breaks.append((depth, t))
    for depth, t in sorted(breaks, reverse=True):
        if any(abs(t - q["t"]) < (1.5 if q["kind"] == "break" else 0.4) for q in picks):
            continue
        lo, hi = max(3, int((t - 0.25) / hop)), min(len(e) - 4, int((t + 0.35) / hop))
        best, bj = t, -1e9
        for i in range(lo, hi):
            j = e[i:i + 3].mean() - e[i - 3:i].mean()
            if j > bj:
                bj, best = j, i * hop
        picks.append({"t": round(best, 2), "rise_db": round(float(depth), 1),
                      "jump_db": round(float(bj), 1), "kind": "break"})
        if sum(q["kind"] == "break" for q in picks) >= 4:
            break
    # score every candidate (see the docstring): magnitude, contrast, suddenness
    ref = pdb(int((intro + 0.5) / hop), int(nat_end / hop))       # the body's level
    w2 = int(2.0 / hop)
    for q in picks:
        i = int(round(q["t"] / hop))
        pre, post = pdb(i - w2, i), pdb(i + 2, i + w2)
        q["lift_db"] = round(float(post - pre), 1)
        q["pre_db"] = round(float(pre - ref), 1)
        # contrast is capped at 12 dB: after a stop, −20 or −40 dB is the same "quiet"
        q["strength"] = round(float(q["lift_db"] + 0.5 * min(12.0, max(0.0, ref - pre))
                                    + 0.2 * min(15.0, max(0.0, q["jump_db"]))), 1)
    strongest = max(picks, key=lambda q: q["strength"]) if picks else None
    reasons = []
    if intro > 2.5:            # the trim takes ~0.5 s; more than 2.5 s is a dead opening
        reasons.append(f"silent intro {intro:.1f}s")
    if gaps:
        reasons.append("dead gap(s) " + ", ".join(f"{a:.1f}-{b:.1f}s" for a, b in gaps))
    return {"file": path, "length": round(L, 2), "median_db": round(med, 1),
            "intro_silence": round(intro, 2), "natural_end": round(nat_end, 2), "gaps": gaps,
            "drops": sorted(picks, key=lambda d: d["t"]), "strongest": strongest,
            "rejected": bool(reasons),
            "reason": "; ".join(reasons), "rms_0_5": [round(float(v), 1) for v in r05]}


def choose_drop(an, turn, lead, need_until, max_offset=MAX_OFFSET_GEN, end=None):
    """The drop of this track that can land on the turn: offset in [0, max_offset], the
    track still playing at `need_until` after the trim; score = strength, minus penalties
    for sitting far from where the plan asked for it (turn + lead) and for the length
    bed.py would have to add after the track's natural end (when `end` is given)."""
    best = None
    for d in an["drops"]:
        off = d["t"] - (turn - 0.06)
        if not (0 <= off <= max_offset):
            continue
        short = need_until - (an["length"] - off)
        dead = max(0.0, an["intro_silence"] - off - 0.3)   # near-silence left at frame 1
        ext = max(0.0, end - (an["natural_end"] - off)) if end else 0.0
        score = d.get("strength", d["rise_db"]) \
            - 2.0 * max(0.0, abs(d["t"] - (turn + lead)) - 1.0) \
            - (3.0 if short > 0.5 else 0.0) - 2.0 * dead - 1.5 * max(0.0, ext - 0.5)
        if best is None or score > best["score"]:
            best = dict(d, offset=round(off, 3), score=round(score, 2))
    return best


def turn_review(results, pick, offset, turn, min_db=TURN_MIN_DB):
    """The turn gate and the alternatives, as (passed, lines).

    WHY: a pick that only "can land on the turn" is not a good turn. The report must
    say how big the change on the turn really is, and where the strongest change of each
    track would land instead, so a weak result is visible before anyone renders it."""
    ch = pick.get("choice")
    lift = ch.get("lift_db") if ch else None
    lines = []
    if ch:
        lines.append(f"turn: the change chosen for the turn lifts {lift:+.1f} dB "
                     f"(at {ch['t']:.2f}s in '{pick['name']}', lands {ch['t'] - offset:.2f}s; "
                     f"strength {ch.get('strength', 0):.1f})")
    for r in results:
        s = r.get("strongest")
        if not s or (r is pick and ch and abs(s["t"] - ch["t"]) < 0.05):
            continue
        off = offset if r is pick else (r["choice"]["offset"] if r.get("choice") else None)
        where = (f" — would land at {s['t'] - off:.2f}s in the video (turn at {turn:.2f}s)"
                 if off is not None else " — cannot be aligned to the turn")
        lines.append(f"strongest change in '{r['name']}': {s['lift_db']:+.1f} dB at "
                     f"{s['t']:.2f}s in the track{where}")
    passed = lift is not None and lift >= min_db
    if not passed:
        what = (f"only {lift:+.1f} dB" if lift is not None else "no change at all")
        lines.append(f"✗ WEAK TURN: the change on the turn is {what} (gate ≥ {min_db:.0f} dB) — "
                     f"the drop will not be heard as the story's turn")
    return passed, lines


# ------------------------------------------------------------------ no-key fallback
def procedural(path, plan, turn, end, lead):
    """A quiet, royalty-free bed synthesised from the plan: a soft minor pad, a sub pulse
    at 90 bpm, hats from the turn on, a deep hit ON the turn (+lead so the offset works
    exactly like a generated track) and a ringing tail. It is a floor, not a score — it
    keeps the reel from sounding empty when there is no key and no licensed track."""
    import numpy as np
    sr = ak.SR
    L = end + lead + 0.5
    n = int(L * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(7)
    hit = (turn + lead) if turn else L * 0.35
    chords = [(220.0, 261.63, 329.63), (174.61, 220.0, 261.63), (130.81, 164.81, 196.0),
              (196.0, 246.94, 293.66)]                      # Am F C G
    bar = 60 / 90 * 4
    pad = np.zeros(n)
    for ci in range(int(L / bar) + 1):
        a, b = int(ci * bar * sr), min(n, int((ci + 1) * bar * sr + 0.3 * sr))
        if a >= n:
            break
        tt = t[a:b] - ci * bar
        env = np.minimum(1, tt / 0.6) * np.exp(-0.15 * tt)
        for f in chords[ci % 4]:
            for det in (-0.6, 0.6):
                pad[a:b] += np.sin(2 * np.pi * (f + det) * tt) * env * 0.06
    bright = np.where(t < hit, 0.55, 1.0)
    pad *= bright
    beat = 60 / 90
    sub = np.zeros(n)
    for k in range(int(L / beat)):
        a = int(k * beat * sr)
        m = min(n - a, int(0.45 * sr))
        tt = np.arange(m) / sr
        f = 55 * np.exp(-3 * tt) + 40
        sub[a:a + m] += np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-7 * tt) * \
            (0.35 if k * beat < hit else 0.55)
    hats = np.zeros(n)
    for k in range(int(L / (beat / 2))):
        tk = k * beat / 2
        if tk < hit or tk > end:
            continue
        a = int(tk * sr)
        m = min(n - a, int(0.05 * sr))
        hats[a:a + m] += rng.uniform(-1, 1, m) * np.exp(-90 * np.arange(m) / sr) * 0.05
    hats = np.diff(np.concatenate([[0], hats]))          # crude high-pass
    boom = np.zeros(n)
    for th, g in ((hit, 1.0), (end - 4.0 if end - 4.0 > hit + 3 else None, 0.8)):
        if th is None:
            continue
        a = int(th * sr)
        m = min(n - a, int(2.5 * sr))
        tt = np.arange(m) / sr
        f = 60 * np.exp(-2.5 * tt) + 30
        boom[a:a + m] += np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-1.8 * tt) * g
    y = pad + sub + hats + boom
    y[t > end - 0.2] *= np.linspace(1, 0, int((t > end - 0.2).sum()))
    y = y / (np.abs(y).max() + 1e-9) * 0.5
    tmp = path[:-4] + ".wav"
    ak.write_wav(tmp, y)
    ak.ff("-i", tmp, "-c:a", "libmp3lame", "-b:a", "256k", path)
    os.remove(tmp)


# ------------------------------------------------------------------ main
def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("--plan", default=PLAN)
    ap.add_argument("--init", action="store_true", help="write a default music_plan.json")
    ap.add_argument("--turn-word", help="the phrase the story turns on (as transcribed)")
    ap.add_argument("--turn-time", type=float, help="…or the turn as a time in seconds")
    ap.add_argument("--words", default="src/words.json")
    ap.add_argument("--voice", help="the raw voice / A-roll (default media.json aroll, assets/aroll.mp4)")
    ap.add_argument("--variants", help="comma list of presets, overrides the plan")
    ap.add_argument("--file", action="append", default=[],
                    help="a track you are licensed to use (repeatable) — skips generation")
    ap.add_argument("--procedural", action="store_true",
                    help="no key and no track: synthesise a quiet royalty-free bed")
    ap.add_argument("--reanalyse", action="store_true",
                    help="re-pick from the variants already in assets/bgm/variants/")
    ap.add_argument("--dry-run", action="store_true", help="print the requests and the estimate")
    ap.add_argument("--force", action="store_true", help="regenerate variants that exist")
    ap.add_argument("--stronger-turn", action="store_true",
                    help="the ONE retry after a weak turn: emphatic build/stop/drop instructions")
    ap.add_argument("--credits", action="store_true",
                    help="read the balance now and report the measured spend of logged runs")
    ap.add_argument("--max-offset", type=float,
                    help="largest trim allowed (default 3 s for generated tracks; for --file "
                         "anything that still leaves the track long enough)")
    a = ap.parse_args()
    if a.credits:
        key = ak.api_key()
        if not key:
            sys.exit("no ELEVENLABS_API_KEY in the project's .env or the environment")
        return ak.ledger_report(key)
    cfg = hfcfg.load(a.config)
    hfcfg.require("ffmpeg", "ffprobe")
    hfcfg.ensure_deps(["numpy"])

    words = ak.load_words(a.words)
    voice = ak.voice_path(a.voice)
    plan = json.load(open(a.plan, encoding="utf-8")) if os.path.exists(a.plan) and not a.init else {}
    speech_end, outro_start, end = timeline(cfg, plan, words, voice)
    turn, turn_label = turn_time(a, plan, words)

    if a.init or not plan:
        if os.path.exists(a.plan) and a.init and not a.force:
            sys.exit(f"{a.plan} exists — edit it, or pass --force to overwrite")
        plan = default_plan(words, speech_end, outro_start, end, turn, turn_label)
        if a.turn_word:
            plan["turn_word"] = a.turn_word
        json.dump(plan, open(a.plan, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        print(f"  wrote {a.plan}: turn {turn_label}, END {end:.2f}s"
              f"{f', outro {outro_start:.2f}s' if outro_start else ''}")
        for name, s0, s1 in section_bounds(plan):
            print(f"    {name:8s} {s0:6.2f} → {s1:6.2f}")
        print("  rewrite the section styles for THIS story, then run music.py again")
        if a.init or not (a.file or a.procedural):
            return 0            # never spend credits on a plan nobody has read
    lead = float(plan.get("lead", 0.5))
    if turn is None:
        print("  ! no turn word/time — the drop is aligned to nothing; pass --turn-word")
    os.makedirs(BGM, exist_ok=True)
    vdir = os.path.join(BGM, "variants")

    # ---------------------------------------------------------------- sources
    files, source = [], ""
    if a.file:
        files, source = [(os.path.splitext(os.path.basename(f))[0], f) for f in a.file], "user file"
        for _, f in files:
            if not os.path.exists(f):
                sys.exit(f"{f} not found")
    elif a.procedural:
        os.makedirs(vdir, exist_ok=True)
        p = os.path.join(vdir, "procedural.mp3")
        procedural(p, plan, turn, end, lead)
        files, source = [("procedural", p)], "procedural (no key)"
        print(f"  synthesised a quiet procedural bed → {p}")
    elif a.reanalyse:
        files = [(os.path.splitext(f)[0], os.path.join(vdir, f)) for f in sorted(os.listdir(vdir))
                 if f.endswith((".mp3", ".wav"))] if os.path.isdir(vdir) else []
        source = "existing variants"
        if not files:
            sys.exit(f"no variants in {vdir}/")
    else:
        variants = variant_list(plan, a.variants)
        strength = 2 if a.stronger_turn else plan.get("turn_strength", 1)
        cplan = dict(plan, turn_time=turn) if turn is not None else plan
        bodies = [(n, {"model_id": "music_v1",
                       "composition_plan": composition(cplan, st, strength)})
                  for n, st in variants]
        total = plan_seconds(bodies[0][1]["composition_plan"])
        need = int(math.ceil(total / 60 * CREDITS_PER_MIN)) * len(variants)
        if turn is not None and turn_index(cplan) is None:
            print(f"  ! no plan section starts on the turn ({turn:.2f}s): the build/stop/drop "
                  f"structure is not added — make a section boundary at the turn")
        if a.dry_run:
            for n, b in bodies:
                print(f"\n  variant {n}:\n" + json.dumps(b, indent=1, ensure_ascii=False))
            print(f"\n  {len(bodies)} × {total:.1f}s: the gate needs ≈{need:,} credits; "
                  f"expected spend ≈{int(math.ceil(total / 60 * CREDITS_PER_MIN_EST)) * len(bodies):,}")
            return 0
        key = ak.api_key()
        if not key:
            print("\n  No ElevenLabs key (ELEVENLABS_API_KEY in the project's .env or the "
                  "environment), so no music was generated. Options:\n"
                  "    1. a track you are licensed to use:   python3 scripts/music.py --file track.mp3\n"
                  "    2. a quiet royalty-free synth bed:    python3 scripts/music.py --procedural\n"
                  "    3. no music: skip bed.py — the reel keeps its voice and SFX only.\n"
                  "  Tell the user which one you used.")
            return 2
        todo = [n for n, _ in bodies
                if a.force or not os.path.exists(os.path.join(vdir, f"{n}.mp3"))]
        before = None
        if todo:
            need = int(math.ceil(total / 60 * CREDITS_PER_MIN)) * len(todo)
            before = ak.require_credits(key, need, f"{len(todo)} music variant(s) of {total:.0f}s")
        made = 0.0
        for n, body in bodies:                      # SEQUENTIAL: parallel requests 429
            out = os.path.join(vdir, f"{n}.mp3")
            if n not in todo:
                print(f"  variant {n}: exists ({out}), reusing (--force regenerates)")
            else:
                print(f"  generating variant {n} ({total:.1f}s)…", flush=True)
                ak.post(MUSIC_URL, body, out, key)
                made += ak.duration(out) or total
            files.append((n, out))
        if todo:                                    # never "credits used: 0" — see audiokit
            ak.spend_report(key, before, int(math.ceil(made / 60 * CREDITS_PER_MIN_EST)),
                            f"music: {len(todo)} variant(s), {made:.0f}s")
        source = "ElevenLabs Music (generated for this video)"

    # ---------------------------------------------------------------- analyse + pick
    need_until = outro_start if outro_start else end
    results = []
    for n, f in files:
        an = analyse(f)
        an["name"] = n
        # A generated track was planned around the turn, but the model places a section
        # change only roughly (measured: one variant on time, the other 5.5 s late), so a
        # generated track may be trimmed up to MAX_OFFSET_GEN s — choose_drop() pays for
        # distance and for any length bed.py must add. A user's song was not planned at
        # all — its best drop may be a minute in; trim as far as needed.
        mo = a.max_offset if a.max_offset is not None else \
            (MAX_OFFSET_GEN if not a.file else max(3.0, an["length"] - need_until - 1.0))
        an["choice"] = choose_drop(an, turn, lead, need_until, mo, end) if turn else None
        results.append(an)
        dr = ", ".join(f"{d['t']:.2f}s ({d['kind']} {d['lift_db']:+.1f} dB, strength "
                       f"{d['strength']:.1f})" for d in an["drops"]) or "none"
        print(f"  changes: {dr}")
        if an["strongest"]:
            print(f"  strongest: {an['strongest']['t']:.2f}s ({an['strongest']['lift_db']:+.1f} dB)")
        print(f"  intro silence {an['intro_silence']:.2f}s, natural end {an['natural_end']:.1f}s"
              f"{'   ✗ REJECTED: ' + an['reason'] if an['rejected'] else ''}")
    ok = [r for r in results if not r["rejected"]] or []
    if not ok:
        sys.exit("\n  ✗ every track was rejected (silent intro / dead gap) — regenerate "
                 "(--force), or pass a different --file")
    if turn:
        ok_c = [r for r in ok if r["choice"]]
        if not ok_c:
            print("\n  ! no drop in any track can land on the turn (it would need a negative "
                  "offset, or a longer track); using offset = intro silence. "
                  + ("Try another track, or align by ear with --turn-time." if a.file
                     else "Regenerate (--force), or move the turn."))
            pick = ok[0]
            offset, drop_t = pick["intro_silence"], None
        else:
            pick = max(ok_c, key=lambda r: r["choice"]["score"])
            offset, drop_t = pick["choice"]["offset"], pick["choice"]["t"]
    else:
        pick = ok[0]
        offset, drop_t = max(0.0, pick["intro_silence"] - 0.013), None

    dst = os.path.join(BGM, "music.mp3")
    if pick["file"].lower().endswith(".mp3"):
        if os.path.abspath(pick["file"]) != os.path.abspath(dst):
            shutil.copy2(pick["file"], dst)
    else:
        ak.ff("-i", pick["file"], "-vn", "-c:a", "libmp3lame", "-b:a", "320k", dst)
    nat_video = round(pick["natural_end"] - offset, 2)
    if pick["intro_silence"] - offset > 0.3:
        print(f"  ! after the trim the track still opens on {pick['intro_silence'] - offset:.2f}s "
              f"of near-silence — bed.py lifts the hook section, but a hook wants music "
              f"from frame 1 (regenerate with 'starts immediately' if it reads empty)")
    ref = outro_start if outro_start else end
    gate_ok, gate_lines = (turn_review(ok, pick, offset, turn) if turn is not None
                           else (True, []))
    if turn is not None and a.procedural:
        gate_ok = True              # the synth bed puts its own hit on the turn
    rep = {"source": source, "chosen": pick["name"], "music": dst, "offset": round(offset, 3),
           "drop_track_time": drop_t, "turn": turn_label, "turn_time": turn,
           "drop_lands_at": round(drop_t - offset, 3) if drop_t is not None else None,
           "turn_gate": {"lift_db": (pick.get("choice") or {}).get("lift_db"),
                         "min_db": TURN_MIN_DB, "passed": gate_ok,
                         "strength": 2 if a.stronger_turn else plan.get("turn_strength", 1)},
           "natural_end_video": nat_video, "outro_start": outro_start, "end": end,
           "ending_vs_outro": round(nat_video - ref, 2),
           "sections": [[n, round(s, 2), round(b, 2)] for n, s, b in section_bounds(plan)]
           if plan.get("sections") else [],
           "variants": results}
    json.dump(rep, open(REPORT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

    print(f"\n  ✓ picked '{pick['name']}' → {dst}")
    if drop_t is not None:
        print(f"    drop at {drop_t:.2f}s in the track; offset {offset:.3f}s → it lands at "
              f"{drop_t - offset:.2f}s, 0.06 s before the turn ({turn_label})")
    for ln in gate_lines:
        print("    " + ln)
    where = "the outro start" if outro_start else "the end"
    d = nat_video - ref
    print(f"    the track's natural ending lands at {nat_video:.2f}s, "
          f"{abs(d):.2f}s {'after' if d >= 0 else 'BEFORE'} {where} ({ref:.2f}s)")
    if nat_video < end - 0.3:
        print(f"    ! the music audibly ends {end - nat_video:.1f}s before the last frame — "
              f"bed.py extends it with a natural tail (a crossfaded loop of the last bar or "
              f"a reverb wash) and reports it; a smaller offset or a longer ring-out avoids it")
    print(f"    report → {REPORT}")
    if not gate_ok:
        retry = "" if a.stronger_turn else " --stronger-turn"
        est = int(math.ceil(len(ok) * (end + lead + TAIL_MIN) / 60 * CREDITS_PER_MIN_EST))
        print("\n  " + "=" * 74)
        print(f"  ✗ TURN GATE FAILED: the change landing on the turn is below "
              f"{TURN_MIN_DB:.0f} dB.")
        if a.file:
            print("    This is the user's track: try another one, or align a different "
                  "moment with --turn-time. Tell the user the turn is weak.")
        elif a.stronger_turn:
            print("    This was already the stronger-turn round. Do NOT regenerate again: keep "
                  "the best of\n    what you have, let bed.py's drop before the turn sell the "
                  "hit, and tell the user.")
        else:
            print(f"    Regenerate ONCE with stronger turn instructions (≈{est:,} credits):\n"
                  f"      python3 scripts/music.py --force{retry}\n"
                  f"    Never loop: if that round is weak too, keep the best result and say so.")
        print("    assets/bgm/music.mp3 was still written, so the edit can go on.")
        print("  " + "=" * 74)
        return 3
    print("  next: python3 scripts/bed.py --init && python3 scripts/bed.py")
    return 0


def selftest():
    """Negative tests — `music.py selftest`. Synthetic tracks in a temp folder, no key,
    no credits. Each one is a failure that shipped once. Exit 1 on failure."""
    import tempfile
    import numpy as np
    fails = []
    tmp = tempfile.mkdtemp(prefix="music_selftest_")
    rng = np.random.default_rng(3)

    def track(name, spans):
        """[(t0, t1, level_dB)] of white noise → a wav; uniform noise RMS is −4.8 dBFS."""
        n = int(spans[-1][1] * ak.SR)
        x = rng.uniform(-1, 1, n)
        g = np.zeros(n)
        for a0, a1, lv in spans:
            g[int(a0 * ak.SR):int(a1 * ak.SR)] = 10 ** ((lv + 4.8) / 20)
        p = os.path.join(tmp, name + ".wav")
        ak.write_wav(p, x * g)
        return p
    # 1. the round-2 failure: a big lift far from the turn, a +4.5 dB step inside a
    #    plateau on the turn → the step is chosen (it is all that can land), the report
    #    names the strongest change, and the GATE fails
    an = analyse(track("plateau", [(0, 8, -30), (8, 20, -12), (20, 30, -7.5)]), verbose=False)
    an["name"] = "plateau"
    an["choice"] = choose_drop(an, 19.5, 0.5, 25.0, 3.0, 30.0)
    if not an["strongest"] or abs(an["strongest"]["t"] - 8.0) > 0.3:
        fails.append(f"plateau: strongest change should be at 8 s, got {an['strongest']}")
    ok, lines = turn_review([an], an, an["choice"]["offset"] if an["choice"] else 0, 19.5)
    if ok or not any("WEAK TURN" in ln for ln in lines) \
            or not any("strongest change" in ln and "would land" in ln for ln in lines):
        fails.append(f"plateau: gate must FAIL and name the strongest change: {lines}")
    # 2. a filtered build, a stop, a full drop on turn + lead → chosen, gate passes
    an = analyse(track("built", [(0, 14, -14), (14, 19.5, -24), (19.5, 20.0, -70),
                                 (20.0, 30, -8)]), verbose=False)
    an["name"] = "built"
    an["choice"] = choose_drop(an, 19.5, 0.5, 25.0, 3.0, 30.0)
    ch = an["choice"]
    if not ch or abs(ch["t"] - 20.0) > 0.1:
        fails.append(f"built: the drop at 20.0 s should be chosen, got {ch}")
    else:
        ok, lines = turn_review([an], an, ch["offset"], 19.5)
        if not ok:
            fails.append(f"built: gate should pass: {lines}")
    # 3. the plan carries the turn structure, the ring-out, and no 'EDM drop' veto
    plan = {"turn_time": 19.5, "lead": 0.5, "tail": 2.0,
            "negative_global_styles": ["vocals", "EDM drop"],
            "sections": [{"name": "hook", "end": 6.0, "styles": ["a"]},
                         {"name": "tension", "end": 19.5, "styles": ["b"]},
                         {"name": "turn", "end": 26.0, "styles": ["c"]},
                         {"name": "outro", "end": 30.0, "styles": ["d"]}]}
    c = composition(plan, ["g"])
    secs = c["sections"]
    if not any("sudden stop" in x for x in secs[1]["positive_local_styles"]) \
            or "silence" in secs[1]["negative_local_styles"]:
        fails.append("plan: the section before the turn must end on a stop (and not veto silence)")
    if "sudden full drop on the very first beat" not in secs[2]["positive_local_styles"][0]:
        fails.append("plan: the turn section must START with the full drop instruction")
    if any(x.lower() == "edm drop" for x in c["negative_global_styles"]):
        fails.append("plan: 'EDM drop' must not be a global negative (it vetoes the drop)")
    if secs[-1]["section_name"] != "Ring out" or secs[-1]["duration_ms"] < TAIL_MIN * 1000 \
            or plan_seconds(c) < 30.0 + 0.5 + TAIL_MIN - 0.01:
        fails.append(f"plan: a ≥ {TAIL_MIN} s ring-out must follow END ({plan_seconds(c):.1f}s)")
    if "THE DROP" not in composition(plan, ["g"], 2)["sections"][2]["positive_local_styles"][0]:
        fails.append("plan: --stronger-turn must use the emphatic drop instruction")
    if turn_index(dict(plan, turn_time=12.0)) is not None:
        fails.append("plan: a turn that is not on a section boundary must not get structure")
    # 4. the spend report never claims 0 while the balance lags
    txt = " ".join(ak.spend_text(1714, 79460, 79460))
    if "≈1,714" not in txt or "lags" not in txt or "used: 0" in txt:
        fails.append(f"spend: a lagging balance must report the estimate + the lag: {txt}")
    if "1,700 so far" not in " ".join(ak.spend_text(1714, 79460, 77760)):
        fails.append("spend: a caught-up balance must report the measured change")
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    for f in fails:
        print("  ✗ " + f)
    print(f"  music selftest: {'all passed' if not fails else f'{len(fails)} failed'}")
    return 1 if fails else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["selftest"]:
        hfcfg.ensure_deps(["numpy"])
        sys.exit(selftest())
    sys.exit(main())
