# The AI Ad Editor: turn your client's raw video into finished ads with Claude

If you run ads for clients, you know how this goes. They send you a video of themselves talking to the camera, with the false starts, the long pauses and the "um"s still in it, and you've got to turn it into an ad that looks like it was made by a pro.

This guide sets up Claude to do that edit for you. You put the video in a folder, you tell Claude "edit this into an ad", and you get three finished ads back: 9:16 for Reels and Stories, 4:5 for the feed, and 1:1 square. Each one has the dead bits cut out, captions timed to every word in your client's own colours and fonts, real B-roll on the lines that need it, a music bed sitting under the voice, and the offer on the first frame.

You don't need to code. You'll install a few things (Claude does most of it for you), open an ElevenLabs account and a free Pixabay account, and then you just talk to Claude.

**Two ways to use this guide.** Follow the steps yourself, or paste this whole guide into Claude Code and say "walk me through this, one step at a time". Either works.

---

## What you get

For every video, three ads that:

- **Cut only the silence and the retakes, never a word.** When your client says a line twice, the last full take stays. Every cut lands in a real pause, and the tool listens to the finished cut again to prove every kept word is still there.
- **Caption every word as it's said,** in your client's own fonts and colours, read from their website. Captions sit under the chin and never over the mouth.
- **Show real B-roll on the exact line it belongs to,** picked by Claude from free stock footage by actually looking at the clips.
- **Open on the offer** (who it's for and what they get) and **close on an end card** with your button word, "Learn more" by default.
- **Sit music under the voice,** made once per client, about 15 dB under so the voice always comes first, and level the whole mix for social.
- **Keep the speaker whole in every ratio.** If the take has no room above the head, the text moves into a band at the top, or the video sits as a framed card, so nobody gets cut off.

You also get a review sheet with stills from every ratio, the checks it ran, and the credits for the footage it used.

## What it never does

- Post anything anywhere. You hand the ads to your client or upload them yourself.
- Use AI-generated footage, faces or voices.
- Put a price, a guarantee or fine print on the video. Those belong on the landing page.
- Touch your client's original video. It reads it and leaves it alone.

---

## Step 1. What you need

| | Why | Where |
|---|---|---|
| A computer | Mac or Windows. We've tested it on a Mac. It's built for Windows too, but we haven't tested it there yet. | |
| A Claude plan with Claude Code | Claude does the editing. Claude Code comes with the Pro, Max, Ultra, Team and Enterprise plans; the free plan doesn't include it. | [claude.com/pricing](https://claude.com/pricing) |
| An ElevenLabs account | It transcribes every word with its timing, and makes your client's music. To use the music in client ads you need the Starter plan or higher, because the free plan doesn't include commercial use of music. | [elevenlabs.io/pricing](https://elevenlabs.io/pricing) |
| A Pixabay account | Free stock footage for the B-roll, and a free API key. | [pixabay.com](https://pixabay.com) |

Everything else in this guide is free.

## Step 2. Install Claude Code

The easiest way is the Claude desktop app, which has Claude Code built in. Download it from [claude.com/download](https://claude.com/download), sign in, and open the **Code** tab.

If you'd rather use a terminal, the install steps are on [code.claude.com/docs/en/setup](https://code.claude.com/docs/en/setup).

On Windows, also install [Git for Windows](https://git-scm.com/downloads/win). Claude Code works better with it.

## Step 3. Get your two keys ready

You'll paste these into a file in Step 5, not into the chat.

1. **ElevenLabs:** sign in, then open [elevenlabs.io/app/api/api-keys](https://elevenlabs.io/app/api/api-keys) and create a key. If you limit what the key can do, leave Speech to Text and Music switched on.
2. **Pixabay:** sign in at [pixabay.com](https://pixabay.com), then open [pixabay.com/api/docs](https://pixabay.com/api/docs/). Your key is shown on that page.

## Step 4. Let Claude set it up

In Claude Code, paste this and send it:

> Set up the AI Ad Editor for me from https://github.com/CaskeyCreates/empire-os-free-resources. Clone it into my Documents folder. Install whatever it needs that I don't have yet: Git, uv, ffmpeg and Node.js (the LTS version). Then go into its `ai-ad-editor` folder, run `uv sync`, copy `.env.example` to `.env`, and run `uv run adeditor doctor --fix`. Fix anything the doctor reports, except the two keys, which I'll add myself. Before each step, tell me in plain words what you're about to do.

Claude will ask before it installs anything. Say yes to each step. It takes about 5 to 15 minutes, mostly downloads.

When it's done, the tool lives in `Documents/empire-os-free-resources/ai-ad-editor`. That's the folder the rest of this guide calls `ai-ad-editor`.

## Step 5. Add your keys

Open the `ai-ad-editor` folder, then the file called `.env` (on a Mac it's hidden: press Cmd, Shift and full stop in Finder to show it). It looks like this:

```
ELEVENLABS_API_KEY=
PIXABAY_API_KEY=
```

Paste each key straight after its `=` sign, with no spaces, and save the file.

**Never paste a key into the chat.** Claude doesn't need to see it, and the tool reads it from this file.

Then, in Claude Code, open the `ai-ad-editor` folder (in the desktop app, choose it as the folder to work in), and say:

> Run the doctor.

Every line should say ok. If you haven't added a Pixabay key, the B-roll line says warn: you can still make ads, just without stock B-roll. If any other line doesn't say ok, it tells you what to do.

**From now on, always open Claude Code in the `ai-ad-editor` folder.** That's how Claude finds its editing instructions.

## Step 6. Set up your first client

Say:

> Set up a new client called Northbank Physio from northbankphysio.com

(with your client's real name and website). Claude reads their site for their colours, fonts and logo, then:

- Asks you to open the colour sheet it made and check it looks like their brand. If a colour's wrong, tell Claude the right one and it fixes it.
- Asks for the offer: who the ad is for, the offer line, an optional detail, and the button word.
- Asks for any names or products the transcriber might mishear.
- Makes their music once, from a mood you describe. If you have a licensed track you'd rather use, give it the file instead.
- Asks for your yes before anything renders.

If Claude says it couldn't read their site, give it another page of the same site, like their About page. Or just tell it their main colour and accent colour. If it didn't find their logo, drop the logo file (PNG or SVG) into the brand folder it names; without one, the end card shows their name instead.

## Step 7. Make your first ad

Put your client's video in `ai-ad-editor/clients/<client>/raw/`. If your client uploads to a shared Google Drive folder, you can make that folder sync to your computer with Google Drive for desktop and point it there.

Then say:

> Edit the new video for Northbank Physio into an ad.

Claude will:

1. Transcribe it, read it line by line, and show you the retakes, false starts and "um"s it wants to cut, including any background talk before or after the take. Say yes, or tell it to keep any of them.
2. Cut it, then check every word you kept is still there. If two words ran together at a cut, it tells you the time so you can listen.
3. Plan the ad: the offer up front, up to two headings in your client's own words, the words to highlight, and B-roll for the lines that name something you can film. It looks at every B-roll option before picking.
4. Render all three ratios, check them, and look at the stills itself before telling you it's done.

On our Mac, a 40 second take came back as three finished ads in about 3 to 4 minutes. Longer videos take longer.

## Step 8. Give notes

Talk to it like an editor:

> Make it a bit faster.
>
> Turn the music down a little.
>
> Swap the shot on "school" for something calmer.
>
> Put back the bit about the free assessment.

Claude changes just that, renders again, checks it and tells you what changed.

## Step 9. Hand the ads to your client

Your ads are in `ai-ad-editor/clients/<client>/ads/<video name>/`:

- the three MP4s, named by ratio
- `review.html`, the review sheet, with the checks it ran and the credits for any B-roll it used

The footage comes from Pixabay, so you don't have to credit it in the ad. Keep the review sheet with the project in case anyone asks where a clip came from.

## Step 10. When something goes wrong

Say "run the doctor" first. It checks every install and both keys and tells you how to fix what's broken. The usual ones:

| What you see | What to do |
|---|---|
| A key is missing | Open `.env` and paste the key after its `=` sign. |
| ffmpeg or Node.js isn't found | Ask Claude to install it, then run the doctor again. |
| "This video has no sound we can hear" | The video's sound is missing or silent. Check the mic in the original. |
| "Listen at 0:12" after a cut | Play that moment. If every word is there, tell Claude, and it records your yes. If a word is clipped, tell Claude which, and it puts the cut back. |
| The music is too loud or too quiet | Tell Claude "music louder" or "music quieter". It sits about 15 dB under the voice and can move 3 dB either way; past that it would fight the voice or vanish on a phone. |

## Want us to do it for you?

If you'd rather hand your clients' videos to someone and get finished ads back, that's what we do at Empire OS. [Book a call](https://goempireos.com) and we'll show you how we run it for agencies.

---

*Built by Empire OS. The code is MIT licensed. The example fonts are under the SIL Open Font License. B-roll comes from Pixabay under the Pixabay Content License. HyperFrames renders the video, ElevenLabs hears every word and makes the music, and Claude does the editing.*
