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

Run the second line if the first one failed or warned. On a new install the first run shows `FAIL face model`; the fix downloads it. A warning that starts "No sound effects:" followed by a reason means the five sounds aren't made (yet, or because of a free plan or a missing key); a warning that says "The sound effects are switched off" means `ADEDITOR_NO_SOUNDS=1` is set. The fix makes them once per install, from five very short clips (3.3 seconds in all), so it spends very few ElevenLabs credits and takes a short while. On a free ElevenLabs plan it makes none, and that's fine; the ads just have no effects. To switch them off on purpose, add `ADEDITOR_NO_SOUNDS=1` to `.env` (the doctor's sound line then says to remove `ADEDITOR_NO_SOUNDS=1` from `.env` to have them back; the render says "No sound effects: ADEDITOR_NO_SOUNDS=1 is set.").

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

The rules only find retakes that open alike and fillers, so read every line yourself. Strike any earlier try at a line the rules missed, and any talk that isn't the take: someone else speaking, a TV or radio in the background, or setup chatter before the first line and after the last. Add each to `clients/<client>/work/<stem>/strikes.json`, one JSON object, never a bare list. If the file is missing, make it with this shape (the example strikes an invented earlier try at a line, words 12 to 19):

```
{"strikes": [{"from": 12, "to": 19, "why": "Claude: earlier try at the line about the free quote"}], "fillers": [], "approved": false}
```

A strike is `{"from": <first word number>, "to": <last word number>, "why": "Claude: <short reason>"}`, using the numbers that command prints, and strikes can't overlap. Keep the last full take of each line. Use your file edit tool to change the file, not a shell one-liner (`sed -i` differs between Mac and Linux, and a failed edit renders the old plan).

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

Write `clients/<client>/work/<stem>/plan.json`. See `docs/plan-example.json` for the shape. Change it with your file edit tool, not a shell one-liner, and run `plan check` after every change. Only these keys exist: `lockup`, `headings`, `key_words`, `broll`, `end_card`, `bed_offset_db`, `graphics`, `sounds`, `flash`, `sfx_offset_db`.

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

Also skip any clip with words, numbers, logos or brand names in frame: a crop for another ratio can cut a word in half. If nothing fits, it may take two or three searches with different words; try plainer words, or leave that line without B-roll. Aim for B-roll about every 6 to 10 seconds, on lines that name something concrete. With no stock key set, put a graphic or the client's own clips (`clients/<client>/broll/own/`) where B-roll would go, and the take itself carries the rest. Then download only the one you picked (a stock clip; an own clip needs no download):

```
uv run adeditor broll <client> "<video>" --get <id>
```

Use the id exactly as the contact sheet shows it, like `pixabay-1234567`, in the plan's `clip`.

### Graphics

A graphic is a short animated card drawn from what the speaker says. It fills the frame, captions included, then leaves. Use one where a line is a list, a sequence, a comparison, a number or a line worth stopping on, and no footage can show it. Each has `kind`, `from` and `to` (they work as they do for headings, with `to_end`, `hold` and `after`), and optional `title`, `ground` and `sfx`. Every example below is for an invented gutter cleaner.

| Kind | Fits | Example |
| --- | --- | --- |
| `cycle` (3 to 6 items) | something that goes round and round | `"items": ["it clogs", "it overflows", "it floods the wall"]` |
| `steps` (2 to 5 items) | what happens first, then next | `"items": ["we clear it", "we flush it", "we check the drain"]` |
| `versus` (1 to 4 a side) | this, but that, with a `turn` phrase between the sides | `"left": ["a leaky gutter"], "turn": "but", "right": ["a dry house"]`, and optional `left_header` and `right_header` |
| `stat` (one number) | a number the speaker says | `"value": "48", "label": "hours"` |
| `checklist` (2 to 6 items) | things they get | `"items": ["a full clean", "a photo report"]` |
| `quote` (one line) | the line worth stopping on | `"line": "we leave it spotless", "accent": "spotless"` |

The caps:

- Each lasts 2.5 to 8 seconds, so widen or narrow its `from` and `to`.
- At most 4 in an ad in theory, but at most 1 starting in any 12 seconds, so a 20 to 30 second ad usually holds one or two, and a very short take may hold none and no headings. Tell the person that plainly rather than forcing more in.
- A `stat` needs its number said after the opening offer's first 2.5 seconds; a number said inside the opening can't carry one.
- Nothing in the first 0.4 seconds of a graphic, the opening offer's first 2.5 seconds or on the end card.
- Never over B-roll, a heading or another graphic.
- Captions hide under every graphic: its words are the speaker's own.

The truth rule: every word on a graphic was said in the kept take, in that order, inside its window. The same goes for its title and the versus headers. A `stat` value is a number the speaker says there, as digits or words, and its unit (a `%`, a `$`) has to be said too. Drawn step numbers, the count-up and the "vs" mark aren't claims. If `plan check` says a phrase isn't said, use the runs of kept words it offers. Never tidy the words.

Set `ground` to `"deep"` (dark) or `"paper"` (light) so the graphic contrasts with the take: a dark take gets `paper`, a bright one gets `deep`. Item text shrinks to fit down to 0.75 of its size, then `plan check` refuses it, so shorten that item. If it says a text would end on one word alone, pick a shorter or longer run of words. Keep graphics to the lines that earn one.

Before you render, look at each graphic:

```
uv run adeditor preview <client> "<video>"
```

It saves one still per graphic at its fullest moment, to `clients/<client>/work/<stem>/preview/<ratio>-graphic-<n>.png`, and sends nothing anywhere. Open every one and look: the words read, nothing is cut off, the contrast is right. Fix the plan and preview again. The final stills show each graphic too, labelled "Graphic <n>".
To look at one ratio only:

```
uv run adeditor preview <client> "<video>" --ratio 9x16
```

### Sounds and the flash

Five small sound effects (cut, push, hit, whip, tick) sit under the voice on graphics, B-roll and heading changes, and a quick white shutter flash marks each visible punch-in and back. Both are on by default: `"sounds": true`, `"flash": true`, and `"sfx_offset_db": 0` (between -12 and 6) moves every effect together. The sounds are made once by the fix in step 0. If the install has none (a free ElevenLabs plan, no key, or `ADEDITOR_NO_SOUNDS=1` in `.env` or the environment), every render says "No sound effects: <reason>." and the ad renders without them. Say that to the person in one plain line; it isn't an error. To have them, remove `ADEDITOR_NO_SOUNDS=1` from `.env`.

## 5. Check the plan

```
uv run adeditor plan check <client> "<video>"
```

Fix everything it lists in `plan.json` and run it again until it's clean. It also warns when more than 20 seconds pass with neither B-roll nor a graphic; add one where the speaker says something you can show. Never work around it by moving a phrase to a time.

## 6. Render and check

A render takes minutes (about 3 to 4 for a 40 second take). Run it with a 10 minute timeout, or in the background and wait for it to finish; never a short timeout.

```
uv run adeditor render <client> "<video>"
uv run adeditor check <client> "<video>"
```

Each ad prints one line, like `take (9x16).mp4: 0:26, -14.3 LUFS, peak -2.0 dBTP, the music 15.0 dB under the voice, 24 sound effects`.

If a caption shows a word the transcriber misheard (a name, a product, a trade word), don't leave it on screen: add the word to `"keyterms"` in the client's `brand/kit.json`, transcribe again (`prepare`), and redo the strikes and the plan.

The check may say, for the very start or end of the ad, "At the very start: listen that '<word>' begins cleanly (0:00)." Ask the person to play the first moment of the ad.

Then open the stills in `clients/<client>/ads/<stem>/stills/` and look at them (they include each graphic, labelled "Graphic <n>"; the preview stills show them too): the lockup reads, nothing covers the face, headings, graphics and B-roll sit right, the end card is clean, and no price or fine print shows. Fix a problem with a plan change, then render all three again and check again: the three ads share the cut and the plan, so the check fails any ad made before a change. Render one ratio alone only to retry one whose render stopped with an error (the `ad-notes` skill shows how). If the check says the music is too loud or can't be heard, change `bed_offset_db` in `plan.json` (between -3 and 3; 0 is the music 15 dB under the voice, negative is quieter, positive louder) and render again.

## 7. Report

Tell the person, in a few short lines:

- the finished ads are in `clients/<client>/ads/<stem>/`, one per ratio, and the review sheet is `review.html` there;
- anything the check flagged, and how it was fixed;
- the spots where words run together, each with its time and the words either side, and ask them to listen there;
- the credits for the B-roll, read from `clients/<client>/broll/credits.json`: who shot each stock clip with a link to its source (Pixabay at https://pixabay.com, Pexels at https://www.pexels.com), and "The client's own footage" for the client's own clips.

Ask: "Anything you'd change?" Notes go to the `ad-notes` skill.

If any command fails, run `uv run adeditor doctor` and explain the fix plainly.
