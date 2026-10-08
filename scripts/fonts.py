#!/usr/bin/env python3
"""Free fonts only — the registry, the fetcher, the pairings and the licence gate.

Why: the people using this skill publish COMMERCIAL videos. A face that is "just on my
Mac" (Arial, Helvetica, Futura, a Fontbit Hebrew face bought for one client) is not a
licence to burn it into somebody else's ad — and a font that sneaks in as a CSS FALLBACK
renders exactly as visibly as the primary one whenever a glyph is missing. So:

  * every face the pipeline can fetch is listed in REGISTRY with its licence
    (SIL OFL 1.1, Apache 2.0 or the Ubuntu Font Licence — all allow commercial use and
    embedding) and a verified download URL in github.com/google/fonts;
  * `fetch` downloads it, saves the licence text next to it, and records a sha256 in
    <font dir>/fonts.lock.json, so "where did this .ttf come from?" always has an answer;
  * `guard()` is the QA gate: every font-family named in the composition must be free
    (registry, lock record or a generic keyword), commercial names are banned even as a
    fallback, and every font FILE in the font dir must be accounted for. A gate cannot be
    forgotten the way a rule can.

The licence gate is language-agnostic. A second, separate check is about the LANGUAGE:
`script_issues()` fails a caption/display face that has no glyphs for the take's script
(Bebas Neue on a Hebrew take, Heebo on a Russian one): the missing letters would come
from whatever fallback the machine has, which is exactly how a non-free face reaches a
render. preflight_qa.py runs it.

CLI
  fonts.py list  [--hebrew | --script cyrillic] [--role headline]
  fonts.py fetch <Name> [<Name> ...] [--dest assets/fonts] [--force]
  fonts.py pair  [--latin | --hebrew]          (default: the config's language)
  fonts.py guard <file.html|.css ...> [--font-dir assets/fonts]
  fonts.py css <Name> [<Name> ...] [--font-dir assets/fonts] [--prefix assets/fonts/]
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Iterable, List, Optional, Tuple

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = os.path.join(SKILL_DIR, "assets", "fonts")
LOCK_NAME = "fonts.lock.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

GF_RAW = "https://raw.githubusercontent.com/google/fonts/main/"
GF_CDN = "https://cdn.jsdelivr.net/gh/google/fonts@main/"

FREE_LICENSES = {"OFL-1.1", "Apache-2.0", "UFL-1.0"}
_LIC_DIR = {"OFL-1.1": "ofl", "Apache-2.0": "apache", "UFL-1.0": "ufl"}
_LIC_FILE = {"OFL-1.1": "OFL.txt", "Apache-2.0": "LICENSE.txt", "UFL-1.0": "UFL.txt"}
_LIC_SHORT = {"OFL-1.1": "OFL", "Apache-2.0": "LICENSE", "UFL-1.0": "UFL"}

_WEIGHT_NAMES = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium",
                 600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black"}


def _q(path: str) -> str:
    return urllib.parse.quote(path, safe="/")


def _family(name: str, gf_dir: str, license: str, scripts: List[str], roles: List[str],
            note: str, variable: Optional[str] = None, weights: str = "400",
            static: Optional[Dict[int, str]] = None) -> dict:
    """One REGISTRY entry. `variable` = the upstream [axes] filename; `static` = {weight:
    upstream filename} for families google/fonts ships as separate per-weight files."""
    base = f"{_LIC_DIR[license]}/{gf_dir}/"
    files = []
    if variable:
        files.append({"file": variable, "save_as": f"{name}.ttf", "weight": weights,
                      "urls": [GF_RAW + _q(base + variable), GF_CDN + _q(base + variable)]})
    for w, fn in sorted((static or {}).items()):
        files.append({"file": fn, "save_as": fn, "weight": str(w),
                      "urls": [GF_RAW + _q(base + fn), GF_CDN + _q(base + fn)]})
    lic = base + _LIC_FILE[license]
    return {"family": name, "files": files, "license": license,
            "license_url": GF_RAW + lic, "license_urls": [GF_RAW + lic, GF_CDN + lic],
            "scripts": scripts, "variable": bool(variable), "weights": weights,
            "roles": roles, "note": note}


def _static(prefix: str, weights: Iterable[int]) -> Dict[int, str]:
    return {w: f"{prefix}-{_WEIGHT_NAMES[w]}.ttf" for w in weights}


HE, LA = "hebrew", "latin"

# Every entry below was verified against google/fonts METADATA.pb (licence, filenames,
# axis range and — for the Hebrew faces — `subsets: "hebrew"`) and every URL answered
# HTTP 200 on 2026-10-06. Add a family only the same way: see references/fonts.md.
# (Tinos, the free Times stand-in, is deliberately absent: google/fonts ships it with no
# licence file, and this registry never records a font without its licence text.)
_ENTRIES = [
    # ------------------------------------------------------------- Hebrew + Latin
    _family("Heebo", "heebo", "OFL-1.1", [HE, LA], ["caption", "body", "headline"],
            "House default caption face. Clean grotesque; 800 reads at phone size. Its "
            "capital I has no serifs, so Latin 'AI' reads as 'Al' — set isolated Latin "
            "acronyms in Roboto Slab 800.", variable="Heebo[wght].ttf", weights="100-900"),
    _family("Rubik", "rubik", "OFL-1.1", [HE, LA, "cyrillic", "arabic"],
            ["caption", "body", "headline", "rounded"],
            "Slightly rounded corners, friendly, very legible; strong at 700-900.",
            variable="Rubik[wght].ttf", weights="300-900"),
    _family("Assistant", "assistant", "OFL-1.1", [HE, LA], ["caption", "body"],
            "Light, airy sans (Source Sans Hebrew). Elegant body; captions want 700-800.",
            variable="Assistant[wght].ttf", weights="200-800"),
    _family("Noto Sans Hebrew", "notosanshebrew", "OFL-1.1", [HE, LA],
            ["caption", "body"], "Neutral workhorse with the widest coverage; also has a "
            "width axis (62.5-100).", variable="NotoSansHebrew[wdth,wght].ttf",
            weights="100-900"),
    _family("Noto Serif Hebrew", "notoserifhebrew", "OFL-1.1", [HE, LA],
            ["serif", "headline", "body"], "Serif companion to Noto Sans Hebrew.",
            variable="NotoSerifHebrew[wdth,wght].ttf", weights="100-900"),
    _family("IBM Plex Sans Hebrew", "ibmplexsanshebrew", "OFL-1.1", [HE, LA],
            ["caption", "body", "headline"], "Technical, corporate, crisp. Static files; "
            "no 800/900 (700 is the heaviest).", weights="100-700",
            static=_static("IBMPlexSansHebrew", (100, 200, 300, 400, 500, 600, 700))),
    _family("Secular One", "secularone", "OFL-1.1", [HE, LA], ["headline", "display"],
            "Heavy, compact display sans — the go-to Hebrew headline/punch word face. One "
            "weight only.", weights="400", static={400: "SecularOne-Regular.ttf"}),
    _family("Suez One", "suezone", "OFL-1.1", [HE, LA], ["headline", "display", "serif"],
            "Heavy slab-serif display. Bold editorial/news punch. One weight only.",
            weights="400", static={400: "SuezOne-Regular.ttf"}),
    _family("Frank Ruhl Libre", "frankruhllibre", "OFL-1.1", [HE, LA],
            ["serif", "headline", "body"], "The classic Hebrew book serif, revived. "
            "Elegant headlines (finance, legal, luxury).", variable="FrankRuhlLibre[wght].ttf",
            weights="300-900"),
    _family("David Libre", "davidlibre", "OFL-1.1", [HE, LA], ["serif", "body"],
            "Free revival of David (NOT Microsoft's David). Literary, traditional.",
            weights="400,500,700", static={400: "DavidLibre-Regular.ttf",
                                           500: "DavidLibre-Medium.ttf",
                                           700: "DavidLibre-Bold.ttf"}),
    _family("Miriam Libre", "miriamlibre", "OFL-1.1", [HE, LA], ["body", "headline"],
            "Free revival of Miriam. Geometric-ish, slightly retro.",
            variable="MiriamLibre[wght].ttf", weights="400-700"),
    _family("Karantina", "karantina", "OFL-1.1", [HE, LA], ["display", "headline"],
            "Very condensed, tall display. Big single words, playful/poster energy. "
            "Never for captions.", weights="300,400,700",
            static={300: "Karantina-Light.ttf", 400: "Karantina-Regular.ttf",
                    700: "Karantina-Bold.ttf"}),
    _family("Varela Round", "varelaround", "OFL-1.1", [HE, LA], ["rounded", "body",
                                                                   "caption"],
            "Soft rounded sans, one weight (400) — friendly body/caption for light tones.",
            weights="400", static={400: "VarelaRound-Regular.ttf"}),
    _family("Alef", "alef", "OFL-1.1", [HE, LA], ["body"],
            "Plain, highly legible text face. Regular + Bold.", weights="400,700",
            static={400: "Alef-Regular.ttf", 700: "Alef-Bold.ttf"}),
    _family("Bellefair", "bellefair", "OFL-1.1", [HE, LA], ["serif", "display"],
            "High-contrast fashion serif. Large sizes only.", weights="400",
            static={400: "Bellefair-Regular.ttf"}),
    _family("Amatic SC", "amaticsc", "OFL-1.1", [HE, LA, "cyrillic"], ["handwritten",
                                                                         "display"],
            "Hand-drawn condensed caps. Accents and doodle labels only, never body.",
            weights="400,700", static={400: "AmaticSC-Regular.ttf", 700: "AmaticSC-Bold.ttf"}),
    _family("Fredoka", "fredoka", "OFL-1.1", [HE, LA], ["rounded", "display", "headline"],
            "Bubbly rounded display (also a width axis). Kids/playful brands.",
            variable="Fredoka[wdth,wght].ttf", weights="300-700"),
    _family("Playpen Sans Hebrew", "playpensanshebrew", "OFL-1.1", [HE, LA],
            ["handwritten", "rounded", "body"], "Casual handwriting with real Hebrew "
            "letterforms; legible enough for short lines.",
            variable="PlaypenSansHebrew[wght].ttf", weights="100-800"),
    _family("Open Sans", "opensans", "OFL-1.1", [HE, LA, "cyrillic", "greek"],
            ["body", "caption"], "Neutral humanist sans with Hebrew; width axis too.",
            variable="OpenSans[wdth,wght].ttf", weights="300-800"),
    _family("Arimo", "arimo", "OFL-1.1", [HE, LA, "cyrillic", "greek"], ["body"],
            "Metric-compatible FREE stand-in for Arial — use it when a reference or a "
            "client file says Arial.", variable="Arimo[wght].ttf", weights="400-700"),
    # ------------------------------------------------------------------ Latin only
    _family("Inter", "inter", "OFL-1.1", [LA, "cyrillic", "greek"],
            ["caption", "body", "headline"], "Universal Latin fallback; every font stack "
            "names it before the generic keyword.", variable="Inter[opsz,wght].ttf",
            weights="100-900"),
    _family("Roboto Slab", "robotoslab", "Apache-2.0", [LA, "cyrillic", "greek"],
            ["serif", "headline"], "REQUIRED with Heebo: isolated Latin acronyms ('AI', "
            "'API', 'CEO') in Hebrew captions are set in Roboto Slab 800 — the slab serifs "
            "on capital I stop 'AI' reading as 'Al'.", variable="RobotoSlab[wght].ttf",
            weights="100-900"),
    _family("Montserrat", "montserrat", "OFL-1.1", [LA, "cyrillic"], ["headline", "display",
                                                                        "body"],
            "Geometric, wide; strong 800-900 headlines.", variable="Montserrat[wght].ttf",
            weights="100-900"),
    _family("Poppins", "poppins", "OFL-1.1", [LA, "devanagari"], ["headline", "body",
                                                                    "caption"],
            "Geometric sans; static files per weight.", weights="100-900",
            static=_static("Poppins", (100, 200, 300, 400, 500, 600, 700, 800, 900))),
    _family("Bebas Neue", "bebasneue", "OFL-1.1", [LA], ["display", "headline"],
            "Tall condensed caps for big numbers and single words. One weight.",
            weights="400", static={400: "BebasNeue-Regular.ttf"}),
    _family("Anton", "anton", "OFL-1.1", [LA], ["display", "headline"],
            "Heavy condensed impact face. One weight.", weights="400",
            static={400: "Anton-Regular.ttf"}),
    _family("Oswald", "oswald", "OFL-1.1", [LA, "cyrillic"], ["display", "headline"],
            "Condensed gothic; variable 200-700.", variable="Oswald[wght].ttf",
            weights="200-700"),
    _family("Space Grotesk", "spacegrotesk", "OFL-1.1", [LA], ["headline", "body"],
            "Techy grotesk with quirky details; tech/AI topics.",
            variable="SpaceGrotesk[wght].ttf", weights="300-700"),
    _family("JetBrains Mono", "jetbrainsmono", "OFL-1.1", [LA, "cyrillic", "greek"],
            ["mono"], "Code, terminals, prompts on screen.",
            variable="JetBrainsMono[wght].ttf", weights="100-800"),
    _family("Ubuntu", "ubuntu", "UFL-1.0", [LA, "cyrillic", "greek"], ["body", "headline"],
            "Ubuntu Font Licence: free to use and embed; a MODIFIED version must be "
            "renamed.", weights="300,400,500,700",
            static={300: "Ubuntu-Light.ttf", 400: "Ubuntu-Regular.ttf",
                    500: "Ubuntu-Medium.ttf", 700: "Ubuntu-Bold.ttf"}),
]

REGISTRY: Dict[str, dict] = {e["family"]: e for e in _ENTRIES}

# Proven Hebrew pairings: (caption/body face + weight, headline/display face + weight, why)
PAIRINGS = [
    ("Heebo 800", "Secular One 400", "house default: neutral captions, heavy compact punch "
     "words. Latin acronyms in Roboto Slab 800."),
    ("Rubik 700", "Suez One 400", "friendly rounded captions + slab-serif editorial "
     "headlines — news, explainers."),
    ("Assistant 700", "Frank Ruhl Libre 800", "elegant: light sans body + classic Hebrew "
     "serif headlines — finance, legal, luxury, family office."),
    ("Varela Round 400", "Karantina 700", "playful: soft round body + tall condensed "
     "poster words — lifestyle, kids, food."),
    ("IBM Plex Sans Hebrew 700", "Heebo 900", "corporate/tech: crisp technical captions, "
     "the headline is the same voice turned up."),
    ("Noto Sans Hebrew 700", "Noto Serif Hebrew 800", "safe multi-language set (same "
     "design family across scripts)."),
]

# Latin-first pairings — for English and every Latin-script language. Heebo still works
# (it has Latin), but these are drawn for Latin first. Same rule: one caption face + at
# most one display face.
LATIN_PAIRINGS = [
    ("Inter 700", "Inter 900", "the neutral default: one family, the headline is the same "
     "voice turned up. Reads at caption size on any footage."),
    ("Inter 600", "Montserrat 800", "geometric punch words over neutral captions — "
     "business, creators, tech."),
    ("Poppins 600", "Poppins 800", "friendly geometric, one family — lifestyle, education."),
    ("Inter 700", "Bebas Neue 400", "tall condensed caps for numbers and one-word punches "
     "— sport, finance, bold statements."),
    ("Open Sans 700", "Oswald 600", "condensed gothic headlines, humanist captions — news, "
     "explainers."),
    ("Space Grotesk 600", "Space Grotesk 700", "techy grotesk — AI and developer topics."),
]

# The script a language is written in (the registry's "scripts" names). Anything not
# listed is Latin.
LANG_SCRIPT = {"he": HE, "yi": HE, "iw": HE, "ar": "arabic", "fa": "arabic", "ur": "arabic",
               "ps": "arabic", "ru": "cyrillic", "uk": "cyrillic", "bg": "cyrillic",
               "sr": "cyrillic", "mk": "cyrillic", "be": "cyrillic", "kk": "cyrillic",
               "el": "greek", "hi": "devanagari", "mr": "devanagari", "ne": "devanagari",
               "zh": "chinese", "ja": "japanese", "ko": "korean", "th": "thai"}


def script_for(lang_code: str) -> str:
    c = str(lang_code or "").lower().replace("_", "-").split("-")[0]
    return LANG_SCRIPT.get(c, LA)


def script_issues(families: Iterable[str], lang_code: str) -> List[str]:
    """Faces that cannot set the language's script. Only REGISTRY faces are judged (their
    scripts are known); an imported face with a lock record is the user's call."""
    sc = script_for(lang_code)
    out = []
    for fam in families:
        fam = (fam or "").strip()
        e = REGISTRY.get(fam)
        if not fam or not e or sc in e["scripts"]:
            continue
        have = [f for f, x in REGISTRY.items() if sc in x["scripts"]]
        out.append(f"{fam} has no {sc} glyphs for a '{lang_code}' take — the letters would "
                   f"come from a fallback font. "
                   + (f"Use one that has them: {', '.join(have[:5])}" if have else
                      f"No registry face covers {sc}: fetch a free one (e.g. a Noto family) "
                      f"with setup_assets.py --font-dir"))
    return out


GENERIC = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui",
           "ui-sans-serif", "ui-serif", "ui-monospace", "ui-rounded", "math", "emoji",
           "fangsong", "inherit", "initial", "unset", "revert", "revert-layer"}

# Commercial / system faces. Banned by name EVEN AS A FALLBACK: on a machine that has
# them installed they render whenever the free face misses a glyph.
BANNED = ["Arial", "Arial Black", "Arial Hebrew", "Helvetica", "Helvetica Neue",
          "Times New Roman", "Times", "Futura", "Gotham", "Avenir", "Avenir Next",
          "Proxima Nova", "SF Pro", "SF Pro Display", "SF Pro Text", "SF Pro Rounded",
          "SF Hebrew", "San Francisco", "-apple-system", "BlinkMacSystemFont",
          "Segoe UI", "Calibri", "Cambria", "Tahoma", "Verdana", "Georgia",
          "Gill Sans", "Lucida Grande", "Courier New", "Narkis", "Narkisim",
          "Narkis Block", "Guttman", "David", "Miriam", "FrankRuehl", "Gisha", "Aharoni",
          "Levenim MT", "Rod", "Almoni", "Ploni", "Simona"]
_BANNED_NORM = {re.sub(r"[\s_-]", "", b.lower()): b for b in BANNED}
_FONTBIT = re.compile(r"^fb[\s_-]?[a-z]", re.I)       # Fontbit's "Fb<Name>" catalogue
_FONT_EXT = (".ttf", ".otf", ".woff", ".woff2", ".ttc")


def _norm(name: str) -> str:
    return re.sub(r"[\s_-]", "", name.strip().strip("'\"").lower())


def lookup(name: str) -> Optional[dict]:
    """Registry entry by family name, case/space-insensitive ('robotoslab' works)."""
    n = _norm(name)
    for fam, e in REGISTRY.items():
        if _norm(fam) == n:
            return e
    return None


# ================================================================== lock file

def lock_path(font_dir: str) -> str:
    return os.path.join(font_dir, LOCK_NAME)


def load_lock(font_dir: str) -> dict:
    p = lock_path(font_dir)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            d.setdefault("fonts", {})
            return d
        except (OSError, ValueError):
            pass
    return {"version": 1, "fonts": {}}


def save_lock(font_dir: str, lock: dict) -> None:
    os.makedirs(font_dir, exist_ok=True)
    with open(lock_path(font_dir), "w", encoding="utf-8") as f:
        json.dump(lock, f, indent=1, ensure_ascii=False)
        f.write("\n")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def record(font_dir: str, family: str, license: str, files: List[dict],
           license_file: str = "", license_url: str = "", source: str = "registry",
           variable: bool = False) -> None:
    """Add/replace one family in fonts.lock.json. files: [{file, weight, source_url}]."""
    if license not in FREE_LICENSES:
        raise ValueError(f"{family}: licence {license!r} is not on the free list "
                         f"{sorted(FREE_LICENSES)}")
    lock = load_lock(font_dir)
    out = []
    for f in files:
        p = os.path.join(font_dir, f["file"])
        out.append({"file": f["file"], "weight": f.get("weight", "400"),
                    "sha256": sha256(p), "source_url": f.get("source_url", "")})
    lock["fonts"][family] = {
        "family": family, "license": license, "license_url": license_url,
        "license_file": license_file, "source": source, "variable": variable,
        "files": out,
        "recorded": datetime.datetime.now().replace(microsecond=0).isoformat()}
    save_lock(font_dir, lock)


# ===================================================================== fetch

def _download(urls: List[str], dest: str, min_bytes: int = 512) -> Optional[str]:
    """First URL that answers with a plausible body wins. Returns the URL used."""
    for u in urls:
        try:
            req = urllib.request.Request(u, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            if len(data) < min_bytes:
                continue
            tmp = dest + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
            return u
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
    return None


def fetch(name: str, dest: str = DEFAULT_DIR, force: bool = False, quiet: bool = False
          ) -> Tuple[bool, str]:
    """Download a registry family + its licence into `dest`, record it in the lock.

    Variable families are saved as '<Family>.ttf' — the convention caption_layer.font_faces
    reads as "one variable file, give it the full weight range". Static families keep the
    upstream per-weight names (Alef-Bold.ttf), whose suffix carries the weight.
    Returns (ok, message).
    """
    e = lookup(name)
    if not e:
        return False, (f"{name!r} is not in the free-font registry. `fonts.py list` shows "
                       f"what is; add a family only with a verified free licence "
                       f"(references/fonts.md).")
    os.makedirs(dest, exist_ok=True)
    fam = e["family"]
    lock = load_lock(dest)
    have = lock["fonts"].get(fam)
    if have and not force and all(os.path.exists(os.path.join(dest, f["file"]))
                                  for f in have["files"]):
        return True, f"{fam}: already present ({len(have['files'])} file(s), {have['license']})"
    got = []
    for f in e["files"]:
        target = os.path.join(dest, f["save_as"])
        used = _download(f["urls"], target, min_bytes=4096)
        if not used:
            return False, f"{fam}: could not download {f['file']} (tried {len(f['urls'])} URLs)"
        got.append({"file": f["save_as"], "weight": f["weight"], "source_url": used})
    lic_name = f"{fam}-{_LIC_SHORT[e['license']]}.txt"
    if not _download(e["license_urls"], os.path.join(dest, lic_name), min_bytes=200):
        return False, f"{fam}: font downloaded but its licence text did not — refusing to " \
                      f"record it without the licence"
    record(dest, fam, e["license"], got, license_file=lic_name,
           license_url=e["license_url"], source="registry", variable=e["variable"])
    kb = sum(os.path.getsize(os.path.join(dest, g["file"])) for g in got) // 1024
    return True, (f"{fam}: {len(got)} file(s), {kb} KB, {e['license']}"
                  f"{' variable ' + e['weights'] if e['variable'] else ' weights ' + e['weights']}")


def ensure(families: Iterable[str], dest: str, quiet: bool = True) -> List[str]:
    """Make sure every family is in the PROJECT's font dir (the composition loads fonts by a
    relative URL, so a face that lives only in the skill's assets never reaches the render).
    Copies from the skill's cache with its licence and lock record; fetches if the cache
    lacks it. Returns the families it could not provide."""
    import shutil
    failed = []
    src_lock = load_lock(DEFAULT_DIR)
    for fam in families:
        if not fam:
            continue
        e = lookup(fam)
        name = e["family"] if e else fam
        if faces_for(name, dest):
            continue
        rec = next((r for f, r in src_lock["fonts"].items() if _norm(f) == _norm(name)), None)
        if rec and os.path.abspath(dest) != os.path.abspath(DEFAULT_DIR) and all(
                os.path.exists(os.path.join(DEFAULT_DIR, f["file"])) for f in rec["files"]):
            os.makedirs(dest, exist_ok=True)
            for f in rec["files"]:
                shutil.copy2(os.path.join(DEFAULT_DIR, f["file"]), os.path.join(dest, f["file"]))
            if rec.get("license_file") and os.path.exists(os.path.join(DEFAULT_DIR, rec["license_file"])):
                shutil.copy2(os.path.join(DEFAULT_DIR, rec["license_file"]),
                             os.path.join(dest, rec["license_file"]))
            lock = load_lock(dest)
            lock["fonts"][name] = rec
            save_lock(dest, lock)
            continue
        ok, msg = fetch(name, dest, quiet=quiet)
        if not ok:
            failed.append(name)
            if not quiet:
                print("  ! " + msg)
    return failed


# ====================================================== font files / name table

def read_name_table(path: str) -> Dict[int, str]:
    """{nameID: string} from a .ttf/.otf 'name' table (stdlib only; .woff/.woff2 -> {}).

    IDs that matter here: 1/16 family, 13 licence description, 14 licence URL.
    """
    out: Dict[int, str] = {}
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return out
    if len(data) < 12 or data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        return out
    num = struct.unpack(">H", data[4:6])[0]
    off = None
    for i in range(num):
        rec = data[12 + 16 * i: 28 + 16 * i]
        if rec[:4] == b"name":
            off = struct.unpack(">I", rec[8:12])[0]
            break
    if off is None:
        return out
    try:
        _, count, sto = struct.unpack(">HHH", data[off:off + 6])
        for i in range(count):
            pid, eid, lid, nid, ln, so = struct.unpack(
                ">HHHHHH", data[off + 6 + 12 * i: off + 18 + 12 * i])
            raw = data[off + sto + so: off + sto + so + ln]
            if pid == 3 or pid == 0:
                s = raw.decode("utf-16-be", "replace")
            else:
                s = raw.decode("latin-1", "replace")
            # prefer English / Windows records, but take anything for a missing ID
            if nid not in out or (pid == 3 and lid == 0x409):
                out[nid] = s
    except struct.error:
        pass
    return out


def font_family_of(path: str) -> str:
    """Typographic family (nameID 16, else 1), else a guess from the filename."""
    names = read_name_table(path)
    fam = names.get(16) or names.get(1)
    if fam:
        return fam.strip()
    stem = os.path.splitext(os.path.basename(path))[0]
    return re.split(r"[-\[]", stem)[0]


def is_variable_file(path: str) -> bool:
    """True when the sfnt has an 'fvar' table (or the name carries Google's [axes] tag)."""
    if "[" in os.path.basename(path):
        return True
    try:
        with open(path, "rb") as f:
            head = f.read(12 + 16 * 64)
        num = struct.unpack(">H", head[4:6])[0]
        return any(head[12 + 16 * i: 16 + 16 * i] == b"fvar" for i in range(min(num, 64)))
    except (OSError, struct.error):
        return False


_LICENSE_GLOBS = ("OFL*.txt", "OFL", "LICENSE*", "LICENCE*", "UFL*.txt", "*license*.txt",
                  "*LICENSE*.txt", "*licence*.txt")


def detect_license_file(folder: str) -> Tuple[Optional[str], Optional[str]]:
    """(path, licence id) of a FREE licence text shipped in `folder`, or (None, None).

    Only the text counts, not a filename: an 'OFL.txt' that is really a EULA is refused.
    """
    seen = set()
    for pat in _LICENSE_GLOBS:
        for p in sorted(glob.glob(os.path.join(folder, pat))):
            if p in seen or not os.path.isfile(p):
                continue
            seen.add(p)
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    txt = f.read(200000)
            except OSError:
                continue
            if "SIL Open Font License" in txt or "SIL OPEN FONT LICENSE" in txt:
                return p, "OFL-1.1"
            if "Apache License" in txt and "Version 2.0" in txt:
                return p, "Apache-2.0"
            if "UBUNTU FONT LICENCE" in txt.upper():
                return p, "UFL-1.0"
    return None, None


def import_dir(src: str, dest: str = DEFAULT_DIR) -> Tuple[List[str], List[str]]:
    """Copy the font files from a user folder into `dest` — ONLY when they are provably free.

    A file is accepted when its family (from the font's own name table) is a REGISTRY
    family, or when the folder ships a free licence text (OFL / Apache 2.0 / UFL). Anything
    else is refused with the reason: "it is on my Mac" is not a licence to publish with it,
    and the users of this skill publish commercially. Returns (imported, refused) messages.
    """
    import shutil
    src = os.path.expanduser(src)
    files = sorted(p for p in glob.glob(os.path.join(src, "*")) if p.lower().endswith(_FONT_EXT))
    imported: List[str] = []
    refused: List[str] = []
    if not files:
        return imported, [f"no .ttf/.otf/.woff/.woff2 files in {src}"]
    lic_path, lic_id = detect_license_file(src)
    by_family: Dict[str, List[str]] = {}
    for p in files:
        by_family.setdefault(font_family_of(p), []).append(p)
    os.makedirs(dest, exist_ok=True)
    for fam, paths in sorted(by_family.items()):
        e = lookup(fam) or lookup(re.split(r"[-\[]", os.path.basename(paths[0]))[0])
        if _norm(fam) in _BANNED_NORM or _FONTBIT.match(fam):
            refused.append(f"{fam}: commercial/system face — never imported, licence file or not")
            continue
        if e:
            name, lic, lic_url = e["family"], e["license"], e["license_url"]
        elif lic_id:
            name, lic, lic_url = fam, lic_id, ""
        else:
            refused.append(
                f"{fam} ({len(paths)} file(s)): not a registry font and {src} ships no free "
                f"licence file (OFL.txt / LICENSE with 'SIL Open Font License' or 'Apache "
                f"License'). Refused to protect you: videos made with this skill are "
                f"published commercially, and a font installed on a computer is not a licence "
                f"to publish with it. Use a registry face (fonts.py list) or put the font's "
                f"free licence text next to it.")
            continue
        recs = []
        for p in paths:
            ext = os.path.splitext(p)[1].lower()
            var = is_variable_file(p)
            target = f"{name}{ext}" if var and len(paths) == 1 else os.path.basename(p)
            shutil.copy2(p, os.path.join(dest, target))
            stem = os.path.splitext(os.path.basename(p))[0]
            recs.append({"file": target, "weight": "100-1000" if var else
                         str(_weight_from_name(stem)), "source_url": "local:" + p})
        lic_file = ""
        if lic_path and not e:
            lic_file = f"{name}-{_LIC_SHORT[lic]}.txt"
            shutil.copy2(lic_path, os.path.join(dest, lic_file))
        elif e:
            lic_file = f"{name}-{_LIC_SHORT[lic]}.txt"
            if lic_path and lic_id == lic:
                shutil.copy2(lic_path, os.path.join(dest, lic_file))
            elif not _download(e["license_urls"], os.path.join(dest, lic_file), 200):
                lic_file = ""
        record(dest, name, lic, recs, license_file=lic_file, license_url=lic_url,
               source="local:" + src, variable=any(r["weight"] == "100-1000" for r in recs))
        imported.append(f"{name}: {len(recs)} file(s), {lic}"
                        f"{'' if e else ' (licence text: ' + os.path.basename(lic_path) + ')'}")
    return imported, refused


def known_files(font_dir: str) -> Dict[str, Tuple[str, str]]:
    """{filename: (family, licence)} for every file the lock or the registry vouches for."""
    out: Dict[str, Tuple[str, str]] = {}
    for e in REGISTRY.values():
        for f in e["files"]:
            out[f["save_as"]] = (e["family"], e["license"])
    lock = load_lock(font_dir)
    for fam, rec in lock["fonts"].items():
        if rec.get("license") in FREE_LICENSES:
            for f in rec.get("files", []):
                out[f["file"]] = (fam, rec["license"])
    return out


# ============================================================= @font-face CSS

def _fmt(fn: str) -> str:
    ext = os.path.splitext(fn)[1].lower()
    return {".ttf": "truetype", ".otf": "opentype", ".woff": "woff",
            ".woff2": "woff2"}.get(ext, "truetype")


def _weight_from_name(stem: str) -> int:
    suf = stem.split("-")[-1].replace(" ", "").lower()
    table = {v.lower(): k for k, v in _WEIGHT_NAMES.items()}
    table.update({"bold": 700, "black": 900, "heavy": 900, "book": 400, "normal": 400})
    return table.get(suf, 400)


def faces_for(family: str, font_dir: str) -> List[Tuple[str, str]]:
    """[(filename, css font-weight)] present on disk for one family.

    Order of truth: the lock record, then the registry's expected filenames, then a
    single '<family>.ttf/.otf/.woff2' (treated as variable, like caption_layer does).
    """
    lock = load_lock(font_dir)
    rec = None
    for fam, r in lock["fonts"].items():
        if _norm(fam) == _norm(family):
            rec = r
            break
    out: List[Tuple[str, str]] = []
    if rec:
        for f in rec["files"]:
            if not os.path.exists(os.path.join(font_dir, f["file"])):
                continue
            w = str(f.get("weight", "400"))
            ranged = "-" in w or "," in w or " " in w      # '100-900' = a variable file
            out.append((f["file"], "100 1000" if ranged else w))
        if out:
            return out
    e = lookup(family)
    if e:
        for f in e["files"]:
            if os.path.exists(os.path.join(font_dir, f["save_as"])):
                out.append((f["save_as"], "100 1000" if e["variable"] else f["weight"]))
        if out:
            return out
    for ext in (".ttf", ".otf", ".woff2", ".woff"):
        p = os.path.join(font_dir, family + ext)
        if os.path.exists(p):
            return [(family + ext, "100 1000")]
    return out


def font_faces_css(families: Iterable[str], font_dir: str = DEFAULT_DIR,
                   url_prefix: str = "assets/fonts/", strict: bool = True) -> str:
    """@font-face CSS for several families — e.g. the caption face AND a display face.

    Variable files get `font-weight: 100 1000` (without a range Chrome clamps a variable
    font to one instance and every weight renders the same); static files get one face
    per weight. Raises FileNotFoundError for a missing family when strict — a missing
    face silently renders in the browser's default serif, which no snapshot flags.
    """
    blocks, missing = [], []
    seen = set()
    for fam in families:
        if not fam or _norm(fam) in seen:
            continue
        seen.add(_norm(fam))
        e = lookup(fam)
        name = e["family"] if e else fam
        faces = faces_for(name, font_dir)
        if not faces:
            missing.append(name)
            continue
        for fn, w in faces:
            url = url_prefix + urllib.parse.quote(fn)
            blocks.append(f'@font-face {{ font-family: "{name}"; src: url("{url}") '
                          f'format("{_fmt(fn)}"); font-weight: {w}; font-style: normal; '
                          f'font-display: block; }}')
    if missing and strict:
        raise FileNotFoundError(
            f"font(s) not in {font_dir}: {', '.join(missing)} — run "
            f"`python3 scripts/fonts.py fetch {' '.join(repr(m) for m in missing)}`")
    return "\n".join(blocks)


# ===================================================================== guard

_DECL = re.compile(r"(?<![\w-])(font-family|font)\s*:\s*", re.I)
_ATTR = re.compile(r"(?<![\w-])font-family\s*=\s*([\"'])(.*?)\1", re.I | re.S)
_JS = re.compile(r"\bfontFamily\s*[:=]\s*([\"'`])(.*?)\1", re.S)
_CANVAS = re.compile(r"\.font\s*=\s*([\"'`])(.*?)\1", re.S)
_VARDEF = re.compile(r"(--[\w-]+)\s*:\s*([^;}{]+)")
_URL = re.compile(r"url\(\s*([\"']?)([^\"')]+)\1\s*\)", re.I)
_LOCAL = re.compile(r"local\(\s*([\"']?)([^\"')]+)\1\s*\)", re.I)
_FONTFACE = re.compile(r"@font-face\s*\{([^}]*)\}", re.I)
_GFONTS = re.compile(r"fonts\.googleapis\.com/css2?\?([^\"'\s>]+)", re.I)
_PAID_CDN = re.compile(r"(use\.typekit\.net|p\.typekit\.net|fonts\.adobe\.com|"
                       r"fast\.fonts\.net|fonts\.com/|cloud\.typography\.com|myfonts\.net|"
                       r"fontbit\.co\.il)", re.I)
_SIZE = re.compile(r"(?:^|\s)(?:[\d.]+(?:px|pt|em|rem|%|vh|vw|vmin|vmax|ch|ex)|xx-small|"
                   r"x-small|small|medium|large|x-large|xx-large|larger|smaller)"
                   r"(?:\s*/\s*\S+)?\s+", re.I)


def _read_value(text: str, i: int) -> str:
    """CSS value starting at i: stops at ; } newline > or an unbalanced attribute quote.

    Inline `style="font-family: 'Heebo'"` and a stylesheet `font-family: "Heebo";` are both
    common; a quote that never closes before the value would end belongs to the
    surrounding attribute, so it terminates the value instead of swallowing the page.
    """
    out = []
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ";}\n>":
            break
        if ch in "\"'":
            j = i + 1
            while j < n and text[j] != ch and text[j] not in ";}\n>":
                j += 1
            if j < n and text[j] == ch:
                out.append(text[i:j + 1])
                i = j + 1
                continue
            break
        out.append(ch)
        i += 1
    return "".join(out).strip()


def _split_families(value: str) -> List[str]:
    parts, cur, q = [], [], None
    for ch in value:
        if q:
            if ch == q:
                q = None
            else:
                cur.append(ch)
        elif ch in "\"'":
            q = ch
        elif ch == ",":
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur).strip())
    return [p for p in parts if p]


def _shorthand_families(value: str) -> Optional[str]:
    """The family list of a `font:` shorthand (after the size), or None if not a shorthand
    with a family (e.g. `font: inherit`)."""
    v = value.strip()
    if v.lower() in GENERIC or v.startswith("var("):
        return v
    m = None
    for m in _SIZE.finditer(" " + v):
        pass
    if not m:
        return None
    return (" " + v)[m.end():].strip()


def _resolve_vars(fam: str, vars_: Dict[str, str], depth: int = 0) -> List[str]:
    m = re.match(r"var\(\s*(--[\w-]+)\s*(?:,\s*(.*))?\)\s*$", fam.strip(), re.S)
    if not m:
        return [fam]
    if depth > 8:
        return []
    name, fallback = m.group(1), m.group(2)
    out: List[str] = []
    if name in vars_:
        for f in _split_families(vars_[name]):
            out += _resolve_vars(f, vars_, depth + 1)
    elif fallback is None:
        out.append(f"<undefined {name}>")
    if fallback:
        for f in _split_families(fallback):
            out += _resolve_vars(f, vars_, depth + 1)
    return out


def _installed_faces() -> Dict[str, str]:
    """normalised stem -> path for fonts installed on THIS machine (user + system)."""
    out = {}
    for d in (os.path.expanduser("~/Library/Fonts"), "/Library/Fonts"):
        for p in glob.glob(os.path.join(d, "*")):
            if p.lower().endswith(_FONT_EXT + (".dfont",)):
                stem = os.path.splitext(os.path.basename(p))[0]
                out[_norm(re.split(r"[-\[]", stem)[0])] = p
    return out


def _check_family(name: str, lock_fams: Dict[str, str], installed: Dict[str, str],
                  aliases: Optional[Dict[str, List[str]]] = None) -> Optional[str]:
    raw = name.strip().strip("'\"")
    if not raw:
        return None
    if raw.startswith("<undefined"):
        return f"font-family uses {raw[11:-1]} but no file defines it (cannot prove it is free)"
    low = raw.lower()
    n = _norm(raw)
    if n in _BANNED_NORM or _FONTBIT.match(raw):
        what = _BANNED_NORM.get(n, "a Fontbit (Fb*) face")
        alt = (" — Arimo is the free metric-compatible stand-in"
               if n in ("arial", "helvetica", "helveticaneue", "arialhebrew") else "")
        return (f"'{raw}' is a commercial/system font ({what}) — banned even as a fallback; "
                f"use a free face (fonts.py list){alt}")
    if low in GENERIC:
        return None
    if lookup(raw):
        return None
    if aliases and n in aliases:
        return None         # an @font-face alias whose every src is a vouched-for free file
    if n in lock_fams:
        lic = lock_fams[n]
        if lic in FREE_LICENSES:
            return None
        return f"'{raw}' is in fonts.lock.json with licence {lic!r}, which is not free"
    hint = ""
    if n in installed:
        hint = (f" (it is installed on this machine at {installed[n]} — an installed font "
                f"is not a licence to publish with it)")
    return (f"'{raw}' is not a registry font and has no free-licence lock record{hint}")


def guard(paths: Iterable[str], font_dir: Optional[str] = None) -> List[str]:
    """Return a list of licence problems (empty = pass) for the given HTML/CSS files.

    Checks: every font-family / font shorthand / @font-face / inline style / SVG attribute /
    JS fontFamily names only free families or generic keywords (custom properties such as
    --brand-font are resolved across the files); no banned name appears anywhere in a
    stack; no local() or ~/Library/Fonts sources; no paid font CDN; and every font file in
    `font_dir` is vouched for by the lock or the registry (an unknown file fails).
    """
    paths = [p for p in paths if p]
    issues: List[str] = []
    texts: Dict[str, str] = {}
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                texts[p] = f.read()
        except OSError as ex:
            issues.append(f"{p}: cannot read ({ex})")

    if font_dir is None:
        for cand in ("assets/fonts", DEFAULT_DIR):
            if os.path.isdir(cand):
                font_dir = cand
                break
    lock = load_lock(font_dir) if font_dir and os.path.isdir(font_dir) else {"fonts": {}}
    lock_fams = {_norm(k): v.get("license", "") for k, v in lock["fonts"].items()}
    installed = _installed_faces()
    known = known_files(font_dir) if font_dir and os.path.isdir(font_dir) else \
        known_files("/nonexistent")

    # @font-face ALIASES: `@font-face { font-family: "Brand"; src: url(.../Rubik.ttf) }` is
    # legitimate when every src is a file the registry or the lock vouches for.
    aliases: Dict[str, List[str]] = {}
    for t in texts.values():
        for m in _FONTFACE.finditer(t):
            block = m.group(1)
            fm = re.search(r"font-family\s*:\s*([\"']?)([^;\"']+)\1", block, re.I)
            if not fm or _LOCAL.search(block):
                continue
            files = [os.path.basename(urllib.parse.unquote(u.group(2)).split("?")[0])
                     for u in _URL.finditer(block) if not u.group(2).startswith("data:")]
            bad_path = any(re.search(r"Library/Fonts", u.group(2)) for u in _URL.finditer(block))
            if files and not bad_path and all(f in known for f in files):
                aliases.setdefault(_norm(fm.group(2)), []).extend(files)

    # custom properties, across ALL files (brand.css defines what index.html uses)
    vars_: Dict[str, str] = {}
    for t in texts.values():
        for m in _VARDEF.finditer(t):
            vars_[m.group(1)] = m.group(2).strip()

    seen = set()

    def add(msg):
        if msg not in seen:
            seen.add(msg)
            issues.append(msg)

    for p, t in texts.items():
        rel = os.path.relpath(p) if not os.path.isabs(p) or p.startswith(os.getcwd()) else p
        stacks: List[Tuple[int, str]] = []
        for m in _DECL.finditer(t):
            val = _read_value(t, m.end())
            if m.group(1).lower() == "font":
                val = _shorthand_families(val)
                if val is None:
                    continue
            stacks.append((m.start(), val))
        for rx in (_ATTR, _JS):
            for m in rx.finditer(t):
                stacks.append((m.start(), m.group(2)))
        for m in _CANVAS.finditer(t):
            fam = _shorthand_families(m.group(2))
            if fam:
                stacks.append((m.start(), fam))
        # custom properties that hold a font stack must be free even if unused
        for name, val in vars_.items():
            if re.search(r"font|family|typeface", name, re.I) and not re.match(
                    r"^[\d.]+(px|em|rem|%)?$|^\d{3}$", val.strip()):
                if name + ":" in t.replace(" ", ""):
                    stacks.append((t.find(name), val))
        for pos, val in stacks:
            line = t.count("\n", 0, max(pos, 0)) + 1
            for fam in _split_families(val):
                for resolved in _resolve_vars(fam, vars_):
                    msg = _check_family(resolved, lock_fams, installed, aliases)
                    if msg:
                        add(f"{rel}:{line}: {msg}")
        for m in _FONTFACE.finditer(t):
            block = m.group(1)
            line = t.count("\n", 0, m.start()) + 1
            for lm in _LOCAL.finditer(block):
                add(f"{rel}:{line}: @font-face uses local('{lm.group(2)}') — that loads "
                    f"whatever is installed on the render machine; ship the file instead")
            for um in _URL.finditer(block):
                u = urllib.parse.unquote(um.group(2))
                if u.startswith("data:"):
                    continue
                if re.search(r"(^|/)(Library/Fonts|System/Library/Fonts)/", u) or "~/Library" in u:
                    add(f"{rel}:{line}: @font-face loads {u} from a system font folder — "
                        f"installed fonts are not licensed for publishing")
                    continue
                base = os.path.basename(u.split("?")[0].split("#")[0])
                if base.lower().endswith(_FONT_EXT) and base not in known:
                    add(f"{rel}:{line}: @font-face file '{base}' has no lock record and is "
                        f"not a registry file")
        for m in _PAID_CDN.finditer(t):
            line = t.count("\n", 0, m.start()) + 1
            add(f"{rel}:{line}: loads fonts from {m.group(1)} — a paid/commercial font "
                f"service; use a free face fetched with fonts.py")
        for m in _GFONTS.finditer(t):
            line = t.count("\n", 0, m.start()) + 1
            for fam in re.findall(r"family=([^&:]+)", urllib.parse.unquote(m.group(1))):
                fam = fam.replace("+", " ")
                if not lookup(fam) and _norm(fam) not in lock_fams:
                    add(f"{rel}:{line}: Google Fonts link for '{fam}' — not in the registry; "
                        f"add it to fonts.py REGISTRY (verified) and fetch it locally")

    # every font FILE in the font dir must be accounted for
    if font_dir and os.path.isdir(font_dir):
        lock_files = {}
        for fam, rec in lock["fonts"].items():
            for f in rec.get("files", []):
                lock_files[f["file"]] = (fam, rec.get("license", ""), f.get("sha256", ""))
        for fn in sorted(os.listdir(font_dir)):
            if not fn.lower().endswith(_FONT_EXT):
                continue
            path = os.path.join(font_dir, fn)
            if fn in lock_files:
                fam, lic, digest = lock_files[fn]
                if lic not in FREE_LICENSES:
                    add(f"{path}: lock says licence {lic!r} — not free")
                elif digest and sha256(path) != digest:
                    add(f"{path}: sha256 does not match fonts.lock.json — the file was "
                        f"replaced after it was recorded; re-fetch or re-import it")
                continue
            if fn in known:
                continue
            fam = font_family_of(path)
            add(f"{path}: unknown font file (family '{fam}') — no lock record, not a "
                f"registry file. Import it with setup_assets.py --font-dir (needs a free "
                f"licence file) or delete it")
    return issues


# ======================================================================= CLI

def _cmd_list(a) -> int:
    rows = []
    for e in REGISTRY.values():
        if a.hebrew and HE not in e["scripts"]:
            continue
        if a.script and a.script not in e["scripts"]:
            continue
        if a.role and a.role not in e["roles"]:
            continue
        rows.append(e)
    print(f"{'family':22} {'licence':10} {'type':8} {'weights':12} {'hebrew':6} roles")
    for e in rows:
        print(f"{e['family']:22} {e['license']:10} "
              f"{'variable' if e['variable'] else 'static':8} {e['weights']:12} "
              f"{'yes' if HE in e['scripts'] else '-':6} {', '.join(e['roles'])}")
        if a.verbose:
            print(f"{'':22} {e['note']}")
    print(f"\n{len(rows)} of {len(REGISTRY)} families "
          f"({sum(1 for e in REGISTRY.values() if HE in e['scripts'])} with Hebrew). "
          f"All free for commercial use and embedding.")
    return 0


def _cmd_fetch(a) -> int:
    bad = 0
    for name in a.names:
        ok, msg = fetch(name, a.dest, a.force)
        print(("  ok  " if ok else "  !!  ") + msg)
        bad += 0 if ok else 1
    print(f"\nlock: {lock_path(a.dest)}")
    return 1 if bad else 0


def _cmd_pair(a) -> int:
    latin = a.latin
    if not latin and not a.hebrew:
        try:                                     # default: the project's language
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import hfcfg
            latin = script_for(hfcfg.load()["language"]["code"]) == LA
        except Exception:                        # noqa: BLE001
            latin = False
    if latin:
        print("Latin-first pairings (caption/body  +  headline/display):\n")
        for body, head, why in LATIN_PAIRINGS:
            print(f"  {body:26} + {head:22} {why}")
        print("\nRules: one caption face + at most ONE display face. Heebo (the house face) "
              "has Latin too;\nthese are drawn for Latin first. Inter is the fallback in "
              "every stack.")
        return 0
    print("Proven Hebrew pairings (caption/body  +  headline/display):\n")
    for body, head, why in PAIRINGS:
        print(f"  {body:26} + {head:22} {why}")
    print("\nRules: one caption face + at most ONE display face. Isolated Latin acronyms "
          "('AI') in\nHeebo captions go in Roboto Slab 800. Inter is the Latin fallback in "
          "every stack.")
    return 0


def selftest() -> int:
    """The language side of the gate (no network) — `fonts.py selftest`."""
    fails = []

    def want(nm, ok):
        print(f"  {'✓' if ok else '✗'} {nm}")
        if not ok:
            fails.append(nm)

    want("Heebo sets English (it has Latin)", not script_issues(["Heebo"], "en"))
    want("Heebo sets Hebrew", not script_issues(["Heebo"], "he"))
    bad = script_issues(["Bebas Neue"], "he")
    want("Bebas Neue on a Hebrew take FAILS and names faces that work",
         bad and "hebrew" in bad[0] and "Heebo" in bad[0])
    want("Heebo on a Russian take FAILS (no Cyrillic)", bool(script_issues(["Heebo"], "ru")))
    want("Inter on a Russian take passes", not script_issues(["Inter"], "ru"))
    want("an imported face (not in the registry) is not judged",
         not script_issues(["My Brand Sans"], "he"))
    want("every Latin pairing names registry faces",
         all(" ".join(x.split()[:-1]) in REGISTRY for b, h, _ in LATIN_PAIRINGS for x in (b, h)))
    want("pt-BR is Latin, ar is Arabic", script_for("pt-BR") == LA and script_for("ar") == "arabic")
    print(f"\n  fonts selftest: {'ok' if not fails else f'{len(fails)} FAILED'}")
    return 1 if fails else 0


def _cmd_guard(a) -> int:
    issues = guard(a.files, a.font_dir)
    if issues:
        print(f"FONT GUARD: {len(issues)} problem(s)")
        for i in issues:
            print(f"  ! {i}")
        print("\nOnly free fonts may reach a render (SIL OFL / Apache / UFL). "
              "See references/fonts.md.")
        return 1
    print(f"FONT GUARD: pass ({len(a.files)} file(s); font dir "
          f"{a.font_dir or 'auto'})")
    return 0


def _cmd_css(a) -> int:
    try:
        print(font_faces_css(a.names, a.font_dir, a.prefix))
    except FileNotFoundError as ex:
        print(f"! {ex}", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("list", help="show the registry")
    p.add_argument("--hebrew", action="store_true", help="only faces with Hebrew")
    p.add_argument("--script", help="only faces with this script (latin, hebrew, cyrillic, "
                                    "greek, devanagari…)")
    p.add_argument("--role", help="caption/headline/body/display/serif/rounded/"
                                  "handwritten/mono")
    p.add_argument("-v", "--verbose", action="store_true", help="print the notes too")
    p = sub.add_parser("fetch", help="download a family + licence, record it in the lock")
    p.add_argument("names", nargs="+")
    p.add_argument("--dest", default=DEFAULT_DIR)
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("pair", help="proven pairings (default: for the config's language)")
    p.add_argument("--latin", action="store_true", help="Latin-first pairings")
    p.add_argument("--hebrew", action="store_true", help="Hebrew pairings")
    sub.add_parser("selftest", help="the language-coverage gate, negative tests")
    p = sub.add_parser("guard", help="licence gate for HTML/CSS")
    p.add_argument("files", nargs="+")
    p.add_argument("--font-dir")
    p = sub.add_parser("css", help="print @font-face CSS for families in a font dir")
    p.add_argument("names", nargs="+")
    p.add_argument("--font-dir", default=DEFAULT_DIR)
    p.add_argument("--prefix", default="assets/fonts/")
    a = ap.parse_args(argv)
    cmds = {"list": _cmd_list, "fetch": _cmd_fetch, "pair": _cmd_pair,
            "guard": _cmd_guard, "css": _cmd_css, "selftest": lambda _a: selftest()}
    if a.cmd not in cmds:
        ap.print_help()
        return 2
    return cmds[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
