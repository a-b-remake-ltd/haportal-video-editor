# Credits

**HAPORTAL - VIDEO EDITOR** by Ben Daskalo ([haportal.ai](https://haportal.ai)).

It stands on two open-source projects, both MIT-licensed:

- **ai-video-editor** by **Omer Yaron**. The foundation: the cutting method (onset-precise,
  frame-exact segments), the layout system, captions, sound design, the HyperFrames build
  and the QA gates. Most of `scripts/` and `references/` started as his work.
- **video-use** by **Browser Use** ([github.com/browser-use/video-use](https://github.com/browser-use/video-use)).
  The working method: the transcript as the primary reading view, strategy approval before
  cutting, self-evaluation of the rendered file at every cut, and session memory.
  `pack_transcript.py` and `timeline_view.py` are adapted from its helpers.

Added in HAPORTAL:

- Reference analysis, so the edit adapts to a video the user supplies
- The Instagram Reels grid, as one module plus a gate
- Free fonts only: a registry, a licence gate and Hebrew pairings
- Brand colours from a logo: roles, contrast-checked highlights and logo variants
- The animated logo outro (opt-in)
- Kinetic word-by-word headlines and a library of designed moments (paper page turn, question
  card, checklist, stamp, chips, search bar, frame fly, punch-ins)
- Hebrew-first defaults: the ivrit.ai transcriber, sticky-word caption breaks, the "AI" glyph
  fix, breath and lip-sync gates
- One-command setup (`scripts/doctor.py`)
