---
name: product-setup
description: Register a new product (reference photos + locked product bible) or a new on-camera creator (face portrait + turnaround sheet + voice) for the UGC ad pipeline. Use when the user wants an ad for a product that is not in products/, gives new product photos, wants to change a product's details, or wants a new/different person in the ads.
---

# Product and creator setup

Consistency starts here: every frame and clip is generated from the product's real photos plus its `product.json` "bible". A sloppy bible = drifting packaging in every shot.

## Add a product
1. Get **real photos** of the product from the user (best: front with label fully readable, plus a 3/4 angle; 1-4 photos, plain background, no hands covering the logo). **Nothing may cover the label** - a price sticker over one word makes the image model garble that word in every frame. Put the cleanest unobstructed photo first (`refs/01`); it carries the most weight. If the user has none and the product is a public retail item, you may fetch a real front-of-pack photo (e.g. Open Food Facts for packaged food), save it in its own scratchpad folder, view it, and tell the user its source. Never use an AI-generated image as the product reference.
2. Run:
   ```bash
   ./studio product add <slug> --name "Brand Product Variant (size)" --images front.jpg angle.jpg \
     --notes "what photos don't show: inside texture, how it is used/cooked, stickers to ignore"
   ```
   `--notes` matters: describe the product *in use* (cooked noodles, bar texture when bitten, pour of a drink) because product shots show it.
3. **Review `products/<slug>/product.json` against the photo yourself** (view the photo with Read):
   - `label_text`: only big, prominent front text, spelled exactly. Remove nutrition tables, barcodes, tiny legal text, price stickers.
   - `colors`, logo placement, `shape_and_size` are right.
   - `must_never`: add product-specific rules (e.g. "noodles are always wavy"). Only describe the real product - never mention things to leave out such as stickers or prices: image models tend to print those words onto the pack (this happened: "WITHOUT STICKER" appeared on a Maggi pack). Fix bad reference photos instead.
   Edit the file directly - it is the single source of truth. `product rebuild <slug>` regenerates it (after adding photos with `--images`).
4. Optional defaults: `"creator": "<name>"`, `"voice": "<voice>"` in product.json.

## Add a creator (same face in every ad)
```bash
./studio creator add <name> --describe "nationality, age, skin tone, hair, distinctive features, outfit" [--voice <ElevenLabs name/id>]
./studio creator add <name> --image real_person.jpg      # from a real photo (only with that person's consent)
```
This makes `creators/<name>/portrait.*` and a 4-view `sheet.*`. View both: same face in all views, natural skin, correct hands. If not, `--force` to regenerate. Put a distinctive, fixed outfit in the description - it is part of identity consistency.

## Voices
ElevenLabs is primary, Sarvam the fallback (`./studio voice --voices` lists them). Test a voice with `./studio voice "line" -v <voice> -o /tmp/x.wav -p`. Do not use any local TTS model.
