#!/usr/bin/env python3
"""Generate index.html — the composition — from the beat map plus a media manifest.

THE POINT OF THIS SCRIPT is that every timed value is DERIVED, never hand-typed. When
you drop a line or re-cut, everything downstream moves; anything hardcoded in an HTML
template is left at the old second and a snapshot at the old time looks fine. Rebuild
instead of editing, and the stranded-time bug cannot happen.

It also applies the rules that are easy to forget by hand:
  * the ONE-FRAME BOUNDARY RULE — HyperFrames ends a <video> INCLUSIVE of the frame at
    start+duration but a timed <div> EXCLUSIVE of it, so hook videos get `end - 0.04`
    and hook overlay divs get `end`, and both land their last paint on the same frame
  * alternating track indices, so adjacent clips never share a track
  * every caption's CSS class comes from slot_at() — never a second, hand-kept table
  * caption durations of exactly `next - this - 0.005`, so no boundary frame stacks
  * every card surface sets font-family itself (a class that does not renders in serif)
  * a `tl.set` hard kill at every animated element's out-point
  * the A-roll's per-beat y state, emitted from the beat map

Inputs
  config.json        brand, language, project
  scripts/beats.py   BEATS / SLOT / END        (edit this per project)
  src/bounds.json    segment boundaries        (written by cut_aroll.py)
  media.json         the layers — see --example

Outputs
  index.html         the composition
  build/expected.json  {id: [start, duration]} — feed it to validate.py --expect

Usage
  python3 scripts/build_index.py --example > media.json    # a commented starting point
  python3 scripts/build_index.py --config config.json
  python3 scripts/validate.py --expect build/expected.json --html index.html
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hfcfg  # noqa: E402
import grid  # noqa: E402
import kinetic  # noqa: E402
import moments  # noqa: E402
import outro  # noqa: E402
import scenes  # noqa: E402

beatmap, BEATS_PATH = hfcfg.load_beats()   # project copy wins over the skill's stub

FRAME = 0.04
OUTRO_PLAN = None      # set by build(); main() writes it to build/outro.json for finish.py
KINETIC_PLAN = None    # set by build(); main() prints its word-timing table
CAPTION_HIDE = None    # set by build(); main() writes it to build/caption_hide.json
MOMENTS_PLAN = None    # set by build(); main() writes build/moments.json
SCENES_PLAN = None     # set by build(); main() writes build/scenes.json


def merge_windows(windows, gap=FRAME):
    """[[a, b, source?], ...] → sorted, merged [[a, b, [sources]]]. Windows that touch or
    sit closer than one frame merge: a one-frame flash of a caption between two hidden
    stretches reads as a glitch, not as a caption."""
    rows = []
    for w in windows:
        a, b = float(w[0]), float(w[1])
        src = w[2] if len(w) > 2 else "manual"
        if b > a:
            rows.append((round(a, 3), round(b, 3), src))
    out = []
    for a, b, src in sorted(rows):
        if out and a <= out[-1][1] + gap - 1e-6:
            out[-1][1] = max(out[-1][1], b)
            out[-1][2].append(src)
        else:
            out.append([a, b, [src]])
    return out


class CaptionHide:
    """Collects the windows in which the caption layer is hidden — ONE place every generator
    feeds (headlines, any media element with "hide_captions": true, media.json's manual
    "hide_captions": [[a, b], ...], and later the moments).

    Why a file and not a CSS rule: captions are usually composited OUTSIDE the renderer
    (caption_layer.py, the ghosting fix), so the composition cannot hide them. The build
    writes build/caption_hide.json; caption_layer.py makes those stretches transparent
    while captions.json and the layer's total length stay exactly as they were.
    """

    def __init__(self, end):
        self.end = float(end)
        self.items = []

    def add(self, a, b, source):
        a, b = max(0.0, float(a)), min(self.end, float(b))
        if b > a:
            self.items.append([a, b, str(source)])

    def feed(self, windows, source="manual"):
        for w in windows or []:
            self.add(w[0], w[1], w[2] if len(w) > 2 else source)

    def merged(self):
        return merge_windows(self.items)

    def write(self, path="build/caption_hide.json"):
        """Always written (an empty list too) so a stale file can never hide captions."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        m = self.merged()
        json.dump({"windows": [[a, b] for a, b, _ in m],
                   "sources": [src for _, _, src in m]},
                  open(path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        return m


def transition_allowed(cfg, name):
    """Omer's rule is the default: NO transition on any B-roll, ever. A style (an analysed
    reference via apply_style.py, or the user's explicit request) may open specific named
    transitions in config style.transitions_allowed — e.g. ["page-turn", "frame-fly"]."""
    allowed = (cfg.get("style") or {}).get("transitions_allowed") or []
    return name in allowed


def warn_transition(cfg, where, name, emitted=False):
    """Say out loud when a transition is asked for. Not allowed → it is NOT emitted (hard
    cut). Allowed but this layer has no generator for it → also a hard cut, and say so."""
    if not name:
        return
    if not transition_allowed(cfg, name):
        allowed = (cfg.get("style") or {}).get("transitions_allowed") or []
        print(f"  ! {where}: transition {name!r} is not in style.transitions_allowed "
              f"{allowed} — Omer's rule stands: it hard-cuts. Open it only for a reference "
              f"that uses it or on the user's request (references/layout.md)")
    elif not emitted:
        print(f"  ! {where}: transition {name!r} is allowed by the style, but this layer has no "
              f"generator for it — it still hard-cuts (a designed transition belongs in a moment)")

EXAMPLE = {
    "title": "my reel",
    "aroll": "assets/aroll.mp4",
    "_comment_hook": "omit the whole 'hook' block for a reel that opens on plain A-roll",
    "hook": {
        "backdrop": "assets/hook_blurred.mp4",
        "matte": "assets/matte.webm",
        "end": 8.28,
        "_comment": "matte_* only when the hook carries a graphics cluster; a bare hook "
                    "gets NO transform. Never scale below ~0.78.",
        "matte_scale": 0.80,
        "matte_y": -31,
        "_comment_wide": "set matte_width/matte_left when you matted a source WIDER than "
                         "the frame because you are scaling the roto down",
        "matte_width": 1292,
        "matte_left": -212,
        "wash": True
    },
    "broll": [
        {"id": "b01", "src": "assets/broll/b01.mp4", "start": 11.56, "duration": 3.34,
         "mode": "full"},
        {"id": "b02", "src": "assets/broll/b02.mp4", "start": 14.90, "duration": 2.60,
         "mode": "panel"}
    ],
    "graphics": [
        {"id": "g1", "start": 14.90, "duration": 3.10, "class": "card ctr",
         "reveal": "unblur",
         "html": "<div class=\"inner\"><div class=\"kicker\">the price</div>"
                 "<div class=\"huge\">free</div></div>"}
    ],
    "audio": {
        "music": [
            {"id": "bgm1", "src": "assets/music/bed.mp3", "start": 0, "duration": 11.06,
             "media_start": 0.125, "volume": 0.056},
            {"id": "bgm2", "src": "assets/music/bed.mp3", "start": 14.88, "duration": 5.12,
             "media_start": 15.005, "volume": 0.135}
        ],
        "sfx": [
            {"id": "riser", "src": "assets/sfx/riser_long.wav", "start": 13.19,
             "duration": 1.69, "volume": 0.5},
            {"id": "impact", "src": "assets/sfx/shutter_impact.wav", "start": 14.88,
             "duration": 0.62, "volume": 0.6}
        ]
    },
    "_comment_flares": "full-frame, screen-blended. Optional per flare: x / y (px offset of "
                       "its centre from the frame centre), scale (1 = full frame), rotation "
                       "(deg), opacity (0-1) — set once; \"drift\": {x, y, scale, rotation} "
                       "deltas reached at the flare's end, linear",
    "flares": [{"id": "flare1", "src": "assets/flares/flare1_v.mp4", "start": 14.88,
                "duration": 1.20},
               {"id": "flare2", "src": "assets/flares/flare2_v.mp4", "start": 21.40,
                "duration": 1.00, "x": 260, "y": -540, "scale": 0.7, "rotation": -18,
                "opacity": 0.85, "drift": {"x": -60, "rotation": 6}}],
    "_comment_captions": "'external' = composited outside the renderer by caption_layer.py "
                         "(the fix for caption ghosting). 'inline' = emitted here as clips.",
    "captions": "external",
    "_comment_headlines": "kinetic headlines on 2-4 key sentences, word times from src/words.json: "
                          "\"headlines\": [{\"id\": \"h1\", \"text\": \"הטעות הזאת / *עולה לכם כסף.*\", "
                          "\"style\": \"rollin|ko|bold\"}] — markup and options in references/kinetic.md",
    "_comment_hide": "extra caption-hide windows: \"hide_captions\": [[a, b], ...]; any broll/graphics "
                     "item may also say \"hide_captions\": true",
    "_comment_outro": "opt-in animated logo outro — ONLY with a logo (brand/brand.json) AND "
                      "the user's yes: \"outro\": {\"style\": \"portal|line|impact\", "
                      "\"tagline\": \"...\", \"handle\": \"@name\"}. See references/outro.md"
}


FLARE_KEYS = {"x": (-2000.0, 2000.0), "y": (-2000.0, 2000.0), "scale": (0.05, 6.0),
              "rotation": (-360.0, 360.0), "opacity": (0.0, 1.0)}


def flare_placement(f, start, dur):
    """Where a media.json flare sits: (inline style attr, tl.set lines, drift lines).

      x, y      px, the flare's centre offset from the frame centre (+x right, +y down)
      scale     1 = full frame (the flare file's own framing); 0.5 = half size
      rotation  degrees, clockwise, about the flare's centre
      opacity   0-1, on top of the clip's own fade (screen blend: lower = subtler)
      drift     optional {x, y, scale, rotation} DELTAS reached at the flare's end,
                linear — a slow lens move. Without it nothing moves.

    WHY: every flare used to sit full-frame and centred, so one stock flare looked the same
    on every reel and could not be put where the light source is (a window, a lamp) or
    kept off the face. Bad values stop the build instead of rendering something odd."""
    fid = f.get("id", "?")
    v = {}
    for k, (lo, hi) in FLARE_KEYS.items():
        if f.get(k) is None:
            continue
        try:
            x = float(f[k])
        except (TypeError, ValueError):
            raise SystemExit(f"flare {fid}: {k} = {f[k]!r} is not a number")
        if not lo <= x <= hi:
            raise SystemExit(f"flare {fid}: {k} = {x} outside {lo:g}..{hi:g}")
        v[k] = x
    drift = f.get("drift")
    if drift is not None and not isinstance(drift, dict):
        raise SystemExit(f"flare {fid}: drift must be an object like "
                         f'{{"x": 40, "y": -20, "scale": 0.1, "rotation": 6}}')
    dv = {}
    for k in ("x", "y", "scale", "rotation"):
        if (drift or {}).get(k) is not None:
            try:
                dv[k] = float(drift[k])
            except (TypeError, ValueError):
                raise SystemExit(f"flare {fid}: drift.{k} = {drift[k]!r} is not a number")
    bad = set(drift or {}) - {"x", "y", "scale", "rotation"}
    if bad:
        raise SystemExit(f"flare {fid}: drift keys {sorted(bad)} — use x, y, scale, rotation")
    unknown = set(f) - set(FLARE_KEYS) - {"id", "src", "start", "duration", "drift"} - \
        {k for k in f if str(k).startswith("_")}
    if unknown:
        # loud, not fatal: an older media.json may carry its own notes on a flare, but a
        # typo ("rot") would otherwise leave the flare centred without a word
        print(f"  ! flare {fid}: ignoring unknown key(s) {sorted(unknown)} — placement "
              f"keys are {', '.join(FLARE_KEYS)} and drift")
    x, y = v.get("x", 0.0), v.get("y", 0.0)
    sc, rot = v.get("scale", 1.0), v.get("rotation", 0.0)
    num = lambda n: (str(int(n)) if float(n) == int(n) else repr(round(float(n), 3)))
    style, set_js, drift_js = "", [], []
    op = v.get("opacity")
    if not dv:
        css = []
        if (x, y, sc, rot) != (0.0, 0.0, 1.0, 0.0):
            css.append(f"transform: translate({num(x)}px, {num(y)}px) rotate({num(rot)}deg) "
                       f"scale({num(sc)}); transform-origin: 50% 50%")
        if op is not None:
            css.append(f"opacity: {num(op)}")
        if css:
            style = f' style="{"; ".join(css)};"'
        return style, set_js, drift_js
    if op is not None:
        style = f' style="opacity: {num(op)};"'
    a = {"x": x, "y": y, "scale": sc, "rotation": rot}
    b = {k: a[k] + dv.get(k, 0.0) for k in a}
    if b["scale"] <= 0:
        raise SystemExit(f"flare {fid}: scale + drift.scale = {b['scale']:g} — must stay > 0")
    js = lambda d: ", ".join(f"{k}: {num(d[k])}" for k in ("x", "y", "scale", "rotation"))
    set_js.append(f'      tl.set("#{fid}", {{ {js(a)}, transformOrigin: "50% 50%" }}, 0);')
    drift_js.append(f'      tl.fromTo("#{fid}", {{ {js(a)} }}, {{ {js(b)}, duration: {num(dur)}, '
                    f'ease: "none", immediateRender: false }}, {num(start)});')
    return style, set_js, drift_js


def ensure_project_assets(media):
    """The page loads everything by RELATIVE url, so the skill's shared assets (GSAP, the
    synthesised SFX, the flares) must exist inside the project. Copy what is referenced
    and missing; never overwrite a file the project already has."""
    import shutil
    want = {"assets/vendor/gsap.min.js"}
    for grp in ("music", "sfx"):
        for s in media.get("audio", {}).get(grp, []):
            want.add(str(s.get("src", "")))
    for f in media.get("flares", []):
        want.add(str(f.get("src", "")))
    for rel in sorted(want):
        if not rel.startswith("assets/") or os.path.exists(rel):
            continue
        src = os.path.join(hfcfg.SKILL_DIR, rel)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(rel), exist_ok=True)
            shutil.copy2(src, rel)
            print(f"  copied {rel} from the skill")
        elif rel.endswith("gsap.min.js"):
            raise SystemExit("assets/vendor/gsap.min.js missing — run scripts/setup_assets.py")


class Tracks:
    """Same track index = no overlap, so adjacent clips must alternate. Groups keep
    unrelated layers off each other's tracks."""

    def __init__(self):
        self.base = {"hook": 0, "matte": 4, "aroll": 5, "broll": 6, "graphics": 10,
                     "kinetic": 14, "moments": 60, "msfx": 70, "scenes": 80, "ssfx": 90,
                     "music": 20, "sfx": 24, "flare": 30, "cap": 40,
                     "outro": 50, "osfx": 54}
        self.n = {k: 0 for k in self.base}

    def next(self, group, width=2):
        i = self.base[group] + (self.n[group] % width)
        self.n[group] += 1
        return i


def _rgb(h):
    """'#2F9BFF' -> '47, 155, 255' for rgba(var(--x-rgb), a) in the CSS."""
    h = str(h).lstrip("#")
    return ", ".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build(cfg, media, bounds, end):
    W = cfg["project"]["width"]
    H = cfg["project"]["height"]
    b = cfg["brand"]
    lang = cfg["language"]
    G = grid.from_config(cfg)
    gx0, gy0, gx1, gy1 = G["safe"]
    # the centred lane (grid.centered_box): x 140-940 on Reels, centred on the FRAME (540).
    # Cards and captions live in it, so they read as centred next to a centred speaker.
    lx0, lw = grid.center_lane(G)
    brand_css = ""
    bcss = b.get("css", "brand/brand.css")
    if bcss and os.path.exists(bcss):
        # Colours derived from the client's logo (scripts/brand_from_logo.py). They
        # override the defaults below, so every card, glow and highlight follows the brand.
        brand_css = open(bcss, encoding="utf-8").read()
    tr = Tracks()
    expected = {}
    body, tl = [], []

    # ------------------------------------------------------------------ outro
    # Opt-in (media.json "outro" or config outro.enabled; needs brand/brand.json with a
    # logo). It makes the COMPOSITION run past the A-roll by the outro tail; the A-roll
    # clip itself still ends at its real duration, and only outro clips (and the bed that
    # carries into it) may use the extra time — the END guard below still holds for the
    # rest. See scripts/outro.py and references/outro.md.
    global OUTRO_PLAN, KINETIC_PLAN, CAPTION_HIDE
    oplan = OUTRO_PLAN = outro.plan(cfg, media, end, beatmap)
    comp_end = oplan["end"] if oplan else end
    hide = CAPTION_HIDE = CaptionHide(end)
    hide.feed(media.get("hide_captions"), "media.json hide_captions")
    if oplan:                     # the outro is the brand's moment: no caption over it
        hide.add(float(oplan["start"]), end, "outro")

    def clip(tag, cid, cls, start, dur, group, extra="", inner="", self_close=False,
             limit=None, track=None):
        idx = tr.next(group) if track is None else track
        lim = end if limit is None else limit
        if start + dur > lim + 1e-6:
            raise SystemExit(f"{cid} runs to {start + dur:.3f}s, past END {lim:.3f}s")
        expected[cid] = [round(start, 3), round(dur, 3)]
        a = (f'<{tag} id="{cid}" class="clip {cls}" data-start="{round(start, 3)}" '
             f'data-duration="{round(dur, 3)}" data-track-index="{idx}"{extra}')
        return f'      {a}></{tag}>' if self_close is False and not inner else \
               f'      {a}>{inner}</{tag}>'

    # ------------------------------------------------------------------ hook
    hook = media.get("hook")
    if hook:
        he = float(hook["end"])
        # THE ONE-FRAME BOUNDARY RULE: videos INCLUSIVE, timed divs EXCLUSIVE.
        # Same number on both leaves a frame of un-washed roto; `end - 0.04` on both
        # kills the wash a frame early. Split them.
        hv, hd = round(he - FRAME, 3), round(he, 3)
        if hook.get("backdrop"):
            body.append(clip("video", "hookbg", "", 0.0, hv, "hook",
                             f' src="{hook["backdrop"]}" muted playsinline'))
        if hook.get("wash", True):
            body.append(clip("div", "hookwash", "", 0.0, hd, "hook"))
        if hook.get("matte"):
            body.append(clip("video", "matte", "", 0.0, hv, "matte",
                             f' src="{hook["matte"]}" muted playsinline'))
            # ONE STATIC TRANSFORM for the whole hook. A scale drift reads as
            # "you changed my position on the third second".
            s, y = hook.get("matte_scale"), hook.get("matte_y")
            if s is not None or y is not None:
                parts = []
                if s is not None:
                    parts.append(f"scale: {s}")
                if y is not None:
                    parts.append(f"y: {y}")
                tl.append(f'      tl.set("#matte", {{ {", ".join(parts)} }}, 0);')

    # ----------------------------------------------------------------- A-roll
    body.append(clip("video", "aroll", "", 0.0, end, "aroll",
                     f' src="{media["aroll"]}" data-has-audio="true" playsinline'))

    # Per-beat A-roll state, DERIVED from the beat map. Cuts are hard sets, exactly on
    # sentence starts.
    if beatmap and beatmap.BEATS:
        prev = None
        for start, kind, tag in beatmap.BEATS:
            if kind == "hook":
                continue
            state = ("scale: 1.02, y: 770" if kind == "panel" else "scale: 1.02, y: 0")
            if state != prev:
                tl.append(f'      tl.set("#aroll", {{ {state} }}, {round(start, 3)});'
                          f'   // {tag}')
                prev = state

    # ----------------------------------------------------------------- B-roll
    # B-ROLL HARD-CUTS IN AND SITS THERE — no entrance tween is emitted, ever.
    for c in media.get("broll", []):
        cls = "toppanel" if c.get("mode") == "panel" else "full"
        tag = "img" if str(c["src"]).lower().endswith((".png", ".jpg", ".jpeg")) else "video"
        extra = (f' src="{c["src"]}"' + ("" if tag == "img" else " muted playsinline"))
        if tag == "img":
            cls += " bimg"
        body.append(clip(tag, c["id"], cls, float(c["start"]), float(c["duration"]),
                         "broll", extra))
        warn_transition(cfg, c["id"], c.get("transition"))
        if c.get("hide_captions"):
            hide.add(float(c["start"]), float(c["start"]) + float(c["duration"]), c["id"])

    # --------------------------------------------------------------- graphics
    for g in media.get("graphics", []):
        gid, st, du = g["id"], float(g["start"]), float(g["duration"])
        body.append(clip("div", gid, g.get("class", "card"), st, du, "graphics",
                         inner=g.get("html", "")))
        rev = g.get("reveal", "unblur")
        if rev == "unblur":
            # Reveals are an UN-BLUR, not a pop. The contents are frosted, so the card's
            # own rounded edge stays crisp. Fire on the CUT — a panel must never sit
            # empty waiting for its pop.
            tl.append(f'      tl.set("#{gid} .inner", {{ filter: "blur(20px)" }}, {st});')
            tl.append(f'      tl.to("#{gid} .inner", {{ filter: "blur(0px)", '
                      f'duration: 0.34, ease: "power2.out" }}, {st});')
        elif rev == "pop":
            tl.append(f'      tl.fromTo("#{gid}", {{ scale: 0.35, opacity: 0 }}, '
                      f'{{ scale: 1.10, opacity: 1, duration: 0.26, ease: "power4.out" }}, {st});')
            tl.append(f'      tl.to("#{gid}", {{ scale: 1.0, duration: 0.20, '
                      f'ease: "back.out(3)" }}, {round(st + 0.26, 3)});')
        # GSAP exits that end at a clip boundary need a hard kill
        # (lint: gsap_exit_missing_hard_kill).
        tl.append(f'      tl.set("#{gid}", {{ opacity: 0 }}, {round(st + du, 3)});')
        warn_transition(cfg, gid, g.get("transition"))
        if g.get("hide_captions"):
            hide.add(st, st + du, gid)

    # -------------------------------------------------------------- headlines
    # Kinetic headlines (scripts/kinetic.py, references/kinetic.md): the key sentence of a
    # beat builds word by word as it is spoken. Word times come from src/words.json, size
    # from a Chrome measurement, place from the grid. Each hides the captions over its
    # window unless it says "hide_captions": false.
    kplan = KINETIC_PLAN = kinetic.plan(cfg, media, end, beatmap, bounds)
    if kplan:
        for el in kplan["elements"]:
            body.append(clip(el["tag"], el["id"], el["cls"], el["start"], el["dur"],
                             "kinetic", el.get("extra", ""), el.get("inner", "")))
        tl.extend(kplan["js"])
        hide.feed(kplan["hide"])
    elif (cfg.get("style") or {}).get("kinetic"):
        print("  ! style.kinetic is on (the reference builds headlines word by word) but "
              "media.json has no \"headlines\" — add 2-4 on the key sentences "
              "(references/kinetic.md)")

    # ---------------------------------------------------------------- moments
    # Designed moments (scripts/moments.py): word-synced, brand-coloured scenes. Moments
    # can overlap each other, so every element carries its own lane. They come AFTER the
    # beat-state tl.set lines so a moment wins when both land on the same frame.
    global MOMENTS_PLAN
    mplan = MOMENTS_PLAN = None
    if media.get("moments"):
        mctx = moments.make_ctx(cfg, media, end, beatmap)
        mplan = MOMENTS_PLAN = moments.collect(media["moments"], mctx)
        for el in mplan["elements"]:
            body.append(clip(el["tag"], el["id"], el["cls"], el["start"], el["dur"], "moments",
                             el.get("extra", ""), el.get("inner", ""),
                             track=tr.base["moments"] + el["lane"]))
        for sx in mplan["sfx"]:
            body.append(clip("audio", sx["id"], "sfx", sx["start"], sx["duration"], "msfx",
                             f' src="{sx["src"]}" data-volume="{sx["volume"]}"',
                             track=tr.base["msfx"] + sx["lane"]))
        tl.extend(mplan["timeline"])
        hide.feed(mplan.get("hide_captions"), "moments")
        for t in mplan.get("transitions", []):
            warn_transition(cfg, t.get("id", "moment"), t.get("name"), emitted=True)

    # ----------------------------------------------------------------- scenes
    # Per-video designed moments (scripts/scenes.py loads the PROJECT's scenes.py and
    # media.json "scenes", built on scripts/kit.py; method in references/storyboard.md).
    # After the moments so a scene's camera move wins a same-frame tie; every scene is one
    # clip on its own lane (base 80), its SFX on lanes 90+, its hidden-caption windows fed
    # to the same CaptionHide as everything else.
    global SCENES_PLAN
    splan = SCENES_PLAN = scenes.plan(cfg, media, end, beatmap, bounds)
    if splan:
        for el in splan["elements"]:
            body.append(clip(el["tag"], el["id"], el["cls"], el["start"], el["dur"], "scenes",
                             el.get("extra", ""), el.get("inner", ""),
                             track=tr.base["scenes"] + el["lane"]))
        for sx in splan["sfx"]:
            body.append(clip("audio", sx["id"], "sfx", sx["start"], sx["duration"], "ssfx",
                             f' src="{sx["src"]}" data-volume="{sx["volume"]}"',
                             track=tr.base["ssfx"] + sx["lane"]))
        tl.extend(splan["js"])
        hide.feed(splan["hide"], "scenes")

    # ----------------------------------------------------------------- flares
    # A flare is a full-frame screen-blended clip; x / y / scale / rotation / opacity place
    # it (flare_placement). Static placement is ONE inline CSS transform — no timeline
    # entry, nothing to drift on a seek. Only "drift" puts the flare on the timeline, and
    # then the start state is a tl.set instead (a CSS transform on an element GSAP tweens
    # is the gsap_css_transform_conflict lint: GSAP would parse it and fight it).
    for f in media.get("flares", []):
        fs, fd = float(f["start"]), float(f["duration"])
        style, set_js, drift_js = flare_placement(f, fs, fd)
        body.append(clip("video", f["id"], "flare", fs, fd, "flare",
                         f' src="{f["src"]}" muted playsinline{style}'))
        tl.extend(set_js + drift_js)

    # ------------------------------------------------------------------ audio
    audio = media.get("audio", {})
    # With an outro, the bed that plays to the A-roll's end is carried to the outro's last
    # frame and fades out ON it (outro.extend_bed) — media.json keeps its spoken-part length.
    # A bed from scripts/bed.py is "baked": already shaped to the composition end, outro
    # lift included — extending and automating it again would shape it twice.
    bed_id = outro.bed_to_extend([m for m in audio.get("music", []) if not m.get("baked")],
                                 end) if oplan else None
    for group in ("music", "sfx"):
        for s in audio.get(group, []):
            ms = s.get("media_start")
            # An <audio> element REQUIRES an id or it renders silent.
            extra = f' src="{s["src"]}"'
            if ms is not None:
                extra += f' data-media-start="{ms}"'
            if s.get("volume") is not None:
                extra += f' data-volume="{s["volume"]}"'
            dur, lim = float(s["duration"]), None
            if group == "music" and bed_id is not None and s.get("id") == bed_id:
                dur, lane = outro.extend_bed(s, oplan, cfg)
                extra += lane
                lim = comp_end
            elif group == "music" and s.get("baked"):
                lim = comp_end           # the baked bed already runs to the composition end
            body.append(clip("audio", s["id"], group, float(s["start"]),
                             dur, group, extra, limit=lim))

    # ------------------------------------------------------------- outro clips
    if oplan:
        for el in oplan["elements"]:
            body.append(clip(el["tag"], el["id"], el.get("cls", ""), el["start"], el["dur"],
                             "outro", el.get("extra", ""), el.get("inner", ""), limit=comp_end))
        for s in oplan["sfx"]:
            body.append(clip("audio", s["id"], "sfx", s["start"], s["duration"], "osfx",
                             f' src="{s["src"]}" data-volume="{s["volume"]}"', limit=comp_end))
        tl.extend(oplan["js"])

    # --------------------------------------------------------------- captions
    caps_mode = media.get("captions", "external")
    if caps_mode == "inline" and os.path.exists("captions.json"):
        caps = json.load(open("captions.json", encoding="utf-8"))
        cap_track = tr.base["cap"]
        for c in caps:
            # The class comes from slot_at() — never from a second, hand-kept table.
            slot = beatmap.slot_at(c["start"]) if beatmap and beatmap.BEATS else "std"
            size = f' style="font-size:{c["size"]}px"' if c.get("size") else ""
            expected[f"c{c['i']:02d}"] = [round(c["start"], 3), round(c["dur"], 3)]
            body.append(f'      <div id="c{c["i"]:02d}" class="clip cap {slot}" data-center="content" '
                        f'data-start="{c["start"]}" data-duration="{c["dur"]}" '
                        f'data-track-index="{cap_track}">'
                        f'<span class="p"{size}>{c["text"]}</span></div>')
        # caption hiding, inline mode: hard sets on the whole caption track (seek-safe
        # both ways); the external layer reads build/caption_hide.json instead
        for a_, b_, src in hide.merged():
            tl.append(f'      tl.set(".cap", {{ opacity: 0 }}, {a_});   // hide: {", ".join(src)}')
            tl.append(f'      tl.set(".cap", {{ opacity: 1 }}, {b_});')

    # ------------------------------------------------------------------- CSS
    slots = dict(beatmap.SLOT) if beatmap else {}
    cy = cfg.get("captions", {}).get("center_y")
    ph = grid.plate_height(b["caption_size"])
    for k in grid.SPEAKER_SLOTS:          # speaker slots follow the configured size/band
        if k in slots:
            slots[k] = round(float(cy) - ph / 2) if cy else grid.caption_top(G, ph)
    slot_css = "\n".join(
        f'      .cap.{k} {{ top: {v}px; }}'
        for k, v in sorted(slots.items(), key=lambda x: x[1]))

    matte_css = ""
    if hook and hook.get("matte"):
        mw = hook.get("matte_width")
        ml = hook.get("matte_left")
        if mw and ml is not None:
            # transform-origin must be the FRAME's bottom-centre expressed in ELEMENT
            # coordinates, or the scale pivots off-centre and the subject slides sideways.
            origin = round((W / 2 - ml) / mw * 100, 1)
            matte_css = (f'      #matte {{ z-index: 30; inset: auto; left: {ml}px; top: 0;\n'
                         f'               width: {mw}px; height: {H}px; object-fit: fill;\n'
                         f'               transform-origin: {origin}% 100%; }}')
        else:
            matte_css = '      #matte { z-index: 30; transform-origin: 50% 100%; }'

    # Free fonts only, copied into the PROJECT (the page loads them by relative URL): the
    # caption face, the optional display face, Roboto Slab for "AI" and Inter as fallback.
    import fonts
    fams = [b["font_family"], b.get("display_family"), "Roboto Slab", "Inter"]
    missing = fonts.ensure(fams, b["font_dir"])
    if missing:
        raise SystemExit(f"font(s) unavailable: {missing} — run scripts/fonts.py fetch")
    font_css = fonts.font_faces_css(fams, b["font_dir"],
                                    url_prefix=b["font_dir"].rstrip("/") + "/")
    display = b.get("display_family") or b["font_family"]

    html = f"""<!doctype html>
<!-- GENERATED by scripts/build_index.py — do not hand-edit.
     Every timed value is derived from scripts/beats.py + media.json. Edit those and
     rebuild; a hand-edit here is a stranded time waiting to happen. -->
<html lang="{lang['code']}">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={W}, height={H}" />
    <title>{esc(media.get('title', 'reel'))}</title>
    <script src="assets/vendor/gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      /* Brand tokens. Defaults = the house teal/blue look; brand/brand.css (generated
         from the logo) overrides them. Use the tokens, never a hard-coded hex, in any
         card or highlight you add. */
      :root {{ --brand-primary: {b['accent']}; --brand-primary-rgb: {_rgb(b['accent'])};
               --brand-secondary: {b['accent_alt']}; --brand-secondary-rgb: {_rgb(b['accent_alt'])};
               --brand-accent: {b['accent_warm']}; --brand-accent-rgb: {_rgb(b['accent_warm'])};
               --brand-ink: #0a0a0a; --brand-paper: #f7f5f0; --brand-on-primary: #0a0a0a;
               --hl-on-dark: {b['accent']}; --hl-on-light: #1B6FBE;
               --brand-grad-a: #0B3E86; --brand-grad-b: #061224;
               --brand-font: "{b['font_family']}"; --display-font: "{display}"; }}
{brand_css}
      html, body {{ margin: 0; width: {W}px; height: {H}px; overflow: hidden; background: #000; }}
      /* A variable font needs the full weight RANGE, or Chrome clamps it to one
         instance and every weight renders identically. */
{font_css}
      #root {{ position: relative; width: {W}px; height: {H}px; overflow: hidden; background: #000; }}
      video {{ position: absolute; inset: 0; width: {W}px; height: {H}px; object-fit: cover; }}

      #hookbg {{ z-index: 5; }}
      #hookwash {{ position: absolute; inset: 0; z-index: 6;
        background:
          radial-gradient(90% 62% at 22% 14%, rgba(var(--brand-primary-rgb),0.34) 0%, rgba(var(--brand-primary-rgb),0) 62%),
          radial-gradient(88% 66% at 84% 30%, rgba(var(--brand-secondary-rgb),0.36) 0%, rgba(var(--brand-secondary-rgb),0) 60%),
          linear-gradient(168deg, rgba(9,38,58,0.62) 0%, rgba(6,22,40,0.70) 52%, rgba(4,12,24,0.80) 100%); }}
{matte_css}
      #aroll {{ z-index: 20; transform-origin: 50% 30%; }}

      /* Qualify the panel rule to (0,2,1) — `video, img.bimg {{ inset:0; height:{H}px }}`
         is (0,1,1) and would otherwise win, rendering every panel full frame. */
      video.full, img.bimg.full {{ z-index: 20; }}
      video.toppanel, img.bimg.toppanel {{ z-index: 22; inset: auto; left: 0; top: 0;
        width: {W}px; height: 900px; object-fit: cover; }}

      /* EVERY card surface must set font-family itself — a new class does NOT inherit
         it and the whole card silently renders in the default serif. Name Inter (free,
         OFL), never a commercial system face as the fallback. */
      /* Cards live inside the Reels safe zone (scripts/grid.py): x {gx0}-{gx1}, y {gy0}-{gy1},
         centred on the FRAME (x {G['center_x']}) in the {lw} px lane x {lx0}-{lx0 + lw}
         (grid.centered_box): the right edge stays clear of the like/comment/share rail. */
      .card {{ position: absolute; z-index: 40; left: {lx0}px; width: {lw}px;
        font-family: var(--brand-font), "Inter", sans-serif;
        background:
          radial-gradient(96% 90% at 50% 0%, rgba(var(--brand-primary-rgb),.30), rgba(var(--brand-primary-rgb),0) 70%),
          linear-gradient(163deg, var(--brand-grad-a), var(--brand-grad-b));
        border-radius: 34px; border: 2px solid rgba(var(--brand-primary-rgb),.30);
        box-shadow: 0 34px 92px rgba(0,0,0,.82), 0 0 90px rgba(var(--brand-primary-rgb),.20);
        padding: 60px 54px 200px; }}   /* leave the caption band empty */
      .card.ctr {{ top: 380px; }}
      /* Bottom card (Reels grid): anchored to the safe line y {gy1} and growing UP, at
         most {G['bottom_card_max_h']} px, so it sits under the caption band and never covers
         the face. The house choice when the speaker should stay on screen. */
      .card.low {{ top: auto; bottom: {H - gy1}px; max-height: {G['bottom_card_max_h']}px;
                   padding: 26px 40px 28px; border-radius: 28px; overflow: hidden; }}
      .card.low .kicker {{ font-size: 30px; }}
      .card.low .huge {{ font-size: 92px; line-height: 1.05; }}
      .card.low .sub {{ font-size: 32px; }}
      .card.panel {{ top: {max(90, gy0)}px; }}
      .card .kicker {{ font-size: 34px; color: var(--hl-on-dark); font-weight: 700; }}
      .card .huge {{ font-size: 168px; color: #fff; font-weight: 900;
                     text-shadow: 0 0 46px rgba(var(--brand-primary-rgb),.55); }}
      .card .sub {{ font-size: 40px; color: var(--hl-on-dark); opacity: .85; font-weight: 600; }}
      /* Highlighted words: on footage or dark cards use --hl-on-dark, on paper/light
         cards use --hl-on-light. Both are contrast-checked by brand_from_logo.py. */
      .hl {{ color: var(--hl-on-dark); }}
      .on-light .hl {{ color: var(--hl-on-light); }}

      /* An inline <svg class="clip"> renders across the WHOLE video — `.clip` does not
         hide svg. Start hidden and drive opacity from the timeline. */
      svg.clip {{ opacity: 0; }}

      /* Captions centre on the FRAME (x {G['center_x']}) in the {lw} px lane x {lx0}-{lx0 + lw}
         (grid.centered_box; fit_captions.py keeps every plate inside {lw} px). The lane is
         laid out RTL whatever the language: a plate that is still too wide then keeps its
         right edge on {lx0 + lw} and grows LEFT, clear of the action rail — the rule for a
         wide element. The plate itself carries the language's direction. */
      .cap {{ position: absolute; left: {lx0}px; width: {lw}px; z-index: 50; text-align: center;
             direction: rtl;
             font-family: var(--brand-font), "Inter", sans-serif;
             font-weight: 800; line-height: 1.0; }}
      .cap .p {{ display: inline-block; font-size: {b['caption_size']}px; direction: {lang['direction']};
                padding: 20px 34px 26px; white-space: nowrap; {grid.caption_css(cfg)} }}
{slot_css}
      .ltr {{ unicode-bidi: isolate; direction: ltr; }}
      /* "AI" in a heavy Hebrew face reads as "Al" (capital I has no serif): slab it. */
      .ai {{ font-family: "Roboto Slab", serif; font-weight: 800; letter-spacing: .02em; }}

      .flare {{ z-index: 60; mix-blend-mode: screen; }}
{kplan["css"] if kplan else ""}
{oplan["css"] if oplan else ""}
{mplan["css"] if mplan else ""}
{splan["css"] if splan else ""}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{round(comp_end, 3)}"
         data-width="{W}" data-height="{H}">
{chr(10).join(body)}
    </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
{chr(10).join(tl)}
      window.__timelines = window.__timelines || {{}};
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""
    return html, expected


def main():
    ap = hfcfg.arg_parser(__doc__)
    ap.add_argument("--media", default="media.json")
    ap.add_argument("--out", default="index.html")
    ap.add_argument("--bounds", default="src/bounds.json")
    ap.add_argument("--example", action="store_true",
                    help="print a starting media.json and exit")
    a = ap.parse_args()

    if a.example:
        print(json.dumps(EXAMPLE, indent=2))
        return 0

    cfg = hfcfg.load(a.config)
    if not os.path.exists(a.media):
        sys.exit(f"{a.media} not found — start one with:\n"
                 f"    python3 scripts/build_index.py --example > {a.media}")
    media = json.load(open(a.media, encoding="utf-8"))
    media = {k: v for k, v in media.items() if not k.startswith("_comment")}
    ensure_project_assets(media)

    if not os.path.exists(a.bounds):
        sys.exit(f"{a.bounds} not found — run scripts/cut_aroll.py first")
    d = json.load(open(a.bounds, encoding="utf-8"))
    bounds, end = d["bounds"], d["total"]

    # Composition duration = the A-ROLL's REAL duration. Setting it short silently
    # chops the last word.
    if beatmap:
        beatmap.END = end
        if beatmap.BEATS:
            # ASSERT EVERY BEAT START IS A REAL SEGMENT BOUNDARY before building
            # anything. A beat mid-sentence is a cut that fires early.
            beatmap.assert_on_boundaries(bounds)
        else:
            print("  ! scripts/beats.py has no beats yet — the A-roll will hold one state "
                  "for the whole reel. Fill in BEATS, then rebuild.")

    html, expected = build(cfg, media, bounds, end)
    open(a.out, "w", encoding="utf-8").write(html)
    os.makedirs("build", exist_ok=True)
    json.dump(expected, open("build/expected.json", "w"), indent=1)
    # finish.py reads this to stop the caption layer at the outro (and the validator to
    # allow outro clips past the A-roll). No outro → no file, so a stale one cannot apply.
    op = "build/outro.json"
    if OUTRO_PLAN:
        json.dump({k: OUTRO_PLAN[k] for k in ("style", "start", "end", "aroll_end",
                                              "freeze_start", "info")},
                  open(op, "w"), indent=1, ensure_ascii=False)
        print(f"  outro: {OUTRO_PLAN['style']} {OUTRO_PLAN['start']:.2f}s → "
              f"{OUTRO_PLAN['end']:.2f}s (composition END; A-roll ends {end:.3f}s) → {op}")
    elif os.path.exists(op):
        os.remove(op)
    # caption_layer.py reads this: the caption layer is transparent inside each window
    mp = "build/moments.json"
    if MOMENTS_PLAN:
        json.dump({"hide_captions": MOMENTS_PLAN.get("hide_captions", []),
                   "transitions": MOMENTS_PLAN.get("transitions", [])},
                  open(mp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    elif os.path.exists(mp):
        os.remove(mp)
    sp = "build/scenes.json"     # the scene table + every SFX cue, for QA transition stills
    if SCENES_PLAN:
        json.dump({"scenes": SCENES_PLAN["table"], "hide_captions": SCENES_PLAN["hide"],
                   "sfx": [{k: s[k] for k in ("id", "name", "start", "volume", "kind")}
                           for s in SCENES_PLAN["sfx"]], "notes": SCENES_PLAN["notes"]},
                  open(sp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    elif os.path.exists(sp):
        os.remove(sp)
    hw = CAPTION_HIDE.write("build/caption_hide.json")
    if hw:
        print(f"  captions hidden over {len(hw)} window(s) → build/caption_hide.json: "
              + ", ".join(f"{a:.2f}-{b:.2f}" for a, b, _ in hw))
    if KINETIC_PLAN:
        for hid, t in KINETIC_PLAN["table"].items():
            ws = "  ".join(f"{w}@{x:.2f}" for (w, _, _), x in zip(t["words"], t["times"]))
            print(f"  headline {hid} {t['start']:.2f}-{t['end']:.2f}s {t['size']}px top "
                  f"{t['top']}: {ws}")
        for w in KINETIC_PLAN["warnings"]:
            print(f"  ! headline {w}")

    print(f"  beat map: {BEATS_PATH or '(none found)'}"
          f"  {len(beatmap.BEATS) if beatmap else 0} beats")
    # two different ends: the A-roll's (the last spoken frame; captions, scenes and SFX
    # live before it) and the composition's (the outro runs past it). Printing only the
    # first once read as "the composition is 4 s short".
    comp_end = OUTRO_PLAN["end"] if OUTRO_PLAN else end
    print(f"  {a.out}  {len(expected)} timed elements, A-roll ends {end:.3f}s, "
          f"composition END {comp_end:.3f}s"
          + (f" (+{comp_end - end:.2f}s outro)" if comp_end > end + 1e-6 else ""))
    print(f"  build/expected.json written — now run:")
    print(f"    python3 scripts/validate.py --expect build/expected.json --html {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
