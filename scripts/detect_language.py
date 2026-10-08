#!/usr/bin/env python3
"""Detect the spoken language of a take and write it into config.json — the first step.

    python3 scripts/detect_language.py raw.mp4              # print what it hears
    python3 scripts/detect_language.py raw.mp4 --apply      # …and write language.code +
                                                            #    direction into config.json
    python3 scripts/detect_language.py --set en             # the user's choice, no detection
    python3 scripts/detect_language.py selftest             # the gates, negative tests

WHY. Everything downstream depends on the language: which transcriber reads the words
(ivrit-ai for Hebrew, Whisper large-v3 otherwise), which caption rules apply (sticky
words, locked phrases, the Hebrew "AI" slab), and which way the whole layout reads
(right-to-left for Hebrew/Arabic/Persian/Urdu…, left-to-right otherwise). A user should
never have to edit a config to get their own language, so config.example.json ships
`language.code: "auto"` and xcheck.py / transcribe.py call this script automatically
before they transcribe anything.

HOW. Whisper's own language identification (faster-whisper `detect_language`) on the
first ~30 s of SPEECH: Silero VAD (bundled with faster-whisper) finds the speech, the
silences and music before it are dropped, and the speech is concatenated into one 30 s
window — the window Whisper's language head was trained on. Model: the best faster-whisper
model ALREADY on this machine (large-v3 → large-v3-turbo → medium → small), else `small`
(a 0.5 GB download, once): a Hebrew user's transcriber is ivrit-ai and should not have to
pull 3 GB of large-v3 just to be told "Hebrew", and small's language ID is reliable on a
clean single-speaker take — the confidence gate below catches the rest. `--model` forces
one. The result is cached by the file's fingerprint, so the second call (transcribe.py
after xcheck.py) costs nothing.

THE GATE. It never guesses silently. Below the confidence threshold (default 0.70) or
with less than 4 s of speech it writes NOTHING, prints the three most likely languages
with their probabilities and exits 2 — the skill then asks the user which one it is and
runs `--set <code>`. A mixed-language take (Hebrew with English terms) still scores
> 0.9 for its main language; a low score means something really is ambiguous.

Writes into config.json: language.code, language.direction (rtl for he/ar/fa/ur/yi/ps/
sd…, ltr otherwise) and language.detected {code, p, top3, speech_s, model, media}, which
the final report quotes. Every other key is kept.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

THRESHOLD = 0.70        # below this the user is asked, never guessed for
MIN_SPEECH = 4.0        # seconds of detected speech needed to say anything at all
WINDOW = 30.0           # Whisper's language head reads one 30 s window
SCAN = 150.0            # how much of the file is scanned for that much speech
DEFAULT_MODEL = "auto"
PREFERRED = ("large-v3", "large-v3-turbo", "medium", "small")
FALLBACK_MODEL = "small"
_HF_REPO = {"large-v3": "Systran/faster-whisper-large-v3",
            "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
            "medium": "Systran/faster-whisper-medium", "small": "Systran/faster-whisper-small"}


def pick_model(model=DEFAULT_MODEL, cache_dir=None):
    """`model` unless it is "auto": then the first of PREFERRED already in the Hugging Face
    cache, else FALLBACK_MODEL."""
    if model and model != "auto":
        return model
    hub = cache_dir or os.environ.get("HF_HUB_CACHE") or os.path.join(
        os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface"), "hub")
    for m in PREFERRED:
        d = os.path.join(hub, "models--" + _HF_REPO[m].replace("/", "--"), "snapshots")
        if os.path.isdir(d) and os.listdir(d):
            return m
    return FALLBACK_MODEL
CACHE_VERSION = 1

NAMES = {"he": "Hebrew", "en": "English", "ar": "Arabic", "fa": "Persian", "ur": "Urdu",
         "yi": "Yiddish", "ru": "Russian", "uk": "Ukrainian", "es": "Spanish",
         "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese", "nl": "Dutch",
         "pl": "Polish", "tr": "Turkish", "ro": "Romanian", "el": "Greek", "hu": "Hungarian",
         "cs": "Czech", "sv": "Swedish", "da": "Danish", "no": "Norwegian", "fi": "Finnish",
         "hi": "Hindi", "bn": "Bengali", "ta": "Tamil", "zh": "Chinese", "ja": "Japanese",
         "ko": "Korean", "vi": "Vietnamese", "th": "Thai", "id": "Indonesian",
         "ms": "Malay", "tl": "Tagalog", "sw": "Swahili", "am": "Amharic", "ps": "Pashto",
         "sd": "Sindhi", "ka": "Georgian", "hy": "Armenian", "az": "Azerbaijani"}


def name(code):
    return NAMES.get(hfcfg.lang_base(code), code)


# ================================================================= detection
def _speech_window(pcm, sr=16000, window=WINDOW):
    """The first `window` seconds of SPEECH (Silero VAD), concatenated, and how much speech
    the scanned audio holds. Falls back to the raw audio when the VAD is unavailable."""
    import numpy as np
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        ts = get_speech_timestamps(pcm, VadOptions(threshold=0.4, min_silence_duration_ms=300,
                                                   speech_pad_ms=150))
    except Exception:                                      # noqa: BLE001 — old/odd versions
        return pcm[: int(window * sr)], len(pcm) / sr
    speech = sum(t["end"] - t["start"] for t in ts) / sr
    out, have = [], 0
    for t in ts:
        take = min(t["end"] - t["start"], int(window * sr) - have)
        if take <= 0:
            break
        out.append(pcm[t["start"]: t["start"] + take])
        have += take
    return (np.concatenate(out) if out else pcm[:0]), speech


def _cache_path(media, model):
    import hashlib
    import transcribe
    key = json.dumps([CACHE_VERSION, transcribe.fingerprint(media), model, WINDOW, SCAN])
    d = os.path.join(hfcfg.home_dir(), "cache", "language")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, hashlib.sha1(key.encode()).hexdigest() + ".json")


def detect(media, model=DEFAULT_MODEL, use_cache=True, verbose=True):
    """{code, p, top3: [[code, p], ...], speech_s, model, media}. Needs faster-whisper
    (call hfcfg.ensure_deps(["faster_whisper"]) first). Uses transcribe.py's model cache,
    so a transcription in the same process reuses the loaded weights."""
    import numpy as np
    import transcribe
    import wave
    media = os.path.abspath(media)
    model = pick_model(model)
    cp = _cache_path(media, model)
    if use_cache and os.path.exists(cp):
        r = json.load(open(cp, encoding="utf-8"))
        if verbose:
            print(f"  language (cached): {r['code']} {name(r['code'])} p={r['p']:.2f}")
        return r
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "scan16k.wav")
        transcribe.extract_audio(media, wav, 0.0, SCAN if _duration(media) > SCAN else None)
        with wave.open(wav, "rb") as wf:
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    pcm = pcm.astype(np.float32) / 32768.0
    win, speech = _speech_window(pcm)
    res = {"code": "", "p": 0.0, "top3": [], "speech_s": round(speech, 1), "model": model,
           "media": media}
    if len(win) < 16000 * 1.0:
        return res
    if verbose:
        print(f"  detecting the language — {min(speech, WINDOW):.0f}s of speech, "
              f"Whisper {model} …", flush=True)
    m = transcribe._faster_model(model)
    if hasattr(m, "detect_language"):
        _lang, _p, probs = m.detect_language(audio=win, vad_filter=False,
                                             language_detection_segments=1)
    else:                                              # faster-whisper < 1.1
        _segs, info = m.transcribe(win, beam_size=1, vad_filter=False)
        probs = list(info.all_language_probs or [(info.language, info.language_probability)])
    probs = sorted(((str(c), float(p)) for c, p in probs), key=lambda x: -x[1])
    res["code"] = hfcfg.lang_base(probs[0][0]) if probs else ""
    res["p"] = round(probs[0][1], 4) if probs else 0.0
    res["top3"] = [[hfcfg.lang_base(c), round(p, 4)] for c, p in probs[:3]]
    json.dump(res, open(cp, "w", encoding="utf-8"))
    return res


def _duration(media):
    try:
        return float(hfcfg.probe(media) or 0.0)
    except ValueError:
        return 0.0


# ================================================================== the gate
def decide(res, threshold=THRESHOLD, min_speech=MIN_SPEECH):
    """(ok, message). ok only when there is enough speech AND the top language clears the
    threshold — otherwise the message lists the top 3 and the command that settles it."""
    top = ", ".join(f"{c} {name(c)} {p:.2f}" for c, p in res.get("top3") or []) or "nothing"
    if res.get("speech_s", 0.0) < min_speech or not res.get("code"):
        return False, (f"only {res.get('speech_s', 0.0):.1f}s of speech found — too little to "
                       f"tell the language (heard: {top}). Ask the user which language the "
                       f"take is in, then: detect_language.py --set <code>")
    if res["p"] < threshold:
        return False, (f"not sure which language this is (p {res['p']:.2f} < {threshold:.2f}). "
                       f"Most likely: {top}. Ask the user, then: detect_language.py --set <code>")
    return True, (f"{res['code']} ({name(res['code'])}), p {res['p']:.2f} — "
                  f"{hfcfg.direction_for(res['code'])}")


def config_path(cfg_arg=None):
    """The config.json to write: --config, else the project's ./config.json."""
    return os.path.abspath(cfg_arg or "config.json")


def apply(path, code, res=None, by="detected"):
    rec = {"by": by}
    if res:
        rec.update({k: res[k] for k in ("p", "top3", "speech_s", "model") if k in res})
        rec["media"] = os.path.basename(res.get("media", ""))
    L = hfcfg.write_language(path, code, extra={"detected": rec})
    print(f"  → {path}: language.code {L['code']}, direction {L['direction']}")
    return L


def ensure(media, cfg_arg=None, model=DEFAULT_MODEL, threshold=THRESHOLD, verbose=True):
    """In-process: when the config's language is still "auto", detect it on `media` and
    write it. Returns the resolved code; exits 2 (writing nothing) when unsure."""
    cfg = hfcfg.load(cfg_arg)
    if cfg["language"].get("resolved"):
        return cfg["language"]["code"]
    res = detect(media, model, verbose=verbose)
    ok, msg = decide(res, threshold)
    if not ok:
        print(f"  ✗ language: {msg}")
        sys.exit(2)
    print(f"  language: {msg}")
    apply(config_path(cfg_arg if cfg_arg else None), res["code"], res)
    return res["code"]


# ================================================================= selftest
def selftest():
    """The gate without a model: synthetic detection results. Exit 1 on any failure."""
    fails = []

    def want(nm, ok):
        print(f"  {'✓' if ok else '✗'} {nm}")
        if not ok:
            fails.append(nm)

    en = {"code": "en", "p": 0.98, "top3": [["en", 0.98], ["cy", 0.01], ["nl", 0.0]],
          "speech_s": 26.0}
    he = {"code": "he", "p": 0.93, "top3": [["he", 0.93], ["yi", 0.03], ["ar", 0.01]],
          "speech_s": 41.0}
    unsure = {"code": "pt", "p": 0.51, "top3": [["pt", 0.51], ["es", 0.4], ["gl", 0.05]],
              "speech_s": 30.0}
    short = {"code": "en", "p": 0.99, "top3": [["en", 0.99]], "speech_s": 1.5}
    want("a confident English take passes, ltr", decide(en)[0] and "ltr" in decide(en)[1])
    want("a confident Hebrew take passes, rtl", decide(he)[0] and "rtl" in decide(he)[1])
    ok, msg = decide(unsure)
    want("a low-confidence result is REFUSED and lists the top 3",
         not ok and "pt" in msg and "es" in msg and "--set" in msg)
    ok, msg = decide(short)
    want("too little speech is refused, even at p 0.99", not ok and "too little" in msg)
    want("no detection at all is refused", not decide({"code": "", "p": 0, "top3": [],
                                                       "speech_s": 0})[0])
    d = tempfile.mkdtemp(prefix="detect_selftest_")
    p = os.path.join(d, "config.json")
    json.dump({"language": {"code": "auto", "direction": "auto", "glossary": ["AI"]},
               "project": {"fps": 25}}, open(p, "w"))
    apply(p, "en", en)
    c = hfcfg.load(p)
    want("--apply writes code + direction + the record, keeps the rest",
         c["language"]["code"] == "en" and c["language"]["direction"] == "ltr"
         and c["language"]["detected"]["p"] == 0.98 and c["project"]["fps"] == 25
         and c["language"]["glossary"] == ["AI"])
    apply(p, "ar", None, by="user")
    c = hfcfg.load(p)
    want("--set ar → rtl, recorded as the user's choice",
         c["language"]["direction"] == "rtl" and c["language"]["detected"]["by"] == "user")
    hub = tempfile.mkdtemp(prefix="hub_")
    want("no model downloaded → the small fallback", pick_model("auto", hub) == "small")
    os.makedirs(os.path.join(hub, "models--Systran--faster-whisper-medium", "snapshots", "x"))
    want("a cached medium is preferred to downloading", pick_model("auto", hub) == "medium")
    os.makedirs(os.path.join(hub, "models--Systran--faster-whisper-large-v3", "snapshots", "y"))
    want("a cached large-v3 wins", pick_model("auto", hub) == "large-v3")
    want("an explicit --model is kept", pick_model("tiny", hub) == "tiny")
    # ensure() on a resolved config never re-detects (no model needed for this branch)
    want("ensure() returns the configured code without detecting", ensure("/nonexistent", p) == "ar")
    print(f"\n  detect_language selftest: {'ok' if not fails else f'{len(fails)} FAILED'}")
    return 1 if fails else 0


# ====================================================================== main
def main():
    if sys.argv[1:2] == ["selftest"] or "--selftest" in sys.argv[1:]:
        return selftest()
    ap = hfcfg.arg_parser(__doc__.split("\n\n")[0])
    ap.add_argument("media", nargs="?", help="the take (video or audio)")
    ap.add_argument("--apply", action="store_true", help="write the result into config.json")
    ap.add_argument("--set", metavar="CODE", help="write this language code (the user's "
                                                  "choice) without detecting")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="faster-whisper model (default "
                                                           "auto: the best one already "
                                                           "downloaded, else small)")
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    a = ap.parse_args()
    if a.set:
        code = a.set.strip().lower()
        if not code.replace("-", "").isalpha() or len(hfcfg.lang_base(code)) not in (2, 3):
            sys.exit(f"--set {a.set!r}: give a language code such as en, he, es, ar, pt-BR")
        apply(config_path(a.config), code, None, by="user")
        return 0
    if not a.media:
        ap.error("give the take to listen to, or --set <code>")
    hfcfg.require("ffmpeg", "ffprobe")
    hfcfg.ensure_deps(["faster_whisper", "numpy"])
    res = detect(a.media, a.model, use_cache=not a.force)
    ok, msg = decide(res, a.threshold)
    for c, p in res.get("top3") or []:
        print(f"    {c:4} {name(c):12} {p:.3f}")
    if not ok:
        print(f"  ✗ {msg}")
        return 2
    print(f"  ✓ language: {msg}")
    if a.apply:
        apply(config_path(a.config), res["code"], res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
