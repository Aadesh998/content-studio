---
name: product-consistency
description: Rules and review checklist that keep the product (and the creator) identical across every generated frame and Veo clip of a UGC ad. Use whenever reviewing frames/clips in runs/, when a generated product looks wrong (wrong logo, garbled label text, wrong color, morphing pack, different face), or before approving a run for the expensive video stage.
---

# Keeping the product consistent

## How the pipeline locks the product (know this before fixing anything)
1. **Real reference photos** (`products/<slug>/refs/`, up to 4) are attached to every image request and to the planner and QA calls.
2. **Product bible** (`product.json`) is injected into every image prompt as a PRODUCT LOCK paragraph and condensed into every Veo prompt ("stays exactly as in the first frame...").
3. **Start-frame animation**: Veo always animates an approved frame, so the product's look is fixed by the frame; Veo only adds motion.
4. **Negative prompt** on every Veo call bans morphing packaging, changing/garbled text, extra products, captions, talking.
5. **Automatic QA**: every frame is scored 0-10 by Gemini against the reference photos (`frames/shot_XX.qa.json`); failing frames are regenerated up to `--tries` times with the reviewer's fix appended.
6. **Creator lock**: creator shots attach the creator portrait + turnaround sheet.

## Review checklist (do this for every frame before running videos)
View each `frames/shot_XX.*` (downscale to ~700px into the scratchpad first) next to `products/<slug>/refs/01.*`:
- [ ] Pack shape, proportions and size relative to hands are right
- [ ] Brand logo: same shape, color, position
- [ ] Big label text spelled exactly (small print may be blurry, but must not be wrong words)
- [ ] Main colors match; finish (glossy/matte) matches
- [ ] Exactly the intended number of packs; no invented flavors/variants
- [ ] No captions, stickers, watermarks, UI or invented text anywhere in the scene
- [ ] Creator shots: same face, hair, outfit as `creators/<name>/portrait.*`; hands have 5 fingers
- [ ] The frame matches what the voiceover line is saying
QA score below 8 or any box unchecked -> fix before Veo.

## Fixing drift (cheapest fix first)
| Problem | Fix |
|---|---|
| Garbled / misspelled label text | `--redo-frames N`. If it repeats, make the shot show the pack smaller or at an angle, or rewrite `image_prompt` so the pack is partly turned; trim long `label_text` lines in product.json |
| Wrong color / logo in many shots | Fix `locked_description`, `colors`, `must_never` in product.json, add a clearer front photo to refs, then redo those frames |
| Product looks different in one shot only | Edit that shot's `image_prompt` (start with "the exact same <name> from the reference images", say where the label faces) and `--redo-frames N` |
| Pack morphs / text melts during the clip | Make `video_prompt` calmer: slower camera, less pack motion, keep the pack upright; then `--redo-clips N`. Product-in-hand shots are riskier than static hero shots |
| Creator face changes | Redo the frame; if persistent, regenerate the creator with a more distinctive description (`creator add --force`) |
| Person appears to talk | Add "mouth closed, smiling, not speaking" to the shot's `video_prompt`, `--redo-clips N` |

## Rules
- Never accept a frame with wrong brand text for a CTA or hero shot - those are what viewers remember.
- Never "fix" the product by describing it differently from the real photos; the photos win.
- Keep the same run folder when fixing: finished shots are reused, only redone shots cost money.
- After assembly, spot-check `final.mp4` at the middle of each shot: `ffmpeg -ss <t> -i final.mp4 -frames:v 1 out.jpg`.
