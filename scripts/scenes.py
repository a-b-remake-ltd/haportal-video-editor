#!/usr/bin/env python3
"""Per-video designed moments: loads the PROJECT's scenes.py and turns it into fragments.

WHY. The target edits are authored: for every video Claude invents 8-12 literal UI moments
from that video's own lines (references/storyboard.md), plus a hook world and one or two
callbacks. That invention cannot live in a fixed list of types, so it lives in a small Python
file in the project folder, `scenes.py`, written per video on the kit (scripts/kit.py):

    def build(ctx):
        k = ctx.kit
        s = ctx.scene("wait", ctx.t("waiting"), ctx.te("arrive") + 0.4)
        s.add(k.widget(s, k.waiting_room(s, "Waiting room", "Waiting for the host…",
                                         "The host will not join", ctx.t("never"))))
        return [s.done()]

This module builds the `ctx` that file receives (word lookups, the grid, the brand tokens,
the framing map, the camera state), runs it, adds any simple JSON-declared scenes from
media.json "scenes", validates the cross-scene rules, places and levels the SFX, assigns track
lanes and returns ONE plan that build_index.py emits in its `# scenes` section.

The ctx (what scenes.py gets)
  ctx.t(word, n=1, after=None)    spoken START of the n-th occurrence (Hebrew prefixes ו/ה/ב/ל/
                                  מ/ש/כ tolerated; multi-word phrases allowed)
  ctx.te(word, n=1, after=None)   its spoken END
  ctx.span(a, b=None, n=1)        (start of a, end of b after it) — or of the phrase a
  ctx.at(v)                       seconds, or "word", "word@2", "word@2+0.1" → seconds
  ctx.say(a, b)                   the words spoken between two times (for comments/checks)
  ctx.scene(id, start, end, layer="front")      a kit Scene
  ctx.punch([(t, factor), ...])   punch-in steps (tl.set on the footage); declare them FIRST
  ctx.state_at(t)                 the footage's (scale, y) at t (beats × punches × pushes)
  ctx.G, ctx.safe, ctx.W, ctx.H, ctx.fps, ctx.end, ctx.dir
  ctx.sky                         the sky zone: left/right 140 (800 px centred on x 540),
                                  top 250, bottom 600 or the measured head top − 20
  ctx.framing                     build/framing.json (head_top, face_cx/cy, chin, chest, free_zones)
  ctx.tokens, ctx.brand           kit colour roles; brand/brand.json (or None)
  ctx.rng(seed)                   a seeded random.Random for positions (never Math.random)
  ctx.kit                         scripts/kit.py
  ctx.note(msg)                   a warning printed with the build

Fragment contract (what build(ctx) returns, a list; Scene.done() makes one):
  {id, start, end, html, css, js[list of lines], sfx[{name, t, kind: exempt|normal, base_vol}],
   hide_captions: bool | [[a, b]], footage: bool | [[a, b]], punch_ok: bool, z, cls}

media.json "scenes" (the JSON shortcut for kit recipes, no Python):
  "scenes": [{"recipe": "waiting_room", "id": "wr", "start": "מחכים", "end": 21.2,
              "title": "...", "wait": "...", "fail": "...", "fail_t": "לבוא"}]
  Times are seconds or spoken words ("word", "word@2", "word@2+0.1").

Usage
  python3 scripts/scenes.py list                  # components, recipes, icons
  python3 scripts/scenes.py words [a b]           # the transcript with times (author from it)
  python3 scripts/scenes.py plan                  # dry run: the scene table, SFX, warnings
  python3 scripts/scenes.py example > scenes.py   # a starting scenes.py (an invented script)
"""
from __future__ import annotations

import difflib
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402
import grid  # noqa: E402
import kit  # noqa: E402
import moments  # noqa: E402

r3 = kit.r3


# ========================================================================= ctx
class Ctx:
    def __init__(self, cfg, media=None, end=None, beatmap=None, root=".", words=None):
        self.cfg, self.media, self.root = cfg, media or {}, root
        self.kit = kit
        self.W, self.H = cfg["project"]["width"], cfg["project"]["height"]
        if (self.W, self.H) != (1080, 1920):
            print(f"  scenes: ! the kit is authored for 1080x1920; this frame is {self.W}x{self.H}")
        self.fps = float(cfg["project"].get("fps", 25))
        self.dir = cfg.get("language", {}).get("direction", "rtl")
        self.G = grid.from_config(cfg)
        self.safe = self.G["safe"]
        gx0, gy0, gx1, _ = self.safe
        self.framing = kit.load_framing(root)
        # the sky widget zone (spec §2): 800 px centred on the FRAME (grid.centered_box:
        # x 140-940, so left 140, right 140), top 250, bottom y 600 — or, on a MEASURED
        # framing, 20 px above the head when that is higher (a widget must never cover it)
        sx0, sx1 = grid.centered_box(self.G["max_centered_w"], gy0, 600, self.G)
        bottom = 600
        if self.framing.get("source") != "default":
            bottom = min(bottom, int(self.framing["head_top"]) - 20)
        self.sky = {"left": round(sx0), "right": round(self.W - sx1), "top": gy0 + 30,
                    "bottom": max(gy0 + 30 + 200, bottom)}
        self.end = float(end) if end is not None else None
        if words is None:
            wp = os.path.join(root, "src", "words.json")
            words = json.load(open(wp, encoding="utf-8")) if os.path.exists(wp) else []
        self.words = moments._words({"words": words})
        self._n = [moments._norm(w) for _, _, w in self.words]
        self.brand = moments.load_brand_json(cfg, root)
        self.tokens = kit.tokens(self.brand)
        self.foot = kit.FOOT
        # build_index.py gives #aroll transform-origin 50% 30%
        self.origin_y = 0.30 * self.H
        beats = list(getattr(beatmap, "BEATS", []) or []) if beatmap else []
        self._beat_state = moments.make_state_at(beats)
        self._events = []          # (t, kind, value): punch → factor, push → ×k, beat → 1.0
        for b in beats:
            if b[1] != "hook":
                self._events.append((float(b[0]), "beat", 1.0))
        for e in self.media.get("moments") or []:
            if e.get("type") == "punch":
                for t, k in moments._punch_steps(e):
                    self._events.append((float(t), "mpunch", float(k)))
                self._events.append((float(e["end"]), "mpunch", 1.0))
        self._ids = set()
        self.notes = []
        bp = os.path.join(root, "src", "bounds.json")
        self.bounds = json.load(open(bp, encoding="utf-8")).get("bounds", []) if os.path.exists(bp) else []

    # ---- words
    def _match(self, i, toks):
        """Do toks match the transcript starting at word i? → index after, or None."""
        j = i
        for tok in toks:
            n = moments._norm(tok)
            if j >= len(self._n):
                return None
            if moments._same(n, self._n[j]):
                j += 1
            elif j + 1 < len(self._n) and moments._same(n, self._n[j] + self._n[j + 1]):
                j += 2
            else:
                return None
        return j

    def find(self, word, n=1, after=None, before=None):
        """(start, end) of the n-th spoken occurrence of `word` (a word or a phrase)."""
        toks = str(word).split()
        if not toks:
            raise kit.KitError("ctx.find: empty word")
        hits = []
        for i in range(len(self.words)):
            s = self.words[i][0]
            if after is not None and s < float(after) - 1e-6:
                continue
            if before is not None and s > float(before) + 1e-6:
                break
            j = self._match(i, toks)
            if j is not None:
                hits.append((self.words[i][0], self.words[j - 1][1]))
        if len(hits) < n:
            near = difflib.get_close_matches(moments._norm(toks[0]), sorted(set(self._n)), 6, 0.6)
            where = f" after {after}s" if after is not None else ""
            raise kit.KitError(
                f"scenes: {word!r} #{n}{where} is not in src/words.json ({len(hits)} found). "
                f"Close spoken words: {', '.join(near) or '—'}. "
                f"`python3 scripts/scenes.py words` prints the transcript with times.")
        return hits[n - 1]

    def find_in(self, word, a, b):
        try:
            return self.find(word, 1, a - 1e-3, b)[0]
        except SystemExit:
            return None

    def t(self, word, n=1, after=None):
        return r3(self.find(word, n, after)[0])

    def te(self, word, n=1, after=None):
        return r3(self.find(word, n, after)[1])

    def span(self, a, b=None, n=1, after=None):
        s, e = self.find(a, n, after)
        if b is not None:
            e = self.find(b, 1, s)[1]
        return r3(s), r3(e)

    def at(self, v):
        """Seconds pass through; "word", "word@2", "word@2+0.15" resolve on the transcript."""
        if isinstance(v, (int, float)):
            return float(v)
        m = re.match(r"^(.*?)(?:@(\d+))?([+-]\d*\.?\d+)?$", str(v).strip())
        if not m or not m.group(1):
            raise kit.KitError(f"scenes: cannot read time {v!r}")
        return self.find(m.group(1).strip(), int(m.group(2) or 1))[0] + float(m.group(3) or 0)

    def say(self, a, b):
        return " ".join(w for s, e, w in self.words if a - 1e-6 <= s < b)

    def sync(self, tokens, t0, t1):
        times, _ = moments.sync(tokens, {"words": [[s, e, w] for s, e, w in self.words]}, t0, t1)
        return times

    # ---- camera state
    def scale_events(self):
        return sorted(self._events)

    def state_at(self, t):
        base, y = self._beat_state(t)
        f = 1.0
        for et, kind, v in sorted(self._events):
            if et > t + 1e-6:
                break
            if kind == "push":
                f *= v
            else:
                f = v
        return r3(base * f), y

    def _push(self, t, k):
        self._events.append((float(t), "push", float(k)))

    def origin_shift(self, s):
        """y offset that keeps the spec's hook geometry (authored for a camera pivoting on the
        face, y 864) when the A-roll pivots on its own transform-origin."""
        return (kit.REF_ORIGIN_Y * self.H / 1920.0 - self.origin_y) * (1 - s)

    def punch(self, steps, sid="punch"):
        """Punch-in steps [(t, factor), ...]: hard tl.set of the footage scale (factor × the
        beat's scale) on phrase boundaries, alternating 1.0 and 1.06-1.14 (spec §5). Declare
        them before any scene reads the camera state (the hook lands on the punch at its end)."""
        st = sorted((r3(self.at(t)), float(k)) for t, k in steps)
        for t, k in st:
            if not 1.0 <= k <= 1.16:
                raise kit.KitError(f"scenes: punch {k} at {t}s — keep factors in 1.00-1.16")
            self._events.append((t, "punch", k))
        self._claim(sid)
        lines = []
        for t, k in st:
            base, _ = self._beat_state(t)
            lines.append(f"tl.set({json.dumps(self.foot)}, {{ scale: {r3(base * k)} }}, {t});")
        return {"id": sid, "start": st[0][0] if st else 0.0, "end": r3((st[-1][0] if st else 0) + 0.04),
                "html": "", "css": "", "js": lines, "sfx": [], "hide_captions": [], "footage": [],
                "punch_ok": True, "z": 0, "cls": "", "punch_steps": [[t, k] for t, k in st]}

    # ---- misc
    def scene(self, sid, start, end, **kw):
        return kit.Scene(self, sid, start, end, **kw)

    def rng(self, seed=11):
        return random.Random(seed)

    def note(self, msg):
        self.notes.append(str(msg))

    def _claim(self, sid):
        if sid in self._ids:
            raise kit.KitError(f"scenes: duplicate scene id {sid!r}")
        self._ids.add(sid)


# ===================================================================== loading
def project_file(root="."):
    p = os.path.join(root, "scenes.py")
    return p if os.path.exists(p) else None


def run_project(ctx, path):
    spec = importlib.util.spec_from_file_location("project_scenes", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "build"):
        raise kit.KitError(f"{path}: define `def build(ctx):` returning a list of fragments")
    out = mod.build(ctx)
    flat = []
    for f in out or []:
        if isinstance(f, (list, tuple)):
            flat += list(f)
        elif isinstance(f, kit.Scene):
            flat.append(f.done())
        else:
            flat.append(f)
    return flat


def run_json(ctx, entries):
    out = []
    for i, e in enumerate(entries or []):
        e = {k: v for k, v in e.items() if not str(k).startswith("_")}
        name = e.pop("recipe", None) or e.pop("type", None)
        e.setdefault("id", f"s{i + 1}")
        for k in ("start", "end"):
            if k not in e:
                raise kit.KitError(f"media.json scenes[{i}] ({name}): missing {k!r}")
        r = kit.recipe(ctx, name, **e)
        out += r if isinstance(r, list) else [r]
    return out


REQ = ("id", "start", "end", "html", "js")


def _windows(v, a, b):
    if v is True:
        return [[a, b]]
    if not v:
        return []
    return [[float(x), float(y)] for x, y in v]


def validate(frags, ctx, media):
    """Cross-scene rules. Fatal: missing keys, past END, two scenes moving the footage at
    once, a scene moving the footage over a moments fly/paper or inside a matted hook, a
    punch step inside a footage window. Warnings: density, heavy elements, dashes."""
    end = ctx.end
    ids = set()
    for f in frags:
        for k in REQ:
            if k not in f:
                raise kit.KitError(f"scenes: fragment {f.get('id', '?')} lacks {k!r} "
                                   f"(use Scene.done())")
        if f["id"] in ids:
            raise kit.KitError(f"scenes: duplicate id {f['id']}")
        ids.add(f["id"])
        if end is not None and float(f["end"]) > end + 1e-6:
            raise kit.KitError(f"scenes: {f['id']} ends {f['end']}s, past the A-roll END {end:.3f}s")
        if float(f["end"]) <= float(f["start"]):
            raise kit.KitError(f"scenes: {f['id']} end ≤ start")
    foot = []
    for f in frags:
        for a, b in _windows(f.get("footage"), f["start"], f["end"]):
            foot.append((a, b, f["id"]))
    for e in media.get("moments") or []:
        if e.get("type") in moments.FOOTAGE_TYPES:
            foot.append((float(e["start"]), float(e["end"]), f"moment {e.get('id')}"))
    foot.sort()
    for (a0, a1, ia), (b0, b1, ib) in zip(foot, foot[1:]):
        if b0 < a1 - 1e-6 and ia != ib:
            raise kit.KitError(f"scenes: {ia} and {ib} both move the footage at once "
                               f"({a0:.2f}-{a1:.2f} / {b0:.2f}-{b1:.2f})")
    hk = media.get("hook") or {}
    if hk.get("matte"):
        he = float(hk["end"])
        for a, b, i in foot:
            if a < he - 1e-6:
                raise kit.KitError(f"scenes: {i} moves the footage at {a:.2f}s, inside the matted "
                                   f"hook (ends {he:.2f}s): the matte would not move with it")
    steps = [(t, f["id"]) for f in frags for t, _ in f.get("punch_steps", [])]
    steps += [(t, "moments punch") for t, kind, _ in ctx.scale_events() if kind == "mpunch"]
    for t, src in steps:
        for a, b, i in foot:
            owner = next((f for f in frags if f["id"] == i), None)
            if a + 1e-6 < t < b - 1e-6 and not (owner and owner.get("punch_ok")):
                raise kit.KitError(f"scenes: punch step at {t:.2f}s ({src}) is inside {i}'s "
                                   f"camera move ({a:.2f}-{b:.2f}) — move it to the move's end")
    # premium restraint (spec §0.1): never two widgets competing for the sky
    wids = sorted((float(f["start"]), float(f["end"]), f["id"]) for f in frags
                  if 'class="kt-wid' in f.get("html", "") or 'class="kt-page' in f.get("html", ""))
    for i, (a0, a1, ia) in enumerate(wids):
        for b0, b1, ib in wids[i + 1:]:
            if b0 < a1 - 0.35:
                ctx.note(f"{ia} and {ib} are both up {b0:.2f}-{min(a1, b1):.2f}s — two widgets "
                         f"compete; one idea on screen at a time (end the first, or move one)")
    # density (spec §3.1): something designed every few seconds
    vis = [f for f in frags if f.get("html")]
    if end and vis:
        print(f"  scenes: {len(vis)} visual scene(s) over {end:.1f}s")
    n_heavy = kit.heavy_count([f["html"] for f in frags])
    if n_heavy > 36:
        ctx.note(f"{n_heavy} heavy elements (glass/blur/radial/clip-path) — above ~40 the capture "
                 f"renders black (spec §9.6); drop a glass widget or a halo")
    return n_heavy


# ========================================================================= SFX
_SFX_FALLBACK = {
    "soft_whoosh": "whoosh_low", "whoosh_impact": "shutter_impact", "swap_pop": "pop",
    "ding": "pop", "message": "pop", "comment_ping": "pop", "glass_snap": "click",
    "clock": "click", "typing": "click", "snap": "click", "bars": "shutter_impact",
    "shatter": "boom", "bricks": "click", "riser_short": "whoosh_high", "page_flip": "whoosh_low",
    "whoosh_high": "whoosh_low",
}


def resolve_sfx(name, root, sfx_dir, notes):
    """assets/sfx/<name>.wav in the project → the skill's library (copied in) → the nearest
    stock effect (said out loud) → None."""
    import shutil
    for cand in (name, _SFX_FALLBACK.get(name)):
        if not cand:
            continue
        rel = f"{sfx_dir}/{cand}.wav"
        full = os.path.join(root, rel)
        if not os.path.exists(full):
            src = os.path.join(hfcfg.SKILL_DIR, "assets", "sfx", cand + ".wav")
            if os.path.exists(src):
                os.makedirs(os.path.dirname(full), exist_ok=True)
                shutil.copy2(src, full)
        if os.path.exists(full):
            if cand != name:
                notes.add(f"sfx {name!r} not in the library — using {cand!r} (generate a custom "
                          f"one per references/sound.md)")
            return rel, full
    notes.add(f"sfx {name!r} missing (and no fallback) — cue skipped")
    return None, None


def voice_mean_db(aroll, root="."):
    """The A-roll's mean volume (volumedetect), cached in build/voice_mean.json. NOTE: never
    pass `-v error` to ffmpeg here — it silences volumedetect's output."""
    if not aroll or not os.path.exists(aroll):
        return None
    cp = os.path.join(root, "build", "voice_mean.json")
    st = os.stat(aroll)
    key = f"{os.path.abspath(aroll)}|{st.st_size}|{int(st.st_mtime)}"
    try:
        c = json.load(open(cp))
        if c.get("key") == key:
            return c["mean_db"]
    except (OSError, ValueError):
        pass
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", aroll, "-vn", "-af", "volumedetect", "-f",
                        "null", "-"], capture_output=True, text=True)
    m = re.search(r"mean_volume: (-?[0-9.]+)", r.stderr)
    if not m:
        return None
    v = float(m.group(1))
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    json.dump({"key": key, "mean_db": v}, open(cp, "w"))
    return v


def place_sfx(t, kind, words):
    """Spec §7.3: an effect must not sit on a word. Inside a word: move to 0.1 s before it
    when the gap before is ≥ 0.14 s, else just after it when the gap after is ≥ 0.14 s, else
    halve the volume. ONE move only — never chain-slide across words (that is how an effect
    once drifted onto a key word). A move of more than 0.3 s is refused too (halved in
    place): a tap heard 0.4 s after the finger lands reads as a mistake. Exempt impacts stay
    on their beat."""
    if kind == "exempt":
        return t, 1.0, ""
    for i, (s, e, w) in enumerate(words):
        if s - 0.04 <= t < e - 0.02:
            pe = words[i - 1][1] if i else 0.0
            ns = words[i + 1][0] if i + 1 < len(words) else 1e9
            if s - pe >= 0.14 and abs(t - (s - 0.1)) <= 0.3:
                return r3(max(0.0, s - 0.1)), 1.0, f"moved before {w!r}"
            if ns - e >= 0.14 and abs(e + 0.02 - t) <= 0.3:
                return r3(e + 0.02), 1.0, f"moved after {w!r}"
            return t, 0.5, f"halved (on {w!r}, no gap within 0.3 s)"
    return t, 1.0, ""


# ========================================================================= plan
def plan(cfg, media, end, beatmap=None, bounds=(), root=".", quiet=False):
    """Everything build_index.py needs for the `# scenes` section, or None when the project
    has neither scenes.py nor media.json "scenes"."""
    path = project_file(root)
    if not path and not media.get("scenes"):
        return None
    ctx = Ctx(cfg, media, end, beatmap, root)
    frags = []
    if path:
        frags += run_project(ctx, path)
    frags += run_json(ctx, media.get("scenes"))
    frags.sort(key=lambda f: (float(f["start"]), f["id"]))
    n_heavy = validate(frags, ctx, media)

    # ---- elements + lanes (interval colouring: overlapping scenes never share a track)
    # Sequential scenes rotate over four lanes, so no single Studio track gets crowded
    # (lint: timeline_track_too_dense); overlapping ones take the next free lane.
    elements, lanes, k = [], [0.0] * 4, 0
    for f in frags:
        if not f.get("html"):
            continue
        a, b = float(f["start"]), float(f["end"])
        order = [(k + j) % 4 for j in range(4)] + list(range(4, len(lanes)))
        lane = next((i for i in order if lanes[i] <= a + 1e-6), None)
        if lane is None:
            lanes.append(0.0)
            lane = len(lanes) - 1
        lanes[lane] = b
        k += 1
        z = int(f.get("z", 45))
        elements.append({"tag": "div", "id": f["id"], "cls": ("kt-scene " + f.get("cls", "")).strip(),
                         "start": r3(a), "dur": r3(b - a), "lane": lane,
                         "extra": f' style="z-index:{z}"', "inner": f["html"]})

    # ---- timeline: the vocabulary once, then each scene in its own block
    js = [kit.PRELUDE]
    for f in frags:
        if not f["js"]:
            continue
        if f.get("html") or f.get("footage"):
            js.append(f"      {{ const {{ {kit.VOCAB} }} = __KIT;   // scene {f['id']} "
                      f"{float(f['start']):.2f}-{float(f['end']):.2f}s")
            js += [f"        {ln}" for ln in f["js"]]
            js.append("      }")
        else:
            js += [f"      {ln}   // {f['id']}" for ln in f["js"]]

    # ---- caption hiding
    hide = []
    for f in frags:
        hide += [[a, b, f["id"]] for a, b in _windows(f.get("hide_captions"), f["start"], f["end"])]

    # ---- sound: placed off words, scaled to THIS voice (spec §7.3)
    aroll = media.get("aroll", "assets/aroll.mp4")
    vmean = voice_mean_db(os.path.join(root, aroll), root)
    K = 1.0 if vmean is None else max(0.03, min(1.0, 10 ** ((vmean + 16.1) / 20.0)))
    sfx_dir = cfg.get("audio", {}).get("sfx_dir", "assets/sfx")
    snotes, sfx, moved = set(), [], []
    for f in frags:
        for c in f.get("sfx") or []:
            rel, full = resolve_sfx(c["name"], root, sfx_dir, snotes)
            if not rel:
                continue
            t, kv, why = place_sfx(float(c["t"]), c.get("kind", "normal"), ctx.words)
            if why:
                moved.append(f"{f['id']} {c['name']} {float(c['t']):.2f}→{t:.2f} {why}")
            try:
                with wave.open(full) as wv:
                    dur = wv.getnframes() / float(wv.getframerate())
            except (wave.Error, OSError, EOFError):
                dur = 1.0
            if end is not None:
                dur = min(dur, end - t)
            if t < 0 or dur <= 0.05:
                continue
            vol = round(max(0.01, min(1.0, float(c.get("base_vol", 0.2)) * K * kv)), 4)
            sfx.append({"id": f"{f['id']}-x{len(sfx)}", "src": rel, "start": r3(t), "duration": r3(dur),
                        "volume": vol, "lane": len(sfx) % 6, "name": c["name"], "kind": c.get("kind")})

    css = kit.css(ctx) + "\n" + "\n".join(f["css"] for f in frags if f.get("css"))
    notes = ctx.notes + sorted(snotes)
    if not quiet:
        src = path or "media.json"
        print(f"  scenes: {src} → {len(frags)} fragment(s), {len(elements)} layer(s), {len(sfx)} sfx "
              f"(voice mean {vmean} dB, k {K:.3f}), {n_heavy} heavy element(s), tokens: "
              f"{ctx.tokens['source']}, framing: {ctx.framing['source']}")
        for m in moved:
            print(f"  scenes: sfx {m}")
        for n in notes:
            print(f"  scenes: ! {n}")
    table = [{"id": f["id"], "start": r3(f["start"]), "end": r3(f["end"]),
              "words": ctx.say(float(f["start"]), float(f["end"]))} for f in frags]
    return {"elements": elements, "css": css, "js": js, "sfx": sfx, "hide": hide,
            "notes": notes, "table": table, "heavy": n_heavy}


# ===================================================================== example
EXAMPLE = r'''"""scenes.py — the designed moments of THIS video (scripts/scenes.py, references/kit.md).

Storyboard (an invented example script; replace with this video's lines, keep the method):
  0.0-1.4  "everyone tells you to wait"                  opener on the chest, word by word
  1.4      (hook)                                        the frame flies away into the world
  1.4-3.6  "for the right moment, for the right job"     card: an inbox that stays empty
  3.6-6.0  "for someone to finally say yes"              card: people picked, "you" left waiting
  5.6      (return)                                      back to the speaker through the tint
  6.3-8.0  "I waited three years"                        sky: a progress bar crawling 1% → 2%
  8.2-10.5 "and then I just pressed send"                approval dialog, the hand taps "send"
"""


def build(ctx):
    k = ctx.kit
    out = []
    # punch-ins first: the hook lands on the punch active at its landing
    out.append(ctx.punch([(ctx.t("waited"), 1.08), (ctx.t("pressed"), 1.12)]))

    # ---- the hook
    a = k.hook_card(ctx, "hk-a", ctx.t("for"), ctx.t("for", 3), big="the right job",
                    head="Inbox", meta="Updated now")
    a.add(k.empty(a, "No new offers", "still waiting"))
    b = k.hook_card(ctx, "hk-b", ctx.t("for", 3), ctx.te("yes") + 0.48, big="say yes",
                    head="Team pick", meta="Pending", meta_tone="wait")
    b.add(k.avatars(b, [ctx.t("finally"), ctx.t("say")], odd="you?", odd_t=ctx.t("yes")))
    out += k.hook(ctx, [a, b], out=ctx.te("wait") + 0.02, back=ctx.te("yes") + 0.1,
                  intro="everyone tells you / to *wait*")

    # ---- three years crawling
    s = ctx.scene("yrs", ctx.t("waited"), ctx.te("years") + 0.5)
    s.add(k.widget(s, k.progress(s, "Progress", [(s.start, "1%"), (ctx.t("years"), "2%")],
                                 [(s.start + 0.2, 0.3, 0.01), (s.start + 0.5, 1.2, 0.02)], warm=True),
                   title="Three years", sub="almost there…"))
    out.append(s.done())

    # ---- the comic twist: you approve yourself
    s = ctx.scene("send", ctx.t("and"), ctx.te("send") + 0.6)
    s.add(k.widget(s, k.dialog(s, "Send request", "Waiting for approval…", "Sent by you",
                               "Wait", "Send", "Sent", show_t=ctx.t("just"), tap_t=ctx.t("send"))))
    out.append(s.done())
    return out
'''


# ==================================================================== selftest
def selftest():
    """Positive + negative tests of the kit fixes — `scenes.py selftest`. A synthetic
    transcript, no media, no Chrome. Exit 1 on a failure."""
    import tempfile
    fails, n = [], [0]

    def want(name, ok, got=""):
        n[0] += 1
        if not ok:
            fails.append(f"{name}  {got}")
    words = [[0.2 * i, 0.2 * i + 0.18, w] for i, w in enumerate(
        "גידלו אותנו לחכות וזה בדיוק הכלא שבנו לנו תפסיקו לחכות תפסיקו להאשים".split())]
    root = tempfile.mkdtemp(prefix="scenes_selftest_")
    cfg = hfcfg.load(None)
    ctx = Ctx(cfg, {}, 12.0, None, root, words)
    k = kit
    # the sky is centred on the frame: left == right
    want("sky centred (left == right)", ctx.sky["left"] == ctx.sky["right"] == 140, ctx.sky)
    # every catalogue entry has a description (hook_card printed an empty one)
    empty = [nm for nm, d in kit.catalogue() if not d]
    want("no empty catalogue line", not empty, empty)
    # sfx=: default, mute, rename, per-role, and the override
    s = ctx.scene("b1", 0.5, 2.0)
    s.add(k.bars(s, slam_t=ctx.t("הכלא")))
    want("bars default slam 0.42", [c["base_vol"] for c in s._sfx if c.get("role") == "slam"] == [0.42])
    s.sfx_override("bars", vol=0.14, dt=-0.12)
    c = [c for c in s._sfx if c.get("role") == "slam"][0]
    want("sfx_override re-levels and moves", c["base_vol"] == 0.14 and abs(c["t"] - (ctx.t("הכלא") - 0.14)) < 1e-6, c)
    try:
        s.sfx_override("nope", vol=0.1)
        want("sfx_override on a missing cue is an error", False)
    except SystemExit:
        want("sfx_override on a missing cue is an error", True)
    s = ctx.scene("b2", 0.5, 2.0)
    s.add(k.bars(s, slam_t=1.0, sfx=None))
    want("sfx=None mutes", s._sfx == [], s._sfx)
    s = ctx.scene("b3", 0.5, 2.0)
    s.add(k.bars(s, slam_t=1.0, lift_t=1.6, sfx={"slam": {"vol": 0.1}, "lift": None}))
    want("per-role sfx", [(c["role"], c["base_vol"]) for c in s._sfx] == [("slam", 0.1)], s._sfx)
    s = ctx.scene("w1", 0.5, 2.0)
    s.add(k.widget(s, "x", title="t", sfx="pop"))
    want("sfx='name' swaps the main cue", [c["name"] for c in s._sfx] == ["pop"], s._sfx)
    try:
        s2 = ctx.scene("w2", 0.5, 2.0)
        k.widget(s2, "x", sfx={"bogus": 1})
        want("unknown sfx role is an error", False)
    except SystemExit:
        want("unknown sfx role is an error", True)
    # strike_pills leave: an away on the row before the scene end (it had no exit)
    s = ctx.scene("sp", 3.0, 5.0)
    s.add(k.strike_pills(s, [("לחכות", 3.2, 3.6)]))
    want("strike_pills exits", any(ln.startswith("away(") and "sp-sprow" in ln for ln in s.lines), s.lines)
    s = ctx.scene("sp2", 3.0, 5.0)
    s.add(k.strike_pills(s, [("לחכות", 3.2, 3.6)], t_out=False))
    want("strike_pills t_out=False keeps it", not any("sp2-sprow" in ln and ln.startswith("away(")
                                                       for ln in s.lines))
    # hook_card meta as a callable (the spinner needs the card)
    hc = k.hook_card(ctx, "hk", 0.6, 2.0, big="x", head="y", meta=lambda c: k.spinner(c))
    want("hook_card meta callable", "kt-spin" in str(hc.meta), hc.meta)
    hc2 = k.hook_card(ctx, "hk2", 0.6, 2.0, big="x", head="y")
    hc2.set_meta(k.spinner(hc2))
    want("card.set_meta", "kt-spin" in str(hc2.meta))
    # today: centred page, ring under the header
    s = ctx.scene("td", 1.0, 3.0)
    h = k.today(s, "יום", 1.5, big="היום")
    want("today page centred (left 330)", re.search(r"left:330(\.0)?px", h) is not None, h[:120])
    css = kit.css(ctx)
    want("ring starts under the header", ".kt-ring { position: absolute; left: 16px; top: 120px" in css)
    want("calendar height follows --kt-avail", "min(262px, var(--kt-avail, 262px))" in css)
    # a measured low head shrinks the sky; the widget passes the height on
    os.makedirs(os.path.join(root, "build"), exist_ok=True)
    json.dump({"head_top": 560}, open(os.path.join(root, "build", "framing.json"), "w"))
    ctx2 = Ctx(cfg, {}, 12.0, None, root, words)
    want("sky ends 20 px above a measured head", ctx2.sky["bottom"] == 540, ctx2.sky)
    s = ctx2.scene("cw", 1.0, 3.0)
    h = k.widget(s, k.calendar(s, "ev"), eyebrow="יומן")
    want("calendar widget gets --kt-avail", "--kt-avail:154px" in h, re.findall(r"--kt-avail:\d+px", h))
    for f in fails:
        print(f"  ✗ {f}")
    print(f"  scenes/kit selftest: {'FAIL' if fails else 'ok'} ({n[0] - len(fails)}/{n[0]})")
    return 1 if fails else 0


# ========================================================================= CLI
def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("cmd", choices=["list", "words", "plan", "example", "selftest"])
    ap.add_argument("a", nargs="?", type=float)
    ap.add_argument("b", nargs="?", type=float)
    ap.add_argument("--media", default="media.json")
    a = ap.parse_args()
    if a.cmd == "example":
        print(EXAMPLE)
        return 0
    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "list":
        print("components (scripts/kit.py; API in references/kit.md):")
        for n, d in kit.catalogue():
            print(f"  {n:14s} {d}")
        print("\nrecipes (media.json \"scenes\": [{\"recipe\": name, \"id\", \"start\", \"end\", ...}]):")
        for n in sorted(kit.RECIPES):
            import inspect
            sig = str(inspect.signature(kit.RECIPES[n])).replace("(ctx, ", "(")
            print(f"  {n:14s} {sig}")
        print("\nsfx= roles (main cue first; sfx=None mutes, sfx=\"name\" or {name, vol, kind, dt}"
              " changes the main cue, {role: ...} one cue; Scene.sfx_override(name_or_role, "
              "vol=, t=, dt=, kind=, to=, mute=) after the fact):")
        for n, roles in sorted(kit.SFX_ROLES.items()):
            print(f"  {n:14s} {', '.join(roles)}")
        print("\nicons: " + ", ".join(sorted(kit.ICONS)))
        return 0
    cfg = hfcfg.load(a.config)
    media = json.load(open(a.media, encoding="utf-8")) if os.path.exists(a.media) else {}
    end = None
    if os.path.exists("src/bounds.json"):
        end = json.load(open("src/bounds.json"))["total"]
    if a.cmd == "words":
        ctx = Ctx(cfg, media, end)
        lo, hi = a.a or 0.0, a.b or 1e9
        row = []
        for s, e, w in ctx.words:
            if lo <= s <= hi:
                row.append(f"{w}@{s:.2f}")
                if len(row) == 8:
                    print("  " + "  ".join(row))
                    row = []
        if row:
            print("  " + "  ".join(row))
        return 0
    beatmap, _ = hfcfg.load_beats()
    p = plan(cfg, media, end, beatmap)
    if not p:
        print("no scenes.py in this folder and no media.json \"scenes\" — "
              "`python3 scripts/scenes.py example > scenes.py` to start one")
        return 0
    for r in p["table"]:
        print(f"  {r['start']:6.2f}-{r['end']:6.2f}  {r['id']:14s} {r['words'][:70]}")
    print(f"  {len(p['sfx'])} sfx; captions hidden over {len(p['hide'])} window(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
