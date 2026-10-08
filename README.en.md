# HAPORTAL - VIDEO EDITOR

**עברית:** [README.md](README.md)

A Claude Code skill that turns a raw talking-head recording into a finished, professional
vertical reel for Instagram Reels, TikTok and YouTube Shorts.

You give it the footage. It cuts, designs, captions, scores and adds sound design and motion
graphics, then masters a high-quality file, following the rules of a professional editor. It
was built Hebrew-first and detects any other language on its own.

---

## What it does

**Cuts tightly to the speech.** No dead air, no breaths after a cut, no clipped word endings.
When you said a line more than once, it keeps the best take.

**Adapts to your reference.** Give it a reel whose style you like, or your own past videos. It
measures the pace, the caption look and position and the music level, and adapts the edit. It
copies the editing language, never the content.

**Instagram Reels grid.** No text or logo ever lands under the buttons, the username or the app
header. An automatic check stops the build if anything crosses into those zones. Elements that
fit are truly centred on the frame.

**Free fonts only.** Every font that reaches the video is licensed for commercial use. The skill
refuses paid fonts, so you never get into licensing trouble.

**Brand colours from your logo.** Give it a logo and it extracts the brand colours, then applies
them to the graphics, the animations and the highlighted words, and checks the text stays
readable on every background.

**Animated logo outro.** If you have a logo and want one, the video ends on an animation: the
frame closes into a door that flies into the logo's symbol, and the logo builds around it,
centred. Three more styles are available.

**Kinetic headlines.** The key sentence builds on screen word by word, exactly as each word is
spoken, with the keyword in your brand colour.

**Designed moments.** Every line is illustrated by a concrete, witty UI moment invented from what
is being said: an inbox, a calendar that keeps postponing, an approval dialog with a tap, a
waiting room, a progress bar, a checklist, a stamp, chips, a search bar, and the frame flying into
a designed world for the hook.

**Music made for each video.** With an ElevenLabs API key, the skill generates original music
shaped around the story, with the big change landing exactly on the turn. Without a key, give it a
track you are licensed to use.

**Any language.** The language is detected from the video. Captions, headlines and graphics follow
its reading direction (left-to-right or right-to-left). Hebrew gets extra care: a Hebrew-tuned free
transcriber, captions that break at the right place, and "AI" that never reads as "Al".

---

## Requirements

1. A Mac (Apple Silicon recommended) or Linux.
2. Claude Code.
3. Google Chrome.
4. A few GB of free disk space for the transcription model.

The skill checks and installs everything else itself.

---

## Install

Open a terminal and run these three lines, one after the other.

Download the skill into Claude's skills folder:

```
git clone https://github.com/a-b-remake-ltd/haportal-video-editor ~/.claude/skills/haportal-video-editor
```

Go into the folder:

```
cd ~/.claude/skills/haportal-video-editor
```

Run the automatic setup:

```
python3 scripts/doctor.py --install
```

Setup checks what is missing, installs what it safely can, and tells you exactly what to do about
anything it can't. It ends with a green checklist.

You can also simply ask Claude: "install the HAPORTAL video editor skill" and give it the link.

---

## How to use it

1. Create a new folder for the video and put the raw recording in it. If you have them, add B-roll,
   a logo and a reference video.
2. Open the folder in Claude Code.
3. Type: "edit my video into a reel".

Claude goes through the material and comes back with a few short questions:

* Is there a reference video whose style you like?
* Do you have a logo? The colours will follow your brand.
* Would you like an animated logo outro?
* Is the reel organic, or will it also run as a paid ad?

It then proposes a plan in a few sentences and starts editing only after you approve it. Before it
shows you anything, it checks the result itself: every cut, the grid, the captions, the loudness,
frozen frames, and whether every word can still be heard over the music.

Send notes in plain words, for example "the captions are too big" or "you cut early at 0:12". It
fixes the problem and looks for the same mistake in the rest of the video.

---

## Tips for the best result

* **Record in good light and a quiet room.** The skill can't rescue bad audio.
* **If you stumble, just say the line again.** It keeps the last take.
* **A reference helps a lot.** One video you like is worth more than any description.
* **A PNG logo with a transparent background** gives the most accurate colours and outro.
* **Music:** generated automatically with an ElevenLabs key; without one, give it a track you are
  licensed to use.

---

## Credits

Built by Ben Daskalo, HAPORTAL (haportal.ai).

Based on two open-source projects, both MIT-licensed:

* Omer Yaron's `ai-video-editor` skill — the foundation for cutting, layout, captions and sound.
* Browser Use's `video-use` — the working method: reading the transcript, approving a plan before
  editing, and self-evaluating the rendered file.

Full details in `NOTICE.md` and `LICENSE`.
