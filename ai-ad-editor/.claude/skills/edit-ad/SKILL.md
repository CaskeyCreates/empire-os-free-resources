---
name: edit-ad
description: Edit a client's new raw video into finished ads in 9:16, 4:5 and 1:1. Transcribes, cuts the retakes and fillers, plans the headings and B-roll, renders, checks and reports. Use when the person says "edit the new video into an ad", "make the ad", "edit this video" or names a client with a new video.
---

# Edit a video into an ad

Run from this folder. Keep each message to the person short and plain, with one question at a time. Never ask for a key in the chat, never edit anything in `raw/`.

## 0. Before you start

The first edit of the day, check the setup. If something fails, let it fix what it can, then explain anything left in plain words.

```
uv run adeditor doctor
uv run adeditor doctor --fix
```

Run the second line only if the first one failed.

The client's kit must be approved. If a command says it isn't, use the `new-client` skill's last step.

Find the new videos for the client:

```
uv run python -c "from adeditor import paths; print(*paths.new_videos('<client>'), sep=chr(10))"
```

If there are several, ask which one. Below, `<video>` is that file's path as printed, in quotes.

## 1. Transcribe and propose the cuts

```
uv run adeditor prepare <client> "<video>"
```

It prints what it would cut. Read the whole transcript yourself, with each word's number and start time:

```
uv run python -c "import json,sys; [print(i, round(w['start'],2), w['word']) for i,w in enumerate(json.load(open(sys.argv[1]))['words'])]" "clients/<client>/work/<stem>/transcript.json"
```

Add any retake the rules missed to `clients/<client>/work/<stem>/strikes.json`: a strike is `{"from": <first word number>, "to": <last word number>, "why": "Claude: <short reason>"}`, using the numbers that command prints, and strikes can't overlap. Keep the last full take of each line.

Tell the person what will be cut, in a few plain lines. Ask: "Cut these?" Wait for a yes.

```
uv run adeditor approve-cut <client> "<video>"
```

## 2. Cut

```
uv run adeditor cut <client> "<video>"
```

Report the word check in one line ("Every kept word is still there" or what it named). If it printed a list of spots where words run together, give each one with its time and the words either side, and ask the person to listen at those spots in the finished ad.

If it stopped on a word that may be clipped, tell the person the word, the time and the line, and ask them to play the file the stop names at that time. You can't hear it; only they can say.

- If the stop names a cut word beside it and they hear a word clipped, put that cut word back: remove it from `strikes.json` (its strike, or its number in `fillers`), then approve and cut again.
- If they listen and every word is there, record their yes. Run this only on their word, never for them:

```
uv run adeditor accept-words <client> "<video>"
```

The yes holds for that cut only: a new cut is checked again. Any other stop, explain plainly.

## 3. Speed

The default is 1x. To judge the pace, divide the number of kept words in `clients/<client>/work/<stem>/cut.json` by its `duration` in minutes:

```
uv run python -c "import json,sys; c=json.load(open(sys.argv[1])); print(round(len(c['words'])/(c['duration']/60)), 'words a minute')" "clients/<client>/work/<stem>/cut.json"
```

Only at or under about 150 words a minute, ask: "Their delivery is slow and calm. Want it a bit quicker?" 1.25x suits that; 1.5x rushes it, so don't suggest it. If they say yes:

```
uv run adeditor speed <client> "<video>" 1.25
```

## 4. The plan

Write `clients/<client>/work/<stem>/plan.json`. See `docs/plan-example.json` for the shape. Only these keys exist: `lockup`, `headings`, `key_words`, `broll`, `end_card`, `bed_offset_db`.

- `"lockup": "kit"` uses the client's approved lockup.
- At most 2 headings. Each is a short run of the speaker's exact kept words, with `from` and `to` phrases saying where it shows. `to` is where it ends, at the start of that phrase, unless `to_end` is true, and then it ends at the end of that phrase plus `hold` seconds (default 0.7). If a phrase is said more than once, add `after` with an earlier phrase so `from` picks the occurrence after it. Without `to_end`, `to` is looked for only after the `from` match, so it's the first time that phrase is said after `from`, and it needs no `after` of its own. With `to_end`, `to` is looked for from the start of the `from` match, so a heading that shows while one phrase is said has the same phrase in `from` and `to`. Every phrase must match the kept words only. A word between asterisks, like `*worse.*`, is emphasised. Never invent or tidy the words; `plan check` rejects anything the speaker didn't say.
- No price, guarantee or fine print in any heading.
- `key_words` are the words that carry each line. A word that repeats can be limited with `only_in`.
- B-roll only on lines that name something you can film. Each B-roll has `clip`, `from`, `to` (with `to_end`, `hold` and `after` working as for headings) and a short `why`. `in` is the second to start inside the clip. `y` is its height on screen as a percentage. B-roll can't start in the first 2.5 seconds, share a moment with a heading, overlap other B-roll or run into the end card.

### Choosing B-roll

There are three sources, and every clip id says which one it came from:

- Pixabay (`pixabay-<number>`): the default when `PIXABAY_API_KEY` is set.
- Pexels (`pexels-<number>`): when only `PEXELS_API_KEY` is set, or when you ask for it with the source option shown below. Pexels has paused new keys, so most people won't have one.
- The client's own clips (`own-<name>`): MP4, MOV or M4V files the person put in `clients/<client>/broll/own/`. Use the own option shown below. They're already on the computer, so there's nothing to download. If no stock key is set, this is the only source, and the search command says so.

For each B-roll line, search with plain words for what you can see:

```
uv run adeditor broll <client> "<video>" "<what to find>"
uv run adeditor broll <client> "<video>" "<what to find>" --source pexels
uv run adeditor broll <client> "<video>" --own
```

It makes a contact sheet from previews and downloads no full clips. Open the contact sheet image and look at it. Pick a clip whose content matches the line. Reject a clip if:

- it has readable words that say something else,
- it shows a trademark, logo or brand of any kind (Pixabay's licence doesn't allow those in commercial use),
- it would be used in a misleading way, so the clip must really show what the line says,
- it shows a person as a customer, patient or client of the offer, or as vouching for it,
- it shows anyone or anything in a bad light,
- it looks made by AI.

If nothing fits, search again in plainer words, or leave that line without B-roll. Then download only the one you picked (a stock clip; an own clip needs no download):

```
uv run adeditor broll <client> "<video>" --get <id>
```

Use the id exactly as the contact sheet shows it, like `pixabay-1234567`, in the plan's `clip`.

## 5. Check the plan

```
uv run adeditor plan check <client> "<video>"
```

Fix everything it lists in `plan.json` and run it again until it's clean. Never work around it by moving a phrase to a time.

## 6. Render and check

```
uv run adeditor render <client> "<video>"
uv run adeditor check <client> "<video>"
```

Then open the stills in `clients/<client>/ads/<stem>/stills/` and look at them: the lockup reads, nothing covers the face, headings and B-roll sit right, the end card is clean, and no price or fine print shows. Fix a problem with a plan change, then render all three again and check again: the three ads share the cut and the plan, so the check fails any ad made before a change. Render one ratio alone only to retry one whose render stopped with an error (the `ad-notes` skill shows how). If the check says the music is too loud or can't be heard, change `bed_offset_db` in `plan.json` (between -6 and 6) and render again.

## 7. Report

Tell the person, in a few short lines:

- the finished ads are in `clients/<client>/ads/<stem>/`, one per ratio, and the review sheet is `review.html` there;
- anything the check flagged, and how it was fixed;
- the spots where words run together, each with its time and the words either side, and ask them to listen there;
- the credits for the B-roll, read from `clients/<client>/broll/credits.json`: who shot each stock clip with a link to its source (Pixabay at https://pixabay.com, Pexels at https://www.pexels.com), and "The client's own footage" for the client's own clips.

Ask: "Anything you'd change?" Notes go to the `ad-notes` skill.

If any command fails, run `uv run adeditor doctor` and explain the fix plainly.
