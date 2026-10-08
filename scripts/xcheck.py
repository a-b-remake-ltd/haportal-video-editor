#!/usr/bin/env python3
"""Two-model transcription cross-check, plus the correction file that fixes the caption text.

    python3 scripts/xcheck.py raw/take.mp4                  # both engines → diff → src/raw_words.json
    python3 scripts/xcheck.py raw/take.mp4 --apply-only     # re-apply src/corrections.json only
    python3 scripts/xcheck.py raw/take.mp4 --start 0 --end 30 --primary mlx

WHY TWO MODELS. One transcriber is confidently wrong in ways you cannot see from its own
output: a word it is sure of can still be the wrong word. Two different models rarely make
the SAME mistake, so a word both agree on is very likely right, and every disagreement is
exactly where a human (or Claude, reading the context) has to decide. That turns "proof-read
600 words" into "decide 10 places".

THE LANGUAGE FIRST. With config `language.code: "auto"` (the default) the take's language
is detected before anything else (scripts/detect_language.py --apply: code + direction into
config.json). Unsure → exit 2 and nothing is transcribed: ask the user, then
`detect_language.py --set <code>`. The engines follow the language:

  Hebrew
    engine A  ivrit-ai/whisper-large-v3-turbo-ct2 on faster-whisper — the Hebrew fine-tune
    engine B  mlx-community/whisper-large-v3-turbo on mlx-whisper (Apple Silicon), with the
              glossary as the initial prompt. Where mlx-whisper is missing it falls back to
              faster-whisper large-v3 (same idea, slower, CPU).
  any other language (ivrit-ai would read it as Hebrew)
    engine A  mlx-community/whisper-large-v3-mlx on mlx-whisper (Apple Silicon), else
              faster-whisper large-v3
    engine B  the other one: faster-whisper large-v3 — or, when A already is faster-whisper
              (no Apple Silicon), faster-whisper large-v3-turbo, a different model
  The glossary goes in as the initial prompt in the detected language's own script
  (transcribe.py drops terms written in another script).
  Both run through scripts/transcribe.py in their own process (so each engine's deps stay
  isolated, and the transcript cache means a re-run costs nothing). When a package is not
  importable here or in the skill venv but `uv` is installed, the engine runs under
  `uv run --with <package>` instead.

WHY A CORRECTION FILE. Captions MUST show the intended, correctly spelled words while the
TIMINGS come from the audio. Three kinds of fix are normal and expected:
  * the speaker (very often an AI avatar) mispronounced the script: the audio says one
    word, the script obviously meant another; the caption shows the intended word
  * colloquial pronunciation of a word that has a standard spelling: the caption uses the
    standard spelling
  * a transcriber slip that both engines did not share (that is what the diff surfaces)
Claude fills src/corrections.json after reading src/transcript_diff.md:

    {"12.34": "intended word",            a TIME key: the word spoken at 12.34 s
     "misheard word": "intended word",    a WORD/PHRASE key: every occurrence
     "two words": {"to": "fixed phrase", "why": "avatar mispronounced the script"},
     "33.10": "",                         an empty value deletes that word
     "_keep": ["d04", "41.20"]}           disagreements where the primary reading is right

The primary engine's words keep their timings; only text changes. A replacement with a
different word count splits the original time span by character length. Trailing
punctuation of the original survives (captions.py breaks cards on it).

GATES (exit 1):
  * a correction key that matches nothing (a typo in a key must never fail silently)
  * --strict: a disagreement that is neither corrected nor listed in "_keep"

Outputs
  src/xcheck/<engine>.json      each engine's raw transcript (whisper-style)
  src/transcript_diff.md        every disagreement with context + both readings + decision,
                                and every correction applied (for the final report)
  src/xcheck.json               the same as data (preflight_qa.py --checklist reads it)
  src/raw.json, src/raw_words.json   the primary transcript WITH the corrections applied —
                                what pack_transcript.py, cut_aroll.py and map_words.py read
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MLX_TURBO = "mlx-community/whisper-large-v3-turbo"
MLX_LARGE = "mlx-community/whisper-large-v3-mlx"
PUNCT_TAIL = re.compile(r"[.,?!…:;\"'”)]+$")
TIME_KEY = re.compile(r"^\d+(?:\.\d+)?$")


# ------------------------------------------------------------------ engines
def _python_for(module):
    """(argv prefix, how) that can import `module`, or (None, why)."""
    if hfcfg.has_module(module):
        return [sys.executable], "this python"
    vp = hfcfg.venv_python()
    if vp and hfcfg.has_module(module, vp):
        return [vp], "skill venv"
    uv = hfcfg.which("uv")
    if uv:
        pkg = {"mlx_whisper": "mlx-whisper", "faster_whisper": "faster-whisper"}[module]
        return [uv, "run", "--no-project", "--quiet", "--with", pkg, "--with", "numpy",
                "python"], f"uv run --with {pkg}"
    return None, f"{module} is not installed (python3 {HERE}/doctor.py --install)"


def plan_engines(want_b="mlx", lang="he"):
    """The two (tag, engine, model, argv-prefix, how) runs, chosen by language (see the
    module doc). The tag names the output files (src/xcheck/<tag>.json)."""
    out = []
    pa, how = _python_for("faster_whisper")
    mlx_ok = sys.platform == "darwin" and want_b == "mlx"
    pm, howm = _python_for("mlx_whisper") if mlx_ok else (None, "not on macOS")
    if hfcfg.is_hebrew(lang):
        if not pa:
            sys.exit(f"engine A (ivrit) cannot run: {how}")
        out.append(("ivrit", "ivrit", "ivrit-ai/whisper-large-v3-turbo-ct2", pa, how))
        if pm:
            out.append(("mlx", "mlx", MLX_TURBO, pm, howm))
            return out
        if mlx_ok:
            print(f"  ! mlx-whisper unavailable ({howm}) — engine B falls back to faster-whisper "
                  f"large-v3")
        out.append(("faster", "faster", "large-v3", pa, how))
        return out
    # any other language: large-v3 on both engines, never the Hebrew fine-tune
    if not pa and not pm:
        sys.exit(f"no transcriber can run: {how}")
    if pm:
        out.append(("mlx", "mlx", MLX_LARGE, pm, howm))
        if pa:
            out.append(("faster", "faster", "large-v3", pa, how))
            return out
        sys.exit(f"engine B (faster-whisper) cannot run: {how}")
    if mlx_ok:
        print(f"  ! mlx-whisper unavailable ({howm}) — both engines run on faster-whisper "
              f"(large-v3 + large-v3-turbo)")
    out.append(("faster", "faster", "large-v3", pa, how))
    out.append(("turbo", "faster", "large-v3-turbo", pa, how))
    return out


def resolve_language(a, media):
    """The first pipeline step: settle config language.code before choosing engines.

    "auto" → scripts/detect_language.py --apply, run under a python that has faster-whisper
    (this script itself stays stdlib-only). Exit 2 from it = unsure: stop here and ask."""
    cfg = hfcfg.load(a.config)
    if a.lang:
        return hfcfg.lang_base(a.lang) if hfcfg.is_hebrew(a.lang) else a.lang.lower()
    if cfg["language"].get("resolved"):
        return cfg["language"]["code"]
    py, how = _python_for("faster_whisper")
    if not py:
        sys.exit(f"cannot detect the language: {how}")
    cmd = py + [os.path.join(HERE, "detect_language.py"), media, "--apply"]
    if a.config:
        cmd += ["--config", os.path.abspath(a.config)]
    print(f"  language: config says \"auto\" — detecting it from the take ({how})")
    r = hfcfg.run(cmd, capture_output=False)
    if r.returncode == 2:
        print("  ✗ the language is unclear — ask the user which language the take is in, then:\n"
              f"      python3 {os.path.join(HERE, 'detect_language.py')} --set <code>\n"
              "    and run xcheck.py again")
        sys.exit(2)
    if r.returncode:
        sys.exit(f"language detection failed (exit {r.returncode})")
    cfg = hfcfg.load(a.config)
    hfcfg.require_language(cfg, "xcheck.py")
    return cfg["language"]["code"]


def run_engine(media, tag, engine, model, prefix, how, a, lang):
    os.makedirs("src/xcheck", exist_ok=True)
    out = f"src/xcheck/{tag}.json"
    cmd = prefix + [os.path.join(HERE, "transcribe.py"), media, "--engine", engine,
                    "--model", model, "--lang", lang, "--out", out,
                    "--words", f"src/xcheck/{tag}_words.json", "--flags", ""]
    if a.config:
        cmd += ["--config", os.path.abspath(a.config)]
    if a.glossary:
        cmd += ["--glossary", a.glossary]
    if a.start is not None:
        cmd += ["--start", str(a.start)]
    if a.end is not None:
        cmd += ["--end", str(a.end)]
    if a.force:
        cmd += ["--force"]
    print(f"  {tag}: {engine} ({model}) via {how}")
    r = hfcfg.run(cmd, capture_output=False)
    if r.returncode or not os.path.exists(out):
        sys.exit(f"engine {tag} failed (exit {r.returncode})")
    return json.load(open(out, encoding="utf-8"))


# ------------------------------------------------------------------ words
def flat_words(result):
    """[{s, e, w, seg}] from a whisper-style result."""
    out = []
    for si, s in enumerate(result.get("segments", [])):
        for w in s.get("words", []):
            out.append({"s": float(w["start"]), "e": float(w["end"]),
                        "w": str(w["word"]).strip(), "seg": si})
    return out


def norm(t):
    t = re.sub(r"[֑-ׇ]", "", str(t))
    return t.strip("\"'.,?!…:;)(-־“”״׳").replace("־", "-").lower()


def context(words, i1, i2, k=5):
    left = " ".join(w["w"] for w in words[max(0, i1 - k):i1])
    mid = " ".join(w["w"] for w in words[i1:i2]) or "∅"
    right = " ".join(w["w"] for w in words[i2:i2 + k])
    return f"{left} [{mid}] {right}".strip()


def diff(pa, pb):
    """Disagreements between primary words pa and secondary words pb."""
    na, nb = [norm(w["w"]) for w in pa], [norm(w["w"]) for w in pb]
    sm = difflib.SequenceMatcher(None, na, nb, autojunk=False)
    rows, agree = [], 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            agree += i2 - i1
            continue
        if i1 < len(pa) and i2 > i1:
            t0, t1 = pa[i1]["s"], pa[i2 - 1]["e"]
        elif j2 > j1:
            t0, t1 = pb[j1]["s"], pb[j2 - 1]["e"]
        else:
            t0 = t1 = pa[min(i1, len(pa) - 1)]["s"] if pa else 0.0
        rows.append({"id": f"d{len(rows) + 1:02d}", "t": round(t0, 2), "t_end": round(t1, 2),
                     "i1": i1, "i2": i2,
                     "a": " ".join(w["w"] for w in pa[i1:i2]),
                     "b": " ".join(w["w"] for w in pb[j1:j2]),
                     "context": context(pa, i1, i2)})
    return rows, agree


# ------------------------------------------------------------- corrections
def load_corrections(path):
    if not os.path.exists(path):
        return {}, []
    d = json.load(open(path, encoding="utf-8"))
    keep = [str(x) for x in d.get("_keep", [])]
    fixes = {}
    for k, v in d.items():
        if str(k).startswith("_"):
            continue
        if isinstance(v, dict):
            fixes[str(k)] = (str(v.get("to", "")), str(v.get("why", "")))
        else:
            fixes[str(k)] = (str(v), "")
    return fixes, keep


def _split_span(s, e, parts):
    """Split [s, e] over `parts` words by character length (timings stay inside the span)."""
    if len(parts) <= 1:
        return [(s, e)]
    total = sum(max(1, len(p)) for p in parts)
    out, t = [], s
    for p in parts:
        d = (e - s) * max(1, len(p)) / total
        out.append((round(t, 3), round(t + d, 3)))
        t += d
    out[-1] = (out[-1][0], e)
    return out


def _replace(words, i1, i2, to):
    """Replace words[i1:i2] by the text `to`, keeping the span's timing and trailing punct."""
    s, e, seg = words[i1]["s"], words[i2 - 1]["e"], words[i1]["seg"]
    tail = PUNCT_TAIL.search(words[i2 - 1]["w"])
    parts = to.split()
    if parts and tail and not PUNCT_TAIL.search(parts[-1]):
        parts[-1] += tail.group(0)
    new = [{"s": a, "e": b, "w": p, "seg": seg} for (a, b), p in zip(_split_span(s, e, parts), parts)]
    return words[:i1] + new + words[i2:], len(new)


def apply_corrections(words, fixes):
    """Apply time keys first (they address one word), then word/phrase keys (every
    occurrence). Returns (words, changes, unmatched_keys)."""
    changes, unmatched = [], []
    words = [dict(w) for w in words]
    for key in sorted((k for k in fixes if TIME_KEY.match(k)), key=float):
        t = float(key)
        cand = [(abs(w["s"] - t), i) for i, w in enumerate(words)
                if w["s"] - 0.15 <= t <= w["e"] + 0.15]
        if not cand:
            unmatched.append(key)
            continue
        i = min(cand)[1]
        to, why = fixes[key]
        old = words[i]["w"]
        words, _n = _replace(words, i, i + 1, to)
        changes.append({"t": round(words[i]["s"] if _n else t, 2), "key": key, "from": old,
                        "to": to or "∅ (deleted)", "why": why})
    for key in [k for k in fixes if not TIME_KEY.match(k)]:
        pat = [norm(x) for x in key.split()]
        to, why = fixes[key]
        hit, i = False, 0
        while i <= len(words) - len(pat):
            if [norm(w["w"]) for w in words[i:i + len(pat)]] == pat:
                old = " ".join(w["w"] for w in words[i:i + len(pat)])
                t = words[i]["s"]
                words, n = _replace(words, i, i + len(pat), to)
                changes.append({"t": round(t, 2), "key": key, "from": old,
                                "to": to or "∅ (deleted)", "why": why})
                hit = True
                i += max(n, 1)
            else:
                i += 1
        if not hit:
            unmatched.append(key)
    changes.sort(key=lambda c: c["t"])
    return words, changes, unmatched


def decided(row, fixes, keep, primary):
    """How a disagreement was settled: a correction covering it, or a _keep entry."""
    for k in keep:
        if k == row["id"] or (TIME_KEY.match(k) and row["t"] - 0.2 <= float(k) <= row["t_end"] + 0.2):
            return "kept primary"
    for k in fixes:
        if TIME_KEY.match(k):
            if row["t"] - 0.2 <= float(k) <= row["t_end"] + 0.2:
                return f"corrected ({k})"
        else:
            pat = [norm(x) for x in k.split()]
            span = [norm(w["w"]) for w in primary[max(0, row["i1"] - len(pat) + 1):
                                                    row["i2"] + len(pat) - 1]]
            if any(span[i:i + len(pat)] == pat for i in range(len(span) - len(pat) + 1)):
                return f"corrected ({k})"
    return ""


# ------------------------------------------------------------------ output
def write_outputs(primary_res, words, out_json, out_words):
    segs = []
    for si, s in enumerate(primary_res.get("segments", [])):
        ws = [{"word": w["w"], "start": w["s"], "end": w["e"]} for w in words if w["seg"] == si]
        if ws:
            segs.append({"start": ws[0]["start"], "end": ws[-1]["end"],
                         "text": " ".join(w["word"] for w in ws), "words": ws})
    res = dict(primary_res)
    res["segments"] = segs
    res["text"] = " ".join(s["text"] for s in segs)
    for p in (out_json, out_words):
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    json.dump(res, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump([[w["s"], w["e"], w["w"]] for w in words],
              open(out_words, "w", encoding="utf-8"), ensure_ascii=False)


def markdown(names, rows, agree, na, nb, changes, unmatched):
    ea, eb = names
    L = ["# Transcript cross-check", "",
         f"Primary **{ea}** ({na} words) vs secondary **{eb}** ({nb} words): agree on "
         f"{agree}, {len(rows)} disagreement(s).", "",
         "Decide every row by meaning and context. Captions show the INTENDED, correctly "
         "spelled words; timings come from the audio. Put each fix in `src/corrections.json` "
         "(time key `\"12.34\": \"word\"` or word key `\"misheard\": \"intended\"`); list rows "
         "where the primary is right under `\"_keep\"` (by id or time).", ""]
    if rows:
        L += ["## Disagreements", ""]
        for r in rows:
            st = r.get("decision") or "UNDECIDED"
            L.append(f"- **{r['id']}** `[{r['t']:06.2f}]`  {ea}: **{r['a'] or '∅'}**  ·  "
                     f"{eb}: **{r['b'] or '∅'}**  —  {st}")
            L.append(f"  - context: {r['context']}")
        L.append("")
    L += ["## Corrections applied", ""]
    if changes:
        for c in changes:
            why = f"  ({c['why']})" if c.get("why") else ""
            L.append(f"- `[{c['t']:06.2f}]` {c['from']} → **{c['to']}**{why}")
    else:
        L.append("_none_")
    if unmatched:
        L += ["", "## Correction keys that matched NOTHING (fix the key)", ""]
        L += [f"- `{k}`" for k in unmatched]
    return "\n".join(L) + "\n"


def selftest():
    """The engine choice per language, with the interpreters faked — `xcheck.py selftest`."""
    global _python_for
    fails = []

    def want(nm, ok):
        print(f"  {'✓' if ok else '✗'} {nm}")
        if not ok:
            fails.append(nm)

    real, real_platform = _python_for, sys.platform
    try:
        sys.platform = "darwin"
        _python_for = lambda m: (["py"], "fake")                       # noqa: E731
        he = plan_engines("mlx", "he")
        want("Hebrew: ivrit-ai + mlx turbo", [p[0] for p in he] == ["ivrit", "mlx"])
        en = plan_engines("mlx", "en")
        want("English on Apple Silicon: mlx large-v3 + faster large-v3",
             [(p[0], p[2]) for p in en] == [("mlx", MLX_LARGE), ("faster", "large-v3")])
        want("English never runs the Hebrew fine-tune",
             not any("ivrit" in p[2] for p in en + plan_engines("faster", "en")))
        _python_for = lambda m: ((None, "no mlx") if m == "mlx_whisper"   # noqa: E731
                                 else (["py"], "fake"))
        en2 = plan_engines("mlx", "es")
        want("no mlx: faster large-v3 + faster large-v3-turbo, distinct tags",
             [(p[0], p[2]) for p in en2] == [("faster", "large-v3"), ("turbo", "large-v3-turbo")])
        he2 = plan_engines("mlx", "he")
        want("Hebrew without mlx: ivrit + faster large-v3", [p[0] for p in he2] == ["ivrit", "faster"])
    finally:
        _python_for, sys.platform = real, real_platform
    print(f"\n  xcheck selftest: {'ok' if not fails else f'{len(fails)} FAILED'}")
    return 1 if fails else 0


def main():
    if sys.argv[1:2] == ["selftest"]:
        return selftest()
    ap = hfcfg.arg_parser(__doc__.split("\n\n")[0])
    ap.add_argument("media")
    ap.add_argument("--primary", choices=("a", "ivrit", "b"), default="a",
                    help="whose words (and timings) become src/raw_words.json: a = engine A "
                         "(default; ivrit-ai for Hebrew, large-v3 otherwise — \"ivrit\" is the "
                         "old name for it) or b = the second engine")
    ap.add_argument("--lang", help="override config language.code for this run")
    ap.add_argument("--engine-b", choices=("mlx", "faster"), default="mlx")
    ap.add_argument("--glossary", default="", help='extra "term1,term2" on top of config')
    ap.add_argument("--start", type=float)
    ap.add_argument("--end", type=float)
    ap.add_argument("--force", action="store_true", help="ignore the transcript cache")
    ap.add_argument("--corrections", default="src/corrections.json")
    ap.add_argument("--diff", default="src/transcript_diff.md")
    ap.add_argument("--out", default="src/raw.json")
    ap.add_argument("--words", default="src/raw_words.json")
    ap.add_argument("--summary", default="src/xcheck.json")
    ap.add_argument("--apply-only", action="store_true",
                    help="reuse src/xcheck/*.json, only (re)apply the corrections")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 while any disagreement is neither corrected nor kept")
    a = ap.parse_args()
    hfcfg.require("ffmpeg", "ffprobe")
    media = os.path.abspath(a.media)

    summary_path = a.summary
    if a.apply_only:
        src = next((p for p in (summary_path, "src/xcheck.json") if os.path.exists(p)), None)
        if not src:
            sys.exit("no src/xcheck.json yet — run without --apply-only first")
        prev = json.load(open(src, encoding="utf-8"))
        names = prev["engines"]
        res = [json.load(open(f"src/xcheck/{n}.json", encoding="utf-8")) for n in names]
    else:
        lang = resolve_language(a, media)
        plan = plan_engines(a.engine_b, lang)
        print(f"  language {lang}: engines {' + '.join(p[0] for p in plan)}")
        res = [run_engine(media, tg, e, m, p, h, a, lang) for tg, e, m, p, h in plan]
        names = [p[0] for p in plan]
    if a.primary == "b":
        res, names = res[::-1], names[::-1]

    pa, pb = flat_words(res[0]), flat_words(res[1])
    rows, agree = diff(pa, pb)
    fixes, keep = load_corrections(a.corrections)
    for r in rows:
        r["decision"] = decided(r, fixes, keep, pa)
    words, changes, unmatched = apply_corrections(pa, fixes)
    write_outputs(res[0], words, a.out, a.words)
    open(a.diff, "w", encoding="utf-8").write(
        markdown(names, rows, agree, len(pa), len(pb), changes, unmatched))
    undecided = [r for r in rows if not r["decision"]]
    json.dump({"engines": names, "media": media, "agree": agree, "words": [len(pa), len(pb)],
               "disagreements": [{k: r[k] for k in ("id", "t", "a", "b", "context", "decision")}
                                 for r in rows],
               "undecided": len(undecided), "changes": changes, "unmatched_keys": unmatched,
               "corrections_file": os.path.exists(a.corrections)},
              open(summary_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"\n  {names[0]}: {len(pa)} words   {names[1]}: {len(pb)} words   agree on {agree}"
          f"   ({100.0 * agree / max(1, len(pa)):.1f} % of the primary)")
    print(f"  {len(rows)} disagreement(s), {len(undecided)} undecided → {a.diff}")
    for r in rows:
        print(f"    {r['id']} [{r['t']:6.2f}] {names[0]}: {r['a'] or '∅'}  |  {names[1]}: "
              f"{r['b'] or '∅'}   {r['decision'] or '← decide'}")
    if changes:
        print(f"  {len(changes)} correction(s) applied (list them in the report):")
        for c in changes:
            print(f"    [{c['t']:6.2f}] {c['from']} → {c['to']}" + (f"  ({c['why']})" if c["why"] else ""))
    print(f"  → {a.words}  {a.out}")
    bad = False
    if unmatched:
        print(f"  ✗ correction key(s) matched nothing: {unmatched}")
        bad = True
    if undecided and a.strict:
        print(f"  ✗ {len(undecided)} disagreement(s) undecided — correct them or list them in "
              f"\"_keep\" in {a.corrections}")
        bad = True
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
