---
name: ad-notes
description: Apply the person's plain notes to an ad that's already been made. Handles faster or slower, music louder or lower, swapping a shot, a different heading, and putting a cut bit back, then renders the three ads again so they stay in step. Use when the person gives feedback on a finished ad, like "faster", "the music is too loud", "swap the shot on that line", "different heading" or "put back the bit about X".
---

# Apply a note

A note changes one thing. Work out which ad it's about (ask if it's unclear), make that one change, render, check, look, report. Keep messages short and plain.

Paths: `clients/<client>/work/<stem>/` holds `plan.json`, `strikes.json` and `transcript.json`. `<video>` is the raw file's path, in quotes.

## What each note becomes

| The note | The change |
| --- | --- |
| "Faster" or "slower" | `speed`, then re-check the plan |
| "Music lower" or "louder" | `bed_offset_db` in `plan.json` |
| "Swap the shot on X" | a new B-roll search for that line |
| "Different heading" | `headings` in `plan.json` |
| "Put back the bit about X" | remove that strike from `strikes.json` |

### Faster or slower

Default is 1x. 1.25x suits a calm delivery. 1.5x rushes it, so say so if they ask for more. Speed always starts again from the 1x cut, so any factor is safe, and 1 puts the original pace back.

```
uv run adeditor speed <client> "<video>" 1.25
```

Times move, so run `plan check` next.

### Music lower or louder

Set `"bed_offset_db"` in `plan.json`. A positive number is louder, a negative number lower, and it can only go between -6 and 6. Try 2 or 3 at a time. The check wants the music 12 to 18 dB under the voice and will say if the note went too far; if it can't hear the music at all, move the number back toward 0. If the music is more than 18 dB under the voice (too quiet), use a positive number, up to 6.

### Swap the shot

Search again for that line only and look at the contact sheet image:

```
uv run adeditor broll <client> "<video>" "<what to find>"
```

Pick with the same rules as the first time: content that matches the line, no readable words saying something else, no person as a customer or vouching for the offer, nothing in a bad light, nothing AI-made. Download the one you pick:

```
uv run adeditor broll <client> "<video>" --get <id>
```

Then change that B-roll's `clip` in `plan.json`.

### Different heading

Edit that heading in `plan.json`. It must be the speaker's exact kept words, with no price, guarantee or fine print.

### Put back the bit about X

Print the word numbers:

```
uv run python -c "import json,sys; [print(i, round(w['start'],2), w['word']) for i,w in enumerate(json.load(open(sys.argv[1]))['words'])]" "clients/<client>/work/<stem>/transcript.json"
```

Find the strike in `strikes.json` that covers the bit and remove it, or remove those word numbers from `fillers`. Then approve and cut again. The cut keeps the speed the person chose.

```
uv run adeditor approve-cut <client> "<video>"
uv run adeditor cut <client> "<video>"
```

If the cut lists spots where words run together, give each with its time and the words either side, and ask them to listen there. If it stops on a word that may be clipped, do what step 2 of the `edit-ad` skill says: the person listens there, and only on their word that every word is there do you run `accept-words`. Times move after a new cut, so run `plan check` next.

## Then, every time

```
uv run adeditor plan check <client> "<video>"
```

Fix what it lists. Then render all three ads again, even when the person named one ratio: every note changes the cut, the plan, the kit or the speed, which all three share, and the check fails any ad made before the change.

```
uv run adeditor render <client> "<video>"
```

Render one ratio alone only to retry one whose render stopped with an error, naming it:

```
uv run adeditor render <client> "<video>" --ratio 4x5
```

Then:

```
uv run adeditor check <client> "<video>"
```

Look at the stills in `clients/<client>/ads/<stem>/stills/` before you say it's done. Report in a line or two what changed, anything the check flagged, and any spots where words run together to listen at. Ask: "Anything else?"

If anything fails, run `uv run adeditor doctor` and explain the fix plainly.
