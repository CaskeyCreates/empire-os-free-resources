---
name: new-client
description: Set up a new client for the AI Ad Editor. Reads their brand from their website, sets the offer lockup every ad carries, adds key terms and makes and approves a music bed. Use when the person says "set up a client", "add a new client", "new client" or gives a client name and website to start with.
---

# Set up a client

Ask one question at a time. Never ask for a key in the chat.

## 1. The client

Ask the client's name, then their website address. Make the folder name from the name: lower case, digits and hyphens only, like `acme-physio`.

```
uv run adeditor client new <client> <site address> --name "<Client Name>"
```

If it says "We couldn't read their site", the client's folder is still made, with plain colours (black and white with a blue accent) and fonts. Say that plainly, then ask for another page of their site, like their About page, and run the same command again, or go to the next step and set their colours by hand.

If it says a key is missing, tell the person to type it into the `.env` file in this folder themselves, then run it again.

## 2. The look

Read `clients/<client>/brand/kit.json`. Tell the person plainly what we took: the colours and the two fonts. If a font note was printed, say it in one sentence, like "We couldn't get their exact font, so we're using Libre Baskerville, which looks close."

If the person has their client's own licensed font files (.woff2, .woff, .ttf or .otf), they go in `clients/<client>/brand/fonts/`. Then, in `kit.json`, set that role's `family` to the font's name and its `files` to those files, like `"regular": "fonts/their-font-regular.ttf"`, with `null` for a face that has no file. We never download a font from their site, so only use files the person gives you.

If `client new` printed a note that the colours are a guess from the stylesheets, say so: they need checking against the logo and the buttons on the client's site. Give them the full path to `clients/<client>/brand/swatch.html` and tell them to open it in their browser. A light logo shows as a dark silhouette on the sheet's top panel, and the end card then sits on the deep ground. Ask: "Does that look like their brand?" If no logo was found, tell them the end card will carry the client's name in its place until they have one, and ask them to drop `logo.png` or `logo.svg` into `clients/<client>/brand/`, then set `"logo"` in `kit.json` to that file name.

If they say the colours look wrong and don't know the codes, take the main colour from the client's logo and the accent from the main button on their site, then check the swatch again. Otherwise ask for the brand's main colour as a hex code, like #1A1A1A, then ask for its accent colour the same way. Never edit the colours in `kit.json` by hand; this works out the rest and checks the captions will read:

```
uv run adeditor client colours <client> --primary "<main colour>" --accent "<accent colour>"
```

If it says a pair doesn't read, tell the person which colour to change, in plain words, and ask for another. Then ask them to look at the swatch again.

## 3. The lockup

This is the opening every ad carries. Ask for each part, one at a time:

1. Who it's for, in how they sell or a hard qualifier ("For desk workers with back pain").
2. The offer line, and an optional second line.
3. An optional detail line.
4. The button word on the end card. Default "Learn more".

Never put a price, a guarantee or fine print in the lockup. If the person gives one, say those belong on the landing page and ask again for a line without it.

```
uv run adeditor client lockup <client> --callout "<who it's for>" --offer "<offer line>" --offer2 "<second line>" --detail "<detail>" --button "Learn more"
```

Leave out the second line and the detail when there isn't one.

## 4. Key terms

Ask: "Any names, products or places the transcriber might mishear?" Put them in the `"keyterms"` list in `kit.json`, a few short words or phrases. There's no command for this; use your file edit tool on the file. If there are none, leave it empty.

## 5. The music

Ask for a mood in a few words ("calm and warm", "steady and hopeful"). Write a prompt from it: instrumental, about 72 BPM, calm and low under a voice, no vocals, no drops. Show the prompt in a line and make the music.

```
uv run adeditor client music <client> --prompt "<the prompt>" --bpm 72
```

If the person has a track they're licensed to use, copy it in instead and give its tempo when they know it:

```
uv run adeditor client music <client> --file "<path to the track>" --bpm 72
```

Play nothing yourself. Tell them it's at `clients/<client>/brand/music/bed.mp3` and ask them to listen. If they don't like it, make another with a changed prompt.

## 5b. The sound effects

Ads carry five small sound effects (cut, push, hit, whip, tick), made once per install, not per client, with ElevenLabs Sound Effects, by this command once the key is in `.env`:

```
uv run adeditor doctor --fix
```

They need a paid ElevenLabs plan (the free plan can't make them). If the person is on the free plan, say so in one sentence: the ads still work, they just have no effects. To switch them off on purpose, add `ADEDITOR_NO_SOUNDS=1` to `.env`; remove it to have them.

## 6. Approve

Ask: "Happy with the look, the lockup and the music?" Only on a clear yes:

```
uv run adeditor client approve <client>
```

Any change to the lockup or the music clears the approval, so run approve again after one. Then tell them the client is ready and to put the first video in `clients/<client>/raw/`.

If any command fails, run `uv run adeditor doctor` and explain the fix plainly.
