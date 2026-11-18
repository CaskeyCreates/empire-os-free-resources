# AI Ad Editor

Turn your client's raw talking-head video into finished video ads by talking to Claude.

Put the video in a folder, say "edit this into an ad", and you get three ads back: 9:16 for Reels and Stories, 4:5 for the feed and 1:1 square. Each one has the silences and retakes cut (never a word you kept), captions timed to every word in your client's own colours and fonts, real B-roll on the lines that need it, a music bed under the voice, the offer on the first frame and an end card.

**The step by step guide is [GUIDE.md](GUIDE.md).** Start there.

Tested on a Mac. It's built for Windows too, but we haven't tested it on Windows yet.

## Setup, in short

You need Claude Code (a Pro plan or higher), an ElevenLabs account and a free Pixabay account. Then, in Claude Code:

> Set up the AI Ad Editor from https://github.com/CaskeyCreates/empire-os-free-resources, install what it needs, and run the doctor.

Add your two keys to `ai-ad-editor/.env` yourself (copy `.env.example`). Never paste a key into the chat. Open Claude Code in the `ai-ad-editor` folder from then on, so it finds its editing skills.

This project uses [uv](https://docs.astral.sh/uv/) for Python. Use `uv sync` and `uv run`, not plain `pip`: one of its pins only works through uv.

## Using it

Three things you say to Claude:

1. **"Set up a new client called Acme from acme.com."** It reads their site for colours, fonts and logo, asks for the offer, makes their music once, and waits for your yes.
2. **"Edit the new video for Acme into an ad."** It cuts, plans, picks B-roll by looking at it, renders all three ratios, checks them and looks at the stills before it tells you it's done.
3. **Notes, in plain words.** "A bit faster." "Music lower." "Swap the shot on school." It changes only that.

Your ads land in `clients/<client>/ads/<video>/` with a review sheet.

## What it never does

- Post anything anywhere.
- Use AI-generated footage, faces or voices.
- Cut a word you kept. It listens to the cut again to prove it, and if two words ran together at a cut, it tells you the time so you can listen too.
- Put a caption over the speaker's mouth. When a take is framed too tightly for that, the check fails and tells you to ask for a wider take.
- Put a price, a guarantee or fine print on the video.

## Where the footage and tools come from

- **B-roll** comes from [Pixabay](https://pixabay.com) by default, or from [Pexels](https://www.pexels.com) if you already have a Pexels key (Pexels has paused new keys), or from your client's own licensed clips in `clients/<client>/broll/own/`. Every clip used is credited in the review sheet.
- **Transcription and music:** [ElevenLabs](https://elevenlabs.io).
- **Rendering:** [HyperFrames](https://github.com/heygen-com/hyperframes) and [ffmpeg](https://ffmpeg.org).
- **Faces:** [MediaPipe](https://ai.google.dev/edge/mediapipe) face detection.
- **Editing decisions:** Claude, in your own Claude Code.

## For developers

```
uv sync
uv run pytest -q
uv run pytest -q -m render   # renders with HyperFrames, needs Chrome
uv run adeditor --help
```

## Licence

MIT, see [LICENSE](../LICENSE). The example client's fonts are under the SIL Open Font License; see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

Made by [Empire OS](https://goempireos.com).
