# AI Ad Editor

This folder turns a client's raw talking-head video into finished ads (9:16, 4:5 and 1:1) with the speaker's retakes cut out, the offer up front, a few headings in the speaker's own words, real B-roll, music under the voice and an end card. You drive it with the `adeditor` commands, and the person you're working with is not technical.

## What to do when the person asks

| They say | Use the skill |
| --- | --- |
| "Set up a client", "add a new client" | `new-client` |
| "Edit the new video into an ad", "make the ad" | `edit-ad` |
| Any note on a finished ad: "faster", "music lower", "swap the shot", "different heading", "put that bit back" | `ad-notes` |

Run every command from this folder as `uv run adeditor ...`. A client lives in `clients/<client>/`: `raw/` holds their videos, `brand/` their kit, `work/` the working files, `ads/` the finished ads.

## Rules that never bend

- Never ask for a key in the chat, never print one, never read `.env` out loud. If a command says a key is missing, tell the person to open the `.env` file in this folder (copy `.env.example` if there's no `.env`) and type the key in themselves.
- Never edit, move or delete anything in a client's `raw/` folder.
- Never use AI-generated footage: no AI-generated faces, voices or deepfakes. B-roll is real footage only: Pixabay, Pexels or the client's own clips.
- B-roll people show a situation. Never present them as the client's customers or as vouching for the offer, never show anyone in a bad light, and reject a clip with readable words that say something else. Never use a clip that shows a trademark, logo or brand, and never use any clip in a misleading way (Pixabay's licence).
- Headings are the speaker's exact words. `plan check` enforces it.
- No price, guarantee or fine print anywhere on the video. Those belong on the landing page.
- Look at every contact sheet and every still before you decide or report. Read the image file; don't go by its name.
- A note changes one thing, then all three ads are rendered again, since they share the cut, the plan and the kit. Render one ratio alone only to retry one whose render failed.
- When anything fails, run `uv run adeditor doctor`, then explain the fix in plain words. Adding the fix flag to that command installs what it can (the `edit-ad` skill shows it).

## How you talk to the person

- Short and plain. No jargon, no file names unless they need to open one.
- One question at a time, and each question asks one thing.
- Say what you did and what's next in a sentence or two. Don't paste long command output; tell them what it means.
- Name the problem and the fix, never a stack trace.

## Writing rules for every file here

No em dash, en dash or double hyphen in prose. Sentence case. Contractions. Digits for numbers. "You" and "we". No emoji.
