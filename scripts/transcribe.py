#!/usr/bin/env python3
"""Word-level transcription — free, local, any language (Hebrew gets its own model).

    python3 scripts/transcribe.py raw.mp4                       # → src/aroll.json + src/words.json
    python3 scripts/transcribe.py raw.mp4 --out build/raw.json  # → build/raw.json + build/raw_words.json
    python3 scripts/transcribe.py assets/aroll.mp4 --out src/aroll.json --words src/words.json
    python3 scripts/transcribe.py take1.mp4 take2.mp4 --out-dir src/transcripts   # model loads once
    python3 scripts/transcribe.py raw.mp4 --start 12 --end 24 --compare          # second opinion

WHY each choice:

* the language comes first: with config `language.code: "auto"` (the default) this script
  runs scripts/detect_language.py on the first file and writes the code + direction into
  config.json before anything is transcribed (it exits 2 and asks when it is unsure).
* engine `auto` picks by language. Hebrew → `ivrit-ai/whisper-large-v3-turbo-ct2` on
  faster-whisper: ivrit.ai's Hebrew fine-tune of Whisper. Vanilla Whisper mangles Hebrew
  (spelling, prefixes, names); this one reads it like a native. Other languages →
  `mlx-community/whisper-large-v3-mlx` via mlx-whisper on Apple Silicon (fast on the GPU),
  faster-whisper `large-v3` elsewhere. All free, all local.
* `scribe` = ElevenLabs Scribe (verbatim, word-level, audio events like "(laughs)"). A paid
  upgrade, used only when asked for AND `ELEVENLABS_API_KEY` is set. Never required.
* Runs IN-PROCESS with the model loaded once for every file. Never shell out to the
  mlx_whisper CLI: it names its output from the FIRST dot of the input name, and batching
  many files into one call writes every result to the first file's json.
* `condition_on_previous_text=False`: a raw take repeats the same line several times, and a
  model conditioned on its own previous text "helpfully" skips the repeat. The repeats are
  exactly what the editor needs to see (the LAST take wins).
* Cached by a fingerprint of the file's bytes + engine + model + span + glossary. An
  unchanged file is never transcribed twice. The cache holds the RAW engine output; the
  Hebrew post-pass below re-runs every time, so improving it never needs a re-transcribe.
* Hebrew post-pass: Whisper writes "AI" phonetically ("איי", "אי איי", "ליי", "באי ביי",
  "לאיה"). Those are normalised to the literal `AI`; a Hebrew prefix letter (ב/ל/ה/ו/ש/מ/כ)
  stays a separate token followed by "-AI", which `captions.py` merges into "ב-AI". Every
  substitution is printed and listed in `src/transcript_flags.md` so Claude confirms it
  against context — "איי" can also be a real word (islands).
* Low-probability words (< 0.5) and suspiciously long tokens (≥ 2 s, usually two takes
  merged) go into `src/transcript_flags.md` for review against context.

Outputs
  --out    whisper-style json {"segments":[{"start","end","text","words":[{"word","start",
           "end","probability"}]}], ...}   (what scripts/captions.py and pack_transcript.py read)
  --words  [[start, end, "word"], ...]       (what scripts/captions.py reads by default).
           Without --words it lands beside --out as <stem>_words.json; src/words.json
           (the caption source) is written only by the bare default invocation.
  --flags  markdown review list

Times are always in the SOURCE file's timebase, also with --start/--end.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import math
import os
import platform
import re
import sys
import tempfile
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

ENGINES = ("auto", "ivrit", "mlx", "faster", "scribe")
DEFAULT_MODEL = {
    "ivrit": "ivrit-ai/whisper-large-v3-turbo-ct2",
    "faster": "large-v3",
    "mlx": "mlx-community/whisper-large-v3-mlx",
    "scribe": "scribe_v1",
}
ENGINE_MODULE = {"ivrit": "faster_whisper", "faster": "faster_whisper",
                 "mlx": "mlx_whisper", "scribe": None}
CACHE_VERSION = 1
LOW_PROB = 0.5
LONG_TOKEN = 2.0
SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"

_MODELS = {}          # (engine, model) -> loaded model; one load per process


def say(msg, verbose=True):
    if verbose:
        print(msg, flush=True)


# ================================================================ engine choice

def apple_silicon():
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _module_available(mod):
    """Importable here, or in the skill venv (ensure_deps will re-exec into it)."""
    if hfcfg.has_module(mod):
        return True
    vp = hfcfg.venv_python()
    return bool(vp) and hfcfg.has_module(mod, vp)


def resolve_engine(engine="auto", lang="he", model=None):
    """(engine, model) after applying the auto rules and the config override.

    A model name that belongs to another engine (config.example.json once shipped an mlx
    repo) is ignored with a warning instead of crashing the loader with a cryptic error.
    The ivrit-ai model is a HEBREW fine-tune: asked for on another language it would read
    that language as Hebrew, so it is refused there (engine large-v3 instead).
    """
    engine = (engine or "auto").lower()
    model = (model or "").strip() or None
    if engine not in ENGINES:
        raise ValueError(f"unknown engine {engine!r} — one of {', '.join(ENGINES)}")
    hebrew = hfcfg.is_hebrew(lang or "")
    if engine == "ivrit" and not hebrew:
        print(f"  ! engine ivrit is a Hebrew model and the language is {lang!r} — using "
              f"Whisper large-v3 instead", file=sys.stderr)
        engine = "auto"
    if engine == "auto":
        if model and "mlx" in model.lower():
            engine = "mlx"
        elif hebrew:
            engine = "ivrit"
        elif apple_silicon() and _module_available("mlx_whisper"):
            engine = "mlx"
        else:
            engine = "faster"
    if model:
        is_mlx = "mlx" in model.lower()
        if (engine == "mlx") != is_mlx or engine == "scribe":
            if engine != "scribe":
                print(f"  ! model {model!r} does not fit engine {engine!r} — "
                      f"using {DEFAULT_MODEL[engine]}", file=sys.stderr)
            model = None
    return engine, model or DEFAULT_MODEL[engine]


# ============================================================ glossary script
#
# WHY. Whisper's initial prompt is read as "the text so far": a prompt written in another
# script pulls the decoder toward that language — a Hebrew glossary term ("קלוד קוד",
# shipped in the example config) in front of an English take makes Whisper drift into
# Hebrew or transliterate. So the prompt keeps only terms in the language's own script,
# plus Latin (brand names, "AI", "Claude Code" are Latin in every language).

LANG_SCRIPT = {"he": "hebrew", "yi": "hebrew", "ar": "arabic", "fa": "arabic",
               "ur": "arabic", "ps": "arabic", "sd": "arabic", "ug": "arabic",
               "ru": "cyrillic", "uk": "cyrillic", "bg": "cyrillic", "sr": "cyrillic",
               "mk": "cyrillic", "be": "cyrillic", "kk": "cyrillic", "mn": "cyrillic",
               "el": "greek", "zh": "han", "ja": "han", "ko": "hangul", "hi": "devanagari",
               "mr": "devanagari", "ne": "devanagari", "th": "thai", "ka": "georgian",
               "hy": "armenian", "bn": "bengali", "ta": "tamil", "am": "ethiopic"}
_SCRIPT_RANGES = [("hebrew", 0x0590, 0x05FF), ("arabic", 0x0600, 0x06FF),
                  ("arabic", 0x0750, 0x077F), ("arabic", 0xFB50, 0xFDFF),
                  ("arabic", 0xFE70, 0xFEFF), ("cyrillic", 0x0400, 0x052F),
                  ("greek", 0x0370, 0x03FF), ("armenian", 0x0530, 0x058F),
                  ("devanagari", 0x0900, 0x097F), ("bengali", 0x0980, 0x09FF),
                  ("tamil", 0x0B80, 0x0BFF), ("thai", 0x0E00, 0x0E7F),
                  ("georgian", 0x10A0, 0x10FF), ("ethiopic", 0x1200, 0x137F),
                  ("hangul", 0xAC00, 0xD7AF), ("han", 0x3040, 0x30FF),
                  ("han", 0x4E00, 0x9FFF)]


def script_of(term):
    """The writing system of the first letter in `term` ("latin" for A-Z and Latin
    accents), or None for a term with no letters (a number)."""
    for ch in str(term):
        if not ch.isalpha():
            continue
        o = ord(ch)
        for nm, a, b in _SCRIPT_RANGES:
            if a <= o <= b:
                return nm
        return "latin" if o < 0x0250 or 0x1E00 <= o <= 0x1EFF else "other"
    return None


def glossary_for(terms, lang):
    """(kept, dropped): the glossary terms that may go into this language's prompt."""
    own = LANG_SCRIPT.get(hfcfg.lang_base(lang), "latin")
    kept, dropped = [], []
    for t in terms:
        sc = script_of(t)
        (kept if sc in (None, "latin", own) else dropped).append(t)
    return kept, dropped


def env_key(name):
    """An API key from the environment, or from a .env in the project or the skill."""
    if os.environ.get(name):
        return os.environ[name].strip()
    for d in (os.getcwd(), hfcfg.SKILL_DIR, hfcfg.home_dir()):
        p = os.path.join(d, ".env")
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() == name:
                    return v.strip().strip('"').strip("'")
    return ""


# ===================================================================== caching

def fingerprint(path):
    """sha1 of size + first and last 8 MB. Hashing a 4 GB raw in full costs seconds on
    every call; any re-export or trim changes the size or the head/tail bytes anyway."""
    h = hashlib.sha1()
    size = os.path.getsize(path)
    h.update(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(8 << 20))
        if size > 16 << 20:
            f.seek(-(8 << 20), os.SEEK_END)
            h.update(f.read())
    return h.hexdigest()


def cache_file(path, engine, model, lang, start, end, prompt, vad):
    key = json.dumps([CACHE_VERSION, fingerprint(path), engine, model, lang,
                      start, end, prompt, vad], ensure_ascii=False)
    d = os.path.join(hfcfg.home_dir(), "cache", "transcripts")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")


# ======================================================================= audio

def extract_audio(src, dst, start=None, end=None):
    """16 kHz mono PCM, the rate every Whisper variant resamples to anyway."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", src]
    if end is not None:
        cmd += ["-t", f"{end - (start or 0.0):.3f}"]
    cmd += ["-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst]
    r = hfcfg.run(cmd)
    if r.returncode or not os.path.exists(dst) or os.path.getsize(dst) < 1000:
        raise RuntimeError(f"could not read audio from {src}: {r.stderr.strip()[-400:]}")


# ===================================================================== engines

def _need(mod):
    if not hfcfg.has_module(mod):
        raise RuntimeError(f"python package {mod!r} is missing in this interpreter — run "
                           f"`python3 {os.path.join(hfcfg.SKILL_DIR, 'scripts', 'doctor.py')}"
                           f" --install`, or call hfcfg.ensure_deps([{mod!r}]) first")


def _faster_model(model):
    """faster-whisper (CTranslate2). On a Mac it runs on the CPU — int8 is ~2x faster than
    float32 there with no audible loss on Hebrew; CUDA gets float16."""
    if ("faster", model) in _MODELS:
        return _MODELS[("faster", model)]
    _need("faster_whisper")
    from faster_whisper import WhisperModel
    device, compute = "cpu", "int8"
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            device, compute = "cuda", "float16"
    except Exception:
        pass
    m = WhisperModel(model, device=device, compute_type=compute,
                     cpu_threads=max(1, min(os.cpu_count() or 4, 8)))
    _MODELS[("faster", model)] = m
    return m


def _run_faster(wav, model, lang, prompt, hotwords, vad, duration, verbose):
    m = _faster_model(model)
    kw = dict(language=lang or None, word_timestamps=True, beam_size=5,
              condition_on_previous_text=False, initial_prompt=prompt or None,
              hotwords=hotwords or None, vad_filter=bool(vad))
    if vad:
        # padded generously and with a low threshold: a quiet aborted take is editorial
        # signal, and VAD must not swallow it
        kw["vad_parameters"] = {"threshold": 0.35, "min_silence_duration_ms": 500,
                                "speech_pad_ms": 400}
    # hand faster-whisper the samples, not the path: its own decoder goes through PyAV,
    # and a fresh install can pair it with a PyAV release whose API it does not match
    # (av 19 rejects the `metadata_errors` argument faster-whisper 1.2.1 passes). ffmpeg
    # already wrote 16 kHz mono PCM, so reading it with `wave` is exact and dependency-free.
    import wave
    import numpy as np
    with wave.open(wav, "rb") as wf:
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    segments, _info = m.transcribe(pcm.astype(np.float32) / 32768.0, **kw)
    out, next_pct = [], 10
    for s in segments:                         # a generator: decoding happens here
        words = [{"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3),
                  "probability": round(w.probability, 3)} for w in (s.words or [])]
        out.append({"start": round(s.start, 3), "end": round(s.end, 3),
                    "text": s.text.strip(), "words": words})
        pct = int(100 * s.end / max(duration, 1e-6))
        if verbose and pct >= next_pct and pct < 100:
            print(f"    … {pct}%", flush=True)
            next_pct = (pct // 10 + 1) * 10
    return out


def _run_mlx(wav, model, lang, prompt):
    """mlx-whisper keeps the last loaded model in its own ModelHolder, so calling it per
    file in one process loads the weights once."""
    _need("mlx_whisper")
    import mlx_whisper
    r = mlx_whisper.transcribe(wav, path_or_hf_repo=model, language=lang or None,
                               word_timestamps=True, condition_on_previous_text=False,
                               initial_prompt=prompt or None, verbose=None)
    out = []
    for s in r.get("segments", []):
        words = [{"word": str(w["word"]).strip(), "start": round(float(w["start"]), 3),
                  "end": round(float(w["end"]), 3),
                  "probability": round(float(w.get("probability", 1.0)), 3)}
                 for w in s.get("words", [])]
        out.append({"start": round(float(s["start"]), 3), "end": round(float(s["end"]), 3),
                    "text": str(s.get("text", "")).strip(), "words": words})
    return out


def _multipart(fields, file_field, file_path):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'
                     f'{v}\r\n'.encode())
    with open(file_path, "rb") as f:
        data = f.read()
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
                 f'filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
                 + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _run_scribe(wav, model, lang):
    """ElevenLabs Scribe over plain urllib (no `requests` dependency). Its flat word list
    is regrouped into segments on a 0.5 s gap or sentence-ending punctuation; audio
    events go to a separate list so they never end up in a caption."""
    import urllib.error
    import urllib.request
    key = env_key("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("engine 'scribe' needs ELEVENLABS_API_KEY (environment or .env). "
                           "It is a paid upgrade — the free default is --engine auto.")
    fields = {"model_id": model, "timestamps_granularity": "word",
              "tag_audio_events": "true", "diarize": "true"}
    if lang:
        fields["language_code"] = lang
    body, ctype = _multipart(fields, "file", wav)
    req = urllib.request.Request(SCRIBE_URL, data=body, method="POST",
                                 headers={"xi-api-key": key, "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Scribe returned {e.code}: {e.read()[:400]!r}")
    segs, cur, events = [], [], []
    for w in data.get("words", []):
        t = w.get("type", "word")
        if t == "spacing" or w.get("start") is None:
            continue
        text = str(w.get("text", "")).strip()
        if t == "audio_event":
            events.append({"start": round(w["start"], 3), "end": round(w["end"], 3),
                           "text": text if text.startswith("(") else f"({text})"})
            continue
        lp = w.get("logprob")
        word = {"word": text, "start": round(w["start"], 3), "end": round(w["end"], 3),
                "probability": round(math.exp(lp), 3) if lp is not None else 1.0}
        if w.get("speaker_id") is not None:
            word["speaker"] = w["speaker_id"]
        if cur and word["start"] - cur[-1]["end"] >= 0.5:
            segs.append(cur)
            cur = []
        cur.append(word)
        if text.endswith((".", "?", "!", "…")):
            segs.append(cur)
            cur = []
    if cur:
        segs.append(cur)
    out = [{"start": s[0]["start"], "end": s[-1]["end"],
            "text": " ".join(w["word"] for w in s), "words": s} for s in segs]
    return out, events


# ========================================================== Hebrew post-pass

_PUNCT = "\"'.,?!…:;)("
_PRE = "[ובהלכמש]{0,3}?"
# one token: optional prefix letters, optional hyphen/maqaf, then a phonetic "AI"
_AI_ONE = re.compile(
    r"^(?P<pre>" + _PRE + r")[-־]?"
    r"(?P<core>אי[-־.]?איי|איי[-־.]?איי|אייאי|איאי|איי|A\.I|AI|Ai|ai)"
    r"(?P<suf>[" + re.escape(_PUNCT) + r"]*)$")
# whole-token oddities heard in real takes, prefix included
_AI_ODD = {"ליי": "ל", "לאיה": "ל", "לאייה": "ל", "באייה": "ב", "האייה": "ה"}
# two tokens: "אי איי", "איי איי", "באי ביי", "לאי איי"
_AI_HEAD = re.compile(r"^(?P<pre>" + _PRE + r")(?:אי|איי)$")
_AI_TAIL = re.compile(r"^(?:איי|אי|ביי|יי)(?P<suf>[" + re.escape(_PUNCT) + r"]*)$")
# forms that are also ordinary Hebrew words: substitute, but mark for a human check
_AMBIGUOUS = {"איי", "ואיי", "מאיי", "האיי"}


def _split_prefixed(w, pre, suf, orig):
    """"ב" + "-AI": the prefix keeps a sliver of the word's time, AI takes the rest.
    captions.py merges a token that starts with "-" into the one before it."""
    s, e = w["start"], w["end"]
    cut = round(min(s + 0.08, s + (e - s) * 0.25), 3)
    p = w.get("probability", 1.0)
    if not pre:
        return [{"word": "AI" + suf, "start": s, "end": e, "probability": p, "orig": orig}]
    return [{"word": pre, "start": s, "end": cut, "probability": p, "orig": orig},
            {"word": "-AI" + suf, "start": cut, "end": e, "probability": p, "orig": orig}]


def normalise_ai(segments):
    """Rewrite phonetic "AI" in place; return the list of substitutions."""
    subs = []
    flat = [w for s in segments for w in s["words"]]

    def ctx(i):
        return " ".join(x["word"] for x in flat[max(0, i - 4):i + 5])

    pos = 0
    for seg in segments:
        words, out, i = seg["words"], [], 0
        while i < len(words):
            w = words[i]
            raw = w["word"].strip()
            core = raw.strip(_PUNCT)
            suf = raw[len(raw.rstrip(_PUNCT)):]
            # two-token form, inside one segment and close together
            if i + 1 < len(words):
                n = words[i + 1]
                mh, mt = _AI_HEAD.match(core), _AI_TAIL.match(n["word"].strip())
                if mh and mt and n["start"] - w["end"] < 0.35:
                    orig = f"{raw} {n['word'].strip()}"
                    merged = {"start": w["start"], "end": n["end"],
                              "probability": min(w.get("probability", 1), n.get("probability", 1))}
                    new = _split_prefixed(merged, mh.group("pre"), mt.group("suf"), orig)
                    subs.append({"t": w["start"], "orig": orig,
                                 "new": "".join(x["word"] for x in new),
                                 "check": False, "context": ctx(pos + i)})
                    out.extend(new)
                    i += 2
                    continue
            m = _AI_ONE.match(raw)
            pre = None
            if m and m.group("core"):
                pre, suf2 = m.group("pre"), m.group("suf")
            elif core in _AI_ODD:
                pre, suf2 = _AI_ODD[core], suf
            if pre is not None and raw not in ("AI",) and not raw.startswith("-AI"):
                new = _split_prefixed(w, pre, suf2, raw)
                if "".join(x["word"] for x in new) != raw:
                    subs.append({"t": w["start"], "orig": raw,
                                 "new": "".join(x["word"] for x in new),
                                 "check": core in _AMBIGUOUS, "context": ctx(pos + i)})
                    out.extend(new)
                    i += 1
                    continue
            out.append(w)
            i += 1
        pos += len(words)
        seg["words"] = out
        seg["text"] = join_words([x["word"] for x in out])
    return subs


_GERESH_HEAD = re.compile(r"^[ובהלכמש]{0,3}[גזצ]$")


def merge_geresh(segments):
    """Re-join a word Whisper's tokenizer split at its geresh: "ג" + "'מיני" → "ג'מיני"
    (Gemini), "צ" + "'אט" → "צ'אט". Left split, the caption builder would print the
    letter and the rest as two words with a space between them."""
    merged = []
    for seg in segments:
        out = []
        for w in seg["words"]:
            if (out and w["word"][:1] in ("'", "׳", "’") and _GERESH_HEAD.match(out[-1]["word"])
                    and w["start"] - out[-1]["end"] < 0.15):
                prev = out[-1]
                orig = f"{prev['word']} {w['word']}"
                prev["word"] = prev["word"] + w["word"]
                prev["end"] = w["end"]
                prev["probability"] = min(prev.get("probability", 1), w.get("probability", 1))
                merged.append({"t": prev["start"], "orig": orig, "new": prev["word"]})
                continue
            out.append(w)
        seg["words"] = out
        seg["text"] = join_words([x["word"] for x in out])
    return merged


def join_words(tokens):
    """Space-join, but glue a "-AI" style token onto its prefix ("ב" + "-AI" → "ב-AI")."""
    out = ""
    for t in tokens:
        if out and t.startswith("-") and len(t) > 1 and not out.endswith(" "):
            out += t
        else:
            out += (" " if out else "") + t
    return out


def find_flags(segments, threshold=LOW_PROB):
    flat = [w for s in segments for w in s["words"]]
    low, long_ = [], []
    for i, w in enumerate(flat):
        c = join_words([x["word"] for x in flat[max(0, i - 4):i + 5]])
        if "orig" not in w and w.get("probability", 1.0) < threshold:
            low.append({"t": w["start"], "word": w["word"],
                        "p": w.get("probability", 1.0), "context": c})
        if w["end"] - w["start"] >= LONG_TOKEN:
            long_.append({"t": w["start"], "end": w["end"], "word": w["word"], "context": c})
    return low, long_


# ===================================================================== public

def transcribe(path, lang="he", engine="auto", model=None, glossary=None, start=None,
               end=None, vad=True, use_cache=True, verbose=True):
    """Transcribe one media file → whisper-style dict (source timebase).

    Call it repeatedly in one process: the model is loaded once and reused. The result
    carries `substitutions` (AI normalisation), `flags` and `long_tokens` for review.
    """
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    engine, model = resolve_engine(engine, lang, model)
    terms = [t.strip() for t in (glossary or []) if t and t.strip()]
    if (lang or "").startswith("he") and "AI" not in terms:
        terms = ["AI"] + terms
    prompt = ", ".join(terms) if terms else ""
    cf = cache_file(path, engine, model, lang, start, end, prompt, bool(vad))

    t0 = time.time()
    events, cached = [], False
    if use_cache and os.path.exists(cf):
        raw = json.load(open(cf, encoding="utf-8"))
        segments, events, cached = raw["segments"], raw.get("events", []), True
        dur = raw.get("duration", 0.0)
        say(f"  cached: {os.path.basename(path)} ({engine})", verbose)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "audio16k.wav")          # dot-free, ASCII name
            extract_audio(path, wav, start, end)
            dur = os.path.getsize(wav) / 32000.0
            say(f"  transcribing {os.path.basename(path)} — {dur:.1f}s with {engine} "
                f"({model})", verbose)
            if engine in ("ivrit", "faster"):
                segments = _run_faster(wav, model, lang, prompt, prompt, vad, dur, verbose)
            elif engine == "mlx":
                segments = _run_mlx(wav, model, lang, prompt)
            else:
                segments, events = _run_scribe(wav, model, lang)
        off = float(start or 0.0)
        if off:
            for s in segments:
                s["start"], s["end"] = round(s["start"] + off, 3), round(s["end"] + off, 3)
                for w in s["words"]:
                    w["start"], w["end"] = round(w["start"] + off, 3), round(w["end"] + off, 3)
            for e in events:
                e["start"], e["end"] = round(e["start"] + off, 3), round(e["end"] + off, 3)
        segments = [s for s in segments if s["words"]]
        json.dump({"segments": segments, "events": events, "duration": dur},
                  open(cf, "w", encoding="utf-8"), ensure_ascii=False)
    elapsed = time.time() - t0

    hebrew = (lang or "").startswith(("he", "iw"))
    merges = merge_geresh(segments) if hebrew else []
    subs = normalise_ai(segments) if hebrew else []
    low, long_ = find_flags(segments)
    result = {
        "source": path, "language": lang, "engine": engine, "model": model,
        "span": [start, end] if (start or end) else None,
        "text": " ".join(s["text"] for s in segments),
        "segments": segments, "events": events,
        "substitutions": subs, "merges": merges, "flags": low, "long_tokens": long_,
        "cached": cached, "seconds": round(elapsed, 2), "audio_seconds": round(dur, 2),
    }
    if verbose and not cached and dur:
        say(f"  done in {elapsed:.1f}s — {elapsed / dur * 60:.1f}s per minute of audio", verbose)
    return result


def words_list(result):
    return [[w["start"], w["end"], w["word"]] for s in result["segments"] for w in s["words"]]


def write_outputs(result, out=None, words=None):
    for p in (out, words):
        if p:
            os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    if out:
        json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if words:
        json.dump(words_list(result), open(words, "w", encoding="utf-8"), ensure_ascii=False)


def flags_markdown(results):
    """One review list for Claude: confirm each AI substitution, check each doubtful word
    against its context, re-transcribe each long token's slice in isolation."""
    lines = ["# Transcript review", "",
             "Generated by scripts/transcribe.py. Confirm every item against context before",
             "captioning; fix real errors in config.json → language.typos.", ""]
    for r in results:
        lines.append(f"## {os.path.basename(r['source'])}  ({r['engine']}, {r['model']})")
        lines.append("")
        if r["substitutions"]:
            lines.append("### \"AI\" substitutions — confirm each one")
            for s in r["substitutions"]:
                mark = "  ← CHECK: also a real Hebrew word" if s["check"] else ""
                lines.append(f"- [{s['t']:07.2f}] `{s['orig']}` → `{s['new']}`  —  "
                             f"{s['context']}{mark}")
            lines.append("")
        if r["flags"]:
            lines.append(f"### Low-confidence words (p < {LOW_PROB})")
            for f in r["flags"]:
                lines.append(f"- [{f['t']:07.2f}] **{f['word']}** (p={f['p']:.2f})  —  "
                             f"{f['context']}")
            lines.append("")
        if r["long_tokens"]:
            lines.append(f"### Tokens ≥ {LONG_TOKEN:.0f}s — usually two takes merged; "
                         f"re-transcribe the slice alone")
            for f in r["long_tokens"]:
                lines.append(f"- [{f['t']:07.2f}-{f['end']:07.2f}] **{f['word']}**  —  "
                             f"{f['context']}")
            lines.append("")
        if not (r["substitutions"] or r["flags"] or r["long_tokens"]):
            lines += ["_nothing flagged_", ""]
    return "\n".join(lines)


# ==================================================================== compare

def _norm_token(t):
    t = re.sub(r"[֑-ׇ]", "", t)                   # niqqud / cantillation
    return t.strip(_PUNCT + "-־").replace("־", "-").lower()


def compare(path, lang, engines, start=None, end=None, glossary=None, verbose=True):
    """Two engines on one span; print every place they disagree, with times.

    A word both engines agree on is very likely right; a disagreement is where a human
    (or Claude, reading the context) has to decide. Cheap QA for a doubtful Hebrew span.
    """
    if start is None and end is None:
        print("  note: no --start/--end — comparing the whole file (slow).")
    runs = [transcribe(path, lang, e, glossary=glossary, start=start, end=end,
                       verbose=verbose) for e in engines]
    a, b = (words_list(r) for r in runs)
    na, nb = [_norm_token(w[2]) for w in a], [_norm_token(w[2]) for w in b]
    sm = difflib.SequenceMatcher(None, na, nb, autojunk=False)
    diffs = [op for op in sm.get_opcodes() if op[0] != "equal"]
    ea, eb = runs[0]["engine"], runs[1]["engine"]
    agree = sum(i2 - i1 for tag, i1, i2, _, _ in sm.get_opcodes() if tag == "equal")
    print(f"\n  {ea}: {len(a)} words   {eb}: {len(b)} words   agree on {agree}")
    print(f"  {len(diffs)} disagreement(s):\n")
    for tag, i1, i2, j1, j2 in diffs:
        t = a[i1][0] if i1 < len(a) else (b[j1][0] if j1 < len(b) else 0.0)
        ta = " ".join(w[2] for w in a[i1:i2]) or "∅"
        tb = " ".join(w[2] for w in b[j1:j2]) or "∅"
        print(f"    [{t:07.2f}]  {ea}: {ta}")
        print(f"    {'':9s}  {eb}: {tb}\n")
    return runs, diffs


# ======================================================================= main

def output_paths(out=None, words=None):
    """(out, words) for a single-file run.

    src/words.json is THE caption source (the cut's words, with the intended spellings
    from xcheck.py). It used to be the --words default even when --out pointed
    elsewhere, so a QA re-transcription (`--out build/qa/x.json`) silently replaced the
    caption source with raw whisper text. Now the words file follows --out
    (<stem>_words.json beside it), and only the bare default invocation writes
    src/aroll.json + src/words.json."""
    if out is None and words is None:
        return "src/aroll.json", "src/words.json"
    if out is None:
        out = "src/aroll.json"
    if words is None:
        stem = os.path.splitext(out)[0]
        words = f"{stem}_words.json"
    return out, words


def selftest():
    """Language-dependent choices, no model needed — `transcribe.py selftest`."""
    fails = []

    def want(nm, ok):
        print(f"  {'✓' if ok else '✗'} {nm}")
        if not ok:
            fails.append(nm)

    kept, dropped = glossary_for(["AI", "Claude Code", "קלוד קוד", "2026"], "en")
    want("an English prompt drops the Hebrew-script term, keeps Latin and numbers",
         kept == ["AI", "Claude Code", "2026"] and dropped == ["קלוד קוד"])
    kept, dropped = glossary_for(["AI", "קלוד קוד"], "he")
    want("a Hebrew prompt keeps Hebrew and Latin", kept == ["AI", "קלוד קוד"] and not dropped)
    kept, dropped = glossary_for(["ChatGPT", "Привет", "ذكاء"], "ar")
    want("an Arabic prompt keeps Arabic + Latin, drops Cyrillic",
         kept == ["ChatGPT", "ذكاء"] and dropped == ["Привет"])
    want("Hebrew → the ivrit-ai engine", resolve_engine("auto", "he")[0] == "ivrit")
    eng, mdl = resolve_engine("auto", "en")
    want("English → a large-v3 engine, never ivrit", eng in ("mlx", "faster")
         and "large-v3" in mdl and "ivrit" not in mdl)
    want("an explicit ivrit request on English is refused (large-v3 instead)",
         resolve_engine("ivrit", "en")[0] != "ivrit")
    print(f"\n  transcribe selftest: {'ok' if not fails else f'{len(fails)} FAILED'}")
    return 1 if fails else 0


def main():
    if sys.argv[1:2] == ["selftest"]:
        return selftest()
    ap = hfcfg.arg_parser(__doc__.split("\n\n")[0])
    ap.add_argument("media", nargs="+", help="audio/video file(s)")
    ap.add_argument("--engine", choices=ENGINES, help="default: config language.transcriber")
    ap.add_argument("--lang", help="language code (default: config language.code; \"auto\" "
                                   "detects it and writes it into config.json)")
    ap.add_argument("--model", help="override the engine's model")
    ap.add_argument("--out", default=None,
                    help="whisper-style json (one file). Default: src/aroll.json")
    ap.add_argument("--words", default=None,
                    help="[[s,e,w]] list (one file). Default: <out stem>_words.json next to "
                         "--out; src/words.json only when neither --out nor --words is given")
    ap.add_argument("--out-dir", default="src/transcripts",
                    help="several files: one <name>.json each here")
    ap.add_argument("--flags", default=None,
                    help="review list (AI substitutions, doubtful words); \"\" to skip. "
                         "Default: src/transcript_flags.md, or <out stem>_flags.md with --out")
    ap.add_argument("--glossary", default="", help='"term1,term2" — brand names, jargon')
    ap.add_argument("--start", type=float, help="transcribe only from here (s)")
    ap.add_argument("--end", type=float, help="… to here (s)")
    ap.add_argument("--no-vad", action="store_true",
                    help="disable the voice-activity filter (faster-whisper engines)")
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    ap.add_argument("--compare", nargs="?", const="", metavar="A,B",
                    help="run two engines on the span and print disagreements "
                         "(default pair: auto + a second free engine)")
    a = ap.parse_args()
    cfg = hfcfg.load(a.config)
    L = cfg["language"]
    hfcfg.require("ffmpeg", "ffprobe")
    lang = (a.lang or "").strip().lower() or L.get("code") or "auto"
    if lang == "auto":
        # the first pipeline step resolves the language (scripts/detect_language.py): it
        # picks the engine below, so it has to be known before anything is transcribed
        hfcfg.ensure_deps(["faster_whisper", "numpy"])
        import detect_language
        lang = detect_language.ensure(a.media[0], a.config)
    engine = a.engine or L.get("transcriber") or "auto"
    model = a.model or L.get("whisper_model") or None
    glossary = list(L.get("glossary") or []) + [t for t in a.glossary.split(",") if t.strip()]
    glossary, dropped = glossary_for(glossary, lang)
    if dropped:
        print(f"  glossary: left out {len(dropped)} term(s) written in another script than "
              f"{lang} ({', '.join(dropped[:6])}) — a prompt in the wrong script pulls Whisper "
              f"toward that language")

    if a.compare is not None:
        names = [x.strip() for x in a.compare.split(",") if x.strip()]
        if not names:
            first, _ = resolve_engine(engine, lang, model)
            second = "mlx" if (apple_silicon() and _module_available("mlx_whisper")) else "faster"
            if second == first:
                second = "faster" if first != "faster" else "ivrit"
            names = [first, second]
        if len(names) != 2:
            sys.exit("--compare takes exactly two engines, e.g. --compare ivrit,mlx")
        mods = {ENGINE_MODULE[resolve_engine(n, lang)[0]] for n in names} - {None}
        hfcfg.ensure_deps(sorted(mods))
        compare(a.media[0], lang, names, a.start, a.end, glossary)
        return 0

    eng, mdl = resolve_engine(engine, lang, model)
    if ENGINE_MODULE[eng]:
        hfcfg.ensure_deps([ENGINE_MODULE[eng]])

    out_path, words_path = output_paths(a.out, a.words)
    if a.flags is None:
        # same rule as the words file: a side run never overwrites the project's review list
        a.flags = ("src/transcript_flags.md" if a.out is None
                   else f"{os.path.splitext(a.out)[0]}_flags.md")
    results = []
    for m in a.media:
        r = transcribe(m, lang, eng, mdl, glossary, a.start, a.end,
                       vad=not a.no_vad, use_cache=not a.force)
        results.append(r)
        if len(a.media) == 1:
            write_outputs(r, out_path, words_path)
            print(f"  → {out_path}")
            print(f"  → {words_path}  (word list)")
        else:
            stem = os.path.splitext(os.path.basename(m))[0]
            p = os.path.join(a.out_dir, f"{stem}.json")
            write_outputs(r, p)
            print(f"  → {p}")
        nw = sum(len(s["words"]) for s in r["segments"])
        print(f"    {nw} words, {len(r['segments'])} segments, "
              f"{len(r['substitutions'])} AI substitution(s), {len(r['flags'])} low-confidence")
        for s in r["substitutions"]:
            mark = "   ← check: also a real word" if s["check"] else ""
            print(f"      AI  [{s['t']:7.2f}] {s['orig']} → {s['new']}{mark}")
        for s in r.get("merges", []):
            print(f"      ׳   [{s['t']:7.2f}] {s['orig']} → {s['new']}")

    if a.flags:
        os.makedirs(os.path.dirname(os.path.abspath(a.flags)), exist_ok=True)
        open(a.flags, "w", encoding="utf-8").write(flags_markdown(results))
        print(f"  review list → {a.flags}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
