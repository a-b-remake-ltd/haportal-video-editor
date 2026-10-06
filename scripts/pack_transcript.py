#!/usr/bin/env python3
"""Pack word-level transcripts into ONE phrase-level reading view: takes_packed.md.

    python3 scripts/pack_transcript.py                         # src/transcripts/*.json + src/aroll.json
    python3 scripts/pack_transcript.py src/raw.json src/take2.json -o takes_packed.md
    python3 scripts/pack_transcript.py src/transcripts --silence 0.4

WHY: choosing takes ("the last take of a repeated line wins", references/cutting.md) is a
reading job. Raw word json costs ~10x the tokens and hides the structure; a list of
phrases with `[start-end]` times shows repeats, false starts and pauses at a glance while
keeping word-boundary precision (every line starts on a word start and ends on a word
end). Read this first; drill into a moment with timeline_view.py only at decision points.

A phrase breaks on any silence >= --silence seconds (default 0.5), on a speaker change,
and after a sentence-ending word (one candidate take per line). Silences are MEASURED on
the source audio with ffmpeg silencedetect whenever the source file is known: Whisper
stretches each word over the pause that follows it (a 0.7 s pause comes back as a 0.06 s
word gap), so its timestamps alone almost never show a silence. Around a measured
silence the line's times are clamped to the real speech edges. Words the transcriber was
unsure of (p < 0.5) carry a `[?]` mark; Scribe audio events appear inline as `(laughs)`.

Line format, one section per source file:

    ## raw.mp4  (duration: 35.1s, 8 phrases)
      [000.00-002.58] אביב רון שואל, אז מה אתם מרוויחים מזה?
      [003.24-005.10] אנחנו לא פועלים לשם שמיים.

Ported from browser-use/video-use helpers/pack_transcripts.py (MIT, Copyright (c) the
video-use authors); adapted to this skill's whisper-style json, the [[s,e,w]] list, and
Hebrew "ב" + "-AI" tokens.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

LOW_PROB = 0.5
SENT_END = (".", "?", "!", "…")


def fmt_t(s):
    return f"{s:06.2f}"


def fmt_dur(s):
    if s < 60:
        return f"{s:.1f}s"
    m = int(s // 60)
    return f"{m}m {s - m * 60:04.1f}s"


def load_tokens(path):
    """Any transcript this skill writes or reads → (name, [token dict]) in time order.

    token: {start, end, text, kind: word|event, p, speaker}
    Accepts: whisper-style {"segments":[{"words":[...]}]}, Scribe {"words":[{type,...}]},
    and the plain [[start, end, "word"], ...] list. Returns None for anything else.
    """
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (ValueError, OSError):
        return None
    toks = []
    name = os.path.splitext(os.path.basename(path))[0]
    meta = {"source": None, "span": None}
    if isinstance(d, dict) and "segments" in d:
        meta = {"source": d.get("source"), "span": d.get("span")}
        if d.get("source"):
            name = os.path.basename(d["source"])
            span = d.get("span")
            if span and any(x is not None for x in span):
                a, b = span
                name += f"  [{a or 0:.2f}-{b if b is not None else 0:.2f}]"
        for s in d["segments"]:
            for w in s.get("words", []):
                toks.append({"start": float(w["start"]), "end": float(w["end"]),
                             "text": str(w.get("word", "")).strip(), "kind": "word",
                             "p": float(w.get("probability", 1.0)),
                             "speaker": w.get("speaker")})
        for e in d.get("events", []) or []:
            toks.append({"start": float(e["start"]), "end": float(e["end"]),
                         "text": e["text"], "kind": "event", "p": 1.0, "speaker": None})
    elif isinstance(d, dict) and isinstance(d.get("words"), list):        # raw Scribe
        for w in d["words"]:
            t = w.get("type", "word")
            if t == "spacing" or w.get("start") is None:
                continue
            txt = str(w.get("text", "")).strip()
            if t == "audio_event" and not txt.startswith("("):
                txt = f"({txt})"
            toks.append({"start": float(w["start"]), "end": float(w.get("end", w["start"])),
                         "text": txt, "kind": "event" if t == "audio_event" else "word",
                         "p": 1.0, "speaker": w.get("speaker_id")})
    elif isinstance(d, list) and d and isinstance(d[0], (list, tuple)) and len(d[0]) >= 3:
        for a, b, w in (x[:3] for x in d):
            toks.append({"start": float(a), "end": float(b), "text": str(w).strip(),
                         "kind": "word", "p": 1.0, "speaker": None})
    else:
        return None
    toks = [t for t in toks if t["text"]]
    toks.sort(key=lambda t: (t["start"], t["end"]))
    return name, toks, meta


def measured_silences(media, min_dur, span=None, noise_db=-33.0):
    """[(start, end)] of real silences >= min_dur in the media (source timebase).
    Never pass -v error to ffmpeg here: it suppresses silencedetect's output."""
    if not media or not os.path.exists(media) or not hfcfg.which("ffmpeg"):
        return None
    a, b = (span or [None, None])[:2]
    cmd = ["ffmpeg", "-nostdin"]
    if a:
        cmd += ["-ss", f"{a:.3f}"]
    cmd += ["-i", media]
    if b is not None:
        cmd += ["-t", f"{b - (a or 0.0):.3f}"]
    cmd += ["-vn", "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"]
    txt = hfcfg.run(cmd).stderr
    off = float(a or 0.0)
    st = [float(x) + off for x in re.findall(r"silence_start: (-?[0-9.]+)", txt)]
    en = [float(x) + off for x in re.findall(r"silence_end: ([0-9.]+)", txt)]
    return list(zip(st, en))


def silence_breaks(toks, silences, max_dist=0.6):
    """Map each measured silence onto the word boundary it belongs to.

    Returns {k: (s0, s1)}: break AFTER token k. A boundary's position is the midpoint
    between word k's end and word k+1's start; the silence goes to the nearest boundary
    within `max_dist` of its midpoint (leading/trailing silences match nothing).
    """
    out = {}
    if not silences or len(toks) < 2:
        return out
    bounds = [((toks[k]["end"] + toks[k + 1]["start"]) / 2.0, k) for k in range(len(toks) - 1)]
    for s0, s1 in silences:
        mid = (s0 + s1) / 2.0
        d, k = min((abs(b - mid), k) for b, k in bounds)
        if d <= max_dist and (k not in out or (s1 - s0) > (out[k][1] - out[k][0])):
            out[k] = (s0, s1)
    return out


def join_tokens(texts):
    """Space-join; glue a "-AI" / "-100" token onto its Hebrew prefix ("ב" + "-AI")."""
    out = ""
    for t in texts:
        if out and t.startswith("-") and len(t) > 1:
            out += t
        else:
            out += (" " if out else "") + t
    for p in (",", ".", "?", "!"):
        out = out.replace(" " + p, p)
    return out


def group_phrases(toks, silence=0.5, mark_doubtful=True, breaks=None, sentences=True):
    """Phrases from tokens. `breaks` = {k: (s0, s1)} measured silences (break after token
    k); without them, fall back to the gap between word stamps. A speaker change and (with
    `sentences`) a sentence-ending word also end a phrase."""
    phrases, cur = [], []
    breaks = breaks if breaks is not None else None

    def flush(clamp_end=None):
        if not cur:
            return
        texts = []
        for t in cur:
            x = t["text"]
            if mark_doubtful and t["kind"] == "word" and t["p"] < LOW_PROB:
                x += "[?]"
            texts.append(x)
        end = cur[-1]["end"]
        if clamp_end is not None and cur[-1]["start"] < clamp_end < end:
            end = clamp_end
        phrases.append({"start": cur[0]["_start"], "end": end,
                        "text": join_tokens(texts), "speaker": cur[0]["speaker"]})

    for k, t in enumerate(toks):
        t = dict(t, _start=t["start"])
        if cur:
            prev = cur[-1]
            gap_break = (k - 1) in breaks if breaks is not None \
                else t["start"] - prev["end"] >= silence
            spk = (t["speaker"] is not None and prev["speaker"] is not None
                   and t["speaker"] != prev["speaker"])
            sent = sentences and prev["kind"] == "word" and prev["text"].endswith(SENT_END)
            if gap_break or spk or sent:
                s01 = breaks.get(k - 1) if breaks else None
                flush(s01[0] if s01 else None)
                cur = []
                if s01 and t["start"] < s01[1] < t["end"]:
                    t["_start"] = s01[1]          # real speech starts after the silence
        cur.append(t)
    flush()
    return phrases


def render(entries, silence):
    lines = ["# Packed transcripts", "",
             f"Phrase-level: a line ends at a silence ≥ {silence:.2f}s (measured on the audio), "
             "a speaker change or a sentence end. Times are in each SOURCE's timebase.",
             "`[?]` = the transcriber was unsure (p < 0.5): check it against context. "
             "The last take of a repeated line wins.", ""]
    for name, phrases in entries:
        dur = phrases[-1]["end"] - phrases[0]["start"] if phrases else 0.0
        lines.append(f"## {name}  (duration: {fmt_dur(dur)}, {len(phrases)} phrases)")
        if not phrases:
            lines += ["  _no speech detected_", ""]
            continue
        for p in phrases:
            spk = p.get("speaker")
            tag = ""
            if spk is not None:
                s = str(spk)
                tag = " S" + (s[len("speaker_"):] if s.startswith("speaker_") else s)
            lines.append(f"  [{fmt_t(p['start'])}-{fmt_t(p['end'])}]{tag} {p['text']}")
        lines.append("")
    return "\n".join(lines)


def collect(inputs):
    """Expand dirs to their *.json; default to src/transcripts/ + src/aroll.json."""
    if not inputs:
        inputs = sorted(glob.glob("src/transcripts/*.json"))
        if os.path.exists("src/aroll.json"):
            inputs.append("src/aroll.json")
    files = []
    for p in inputs:
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "*.json")))
        else:
            files.append(p)
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("inputs", nargs="*", help="transcript json files or directories")
    ap.add_argument("-o", "--out", default="takes_packed.md")
    ap.add_argument("--silence", type=float, default=0.5,
                    help="break a phrase on a pause this long (s). Default 0.5")
    ap.add_argument("--no-marks", action="store_true", help="omit the [?] doubt marks")
    ap.add_argument("--no-audio", action="store_true",
                    help="use word-stamp gaps only (skip measuring silences on the source)")
    ap.add_argument("--no-sentences", action="store_true",
                    help="do not break after sentence-ending punctuation")
    ap.add_argument("--noise", type=float, default=-33.0,
                    help="silencedetect level in dB (default -33; try -40 for a quiet take)")
    a = ap.parse_args()

    files = collect(a.inputs)
    if not files:
        sys.exit("no transcripts found — run scripts/transcribe.py first "
                 "(or pass the json files explicitly)")
    entries = []
    for f in files:
        got = load_tokens(f)
        if got is None:
            print(f"  skip {f} (not a transcript)")
            continue
        name, toks, meta = got
        breaks, how = None, "word gaps"
        if not a.no_audio:
            sil = measured_silences(meta.get("source"), a.silence, meta.get("span"), a.noise)
            if sil is not None:
                breaks, how = silence_breaks(toks, sil), f"{len(sil)} measured silences"
        print(f"  {name}: {len(toks)} words, phrase breaks from {how}")
        entries.append((name, group_phrases(toks, a.silence, not a.no_marks, breaks,
                                            not a.no_sentences)))
    if not entries:
        sys.exit("none of the inputs is a transcript")

    md = render(entries, a.silence)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, "w", encoding="utf-8").write(md)
    n = sum(len(p) for _, p in entries)
    print(f"packed {len(entries)} transcript(s) → {a.out}  ({n} phrases, "
          f"{os.path.getsize(a.out) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
