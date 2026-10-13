# Content Studio

One tool for an AI content agency: **UGC ad videos**. Product + voiceover script → finished Reel / Short (**9:16**) or YouTube ad (**16:9**) with a consistent product, a consistent AI creator, **English + Hindi** voiceovers and burned-in captions.

Everything runs through one command: `./studio`. Python standard library + ffmpeg only; nothing to `pip install`.

## Sample output
A concept/spec ad made with this pipeline from `scripts/maggi-midnight.txt` (9:16, 22s, practice only, not affiliated with the brand):
- [English version](docs/samples/maggi-concept-ad-en.mp4)
- [Hindi version](docs/samples/maggi-concept-ad-hi.mp4)

## Getting started
**Requirements:** Linux or macOS, Python 3.10+, ffmpeg (with libass), and API keys for [Google Gemini](https://aistudio.google.com/apikey) (with Veo access), [ElevenLabs](https://elevenlabs.io) and [Sarvam](https://dashboard.sarvam.ai). Install the fonts *Liberation Sans* and *Noto Sans Devanagari* for captions.

```bash
git clone <your-repo-url> content-studio && cd content-studio
./setup.sh                 # checks ffmpeg + fonts, creates .env from .env.example
nano .env                  # paste GEMINI_API_KEY, ELEVEN_API_KEY, SARVAM_API_KEY
./studio doctor --online   # everything should say OK
```
Or open the folder in **Claude Code** and describe what you want (*"make a 9:16 reel for this product with this script"*); the skills in `.claude/skills/` run the whole workflow, including the review steps.

## Command cheat sheet
| I want to... | Command |
|---|---|
| Check my setup | `./studio doctor --online` |
| Add a product (real photos) | `./studio product add <slug> --name "Brand Product (size)" --images front.jpg side.jpg --notes "how it looks in use"` |
| See / fix a product's locked details | `./studio product show <slug>` → edit `products/<slug>/product.json` |
| Create an AI creator | `./studio creator add <name> --describe "Indian woman, 24, ..." [--voice <elevenlabs id>]` |
| Make an ad (full run) | `./studio make <slug> scripts/<script>.txt --ratio 9:16 --creator <name>` |
| Make an ad, review frames first (recommended) | `./studio make <slug> scripts/<script>.txt --ratio 9:16 --creator <name> --until images` |
| Continue / finish a run | `./studio make --run runs/<run-folder>` |
| Redo a bad frame or clip | `./studio make --run runs/<run> --redo-frames 3` / `--redo-clips 5` |
| English only / English + Hindi | `--languages en` / `--languages en,hi` (default from `UGC_LANGUAGES`) |
| Add music, mute Veo sound, no captions | `--music track.mp3`, `--ambient 0`, `--no-captions` |
| See a run's progress | `./studio status runs/<run>` |
| One 8s cinematic product clip | `./studio hero <slug> --ratio 16:9` |
| Test a voice | `./studio voice "Hello" -p` |

Final videos land in `runs/<product>-<timestamp>/final.mp4` (English) and `final_hi.mp4` (Hindi).

## What's committed to git
Code, prompts, skills, docs (including the sample videos in `docs/samples/`) and scripts are committed. `.env` (keys), `products/`, `creators/` and `runs/` (client photos, faces, videos) stay local, and `.gitkeep` keeps the empty folders. Each teammate creates their own `.env` from `.env.example`.

## Make a UGC ad
```bash
./studio product add maggi-masala --name "MAGGI 2-Minute Noodles Masala (70g)" \
    --images front.jpg side.jpg --notes "cooked: wavy golden noodles with peas and tomato"
./studio creator add riya --describe "Indian woman, 21, college student, messy bun, lavender hoodie"
./studio make maggi-masala scripts/maggi-midnight.txt --creator riya --until images   # asks 9:16 or 16:9
#   review runs/<run>/frames, redo bad ones with --redo-frames N
./studio make --run runs/<run>                    # Veo clips + final.mp4 (English) + final_hi.mp4 (Hindi)
```

| Stage | Tool | Output in `runs/<run>/` |
|---|---|---|
| plan | Gemini `gemini-3.1-pro-preview` + master prompt | `plan.json` (editable) |
| voice | ElevenLabs → Sarvam fallback, per language | `voice/`, `voice_hi/` |
| images | Gemini image + product-consistency QA (retries) | `frames/shot_XX.*`, `*.qa.json` |
| videos | Veo 3.1 image-to-video | `clips/` |
| assemble | ffmpeg: voice, ambient, captions, music | `final.mp4`, `final_hi.mp4` |

Options: `--ratio 9:16|16:9`, `--languages en,hi`, `--creator NAME|none`, `--voice`, `--until STAGE`, `--only 2,3`, `--redo-frames/--redo-clips/--redo-voice N`, `--music file.mp3`, `--ambient 0..1`, `--no-captions`.
Also: `./studio status runs/<run>`, `./studio hero <product> --ratio 16:9` (one cinematic 8s product clip).

**How the product stays consistent:** real product photos go with every request, a locked product bible is part of every prompt, Veo only animates QA-approved frames, every frame is scored against the photos (below 8/10 it's regenerated), and a negative prompt blocks morphing packs and garbled text.

## Voice only
```bash
./studio voice "Hi, I am Riya!" -p                          # ElevenLabs (Monika), Sarvam if it fails
./studio voice "नमस्ते दोस्तों" -e sarvam -v neha -l hi-IN
./studio voice --voices
```

## Project layout
```
studio, setup.sh          entry point and one-time setup
.env / .env.example       API keys, models, defaults (UGC_LANGUAGES=en,hi)
adgen/                    the code: cli, config, gemini, tts, ugc (ads)
prompts/ugc/              master_prompt (from docs/Protein bar prompting.pdf), product bible, QA, translation, hero
products/<slug>/          real product photos (refs/) + product.json
creators/<name>/          creator portrait + turnaround sheet + creator.json
scripts/                  voiceover scripts
runs/<product>-<time>/    one folder per ad (git-ignored)
.claude/skills/           ugc-ad, product-setup, product-consistency
docs/                     reference material
```

## Costs and safety
- Plans, voices and images are cheap; Veo clips are the main cost. Always review frames before the videos stage.
- Demo ads of real brands (like the Maggi sample) are for practice. Don't post them publicly as portfolio without the brand's permission; use your own products, fictional ones, or client-approved work, labelled as concept ads.
- `.env` holds your keys and is git-ignored. Never commit it.
