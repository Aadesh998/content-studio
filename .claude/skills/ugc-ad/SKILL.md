---
name: ugc-ad
description: Generate a complete UGC ad video (Instagram Reel / Shorts / YouTube) from a voiceover script using this project's pipeline - Gemini plans shots with the master prompt, Gemini image makes frames, Veo animates them, ElevenLabs (Sarvam fallback) voices it, ffmpeg assembles with captions. Use when the user gives an ad script, asks to "make/generate an ad/video/reel" for a product, or asks to resume, fix or re-render a run in runs/.
---

# UGC ad from a script

Everything runs through `./studio` in the project root. Each stage writes to `runs/<product>-<timestamp>/` and is skipped when its output exists, so you can always stop, inspect and resume with `--run`.

Stages: `plan` -> `voice` -> `images` -> `videos` -> `assemble`.

## 1. Collect inputs (ask only for what is missing)
- **Product**: must exist in `products/<slug>/product.json` (`./studio product list`). If not, follow the `product-setup` skill first. Never generate an ad for a product without real reference photos.
- **Script**: save it verbatim to `scripts/<slug>-<short-name>.txt`. Do not rewrite the user's script unless asked. If the user gives only an idea, write a 20-40 second script (55-100 words) with hook -> problem -> reveal -> proof -> CTA, conversational first-person, and show it to them before spending on generation.
- **Aspect ratio**: ALWAYS ask with AskUserQuestion unless the user already said it: `9:16` (Instagram Reels, Shorts, TikTok) or `16:9` (YouTube, web). "Reel/short/story" means 9:16.
- **Creator**: `./studio creator list`. Use the product's default or ask; `--creator none` gives a product-only ad. New creator -> `product-setup` skill.
- **Languages**: default `UGC_LANGUAGES=en,hi` gives `final.mp4` (English) and `final_hi.mp4` (Hindi) from the same visuals. Override with `--languages en` or `--languages en,hi`. After the voice stage, read `plan.json` → `translations.hi` and check it sounds natural and uses the speaker's grammatical gender (female: "रही हूँ", male: "रहा हूँ"); fix lines there, delete `voice_hi/shot_XX.wav`, rerun.
- **Voice** (optional): default is TTS_VOICE (ElevenLabs Monika, Indian female). Override with `--voice <ElevenLabs name/id or Sarvam name>`. Only ElevenLabs and Sarvam are used - never a local model.

## 2. Plan and review (cheap)
```bash
./studio make <slug> scripts/<file>.txt --ratio 9:16 --creator <name> --until plan
```
Read `plan.json`. Check: voiceover lines cover the script word for word; first shot is a strong hook; at least two cinematic product shots; CTA shot shows the pack clearly; no shot asks for on-screen text. Edit `plan.json` directly if needed (keep ids sequential).

## 3. Voice + frames, then review every frame (before Veo, which is the expensive part)
```bash
./studio make --run runs/<run> --until images
```
Each frame is auto-checked by Gemini against the product photos (`frames/shot_XX.qa.json`, pass = score >= 8). Then **look at every frame yourself** with the Read tool (downscale big ones with ffmpeg into the scratchpad first) and apply the `product-consistency` checklist. For a bad frame: improve that shot's `image_prompt` in plan.json if the idea is wrong, then
```bash
./studio make --run runs/<run> --redo-frames 2,5 --until images
```

## 4. Video clips + final assembly
```bash
./studio make --run runs/<run>
```
Veo takes 1-3 minutes per clip (3 run in parallel). This can exceed the 10-minute Bash timeout - use `run_in_background: true` and wait for the notification. A failed shot does not stop the others; rerun the same command to retry only what is missing.

Then extract 3-4 frames from `final.mp4` and from suspicious clips to check that the product did not morph during motion. Bad clip -> adjust `video_prompt` and `--redo-clips N`.

## 5. Finishing options (re-assemble only, no regeneration)
- `--music track.mp3` background music, `--ambient 0` mute Veo's ambient sound, `--no-captions`
- Changed a voice line? Edit plan.json then `--redo-voice N`.
- Re-assemble: delete `final.mp4` is not needed; rerunning `make --run` always rebuilds the final video.

## 6. Report
Give the user the paths to `final.mp4` and `final_hi.mp4`, its length and ratio, the shot list, QA scores (`./studio status runs/<run>`), and any frame that never passed QA.

## Other modes
- One cinematic 8s product hero clip (the PDF's universal prompt, no script): `./studio hero <slug> --ratio 16:9 [--idea "exploded view that reforms"]`

## Cost awareness
Plan/voice/images are cheap; each Veo clip is the main cost (8 shots ~ 8 clips). Always review frames before running the videos stage, and use `--only 3,4` to test a few clips first when unsure.
