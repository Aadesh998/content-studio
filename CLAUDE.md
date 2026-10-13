# Content studio (adgen)

One command, `./studio`, for UGC ad videos (see README.md): script → Gemini shot plan → frames (product-locked, QA'd) → Veo clips → ElevenLabs/Sarvam voice (English + Hindi) → ffmpeg final videos. Code: `adgen/ugc.py`.

Shared: `adgen/config.py` (paths, .env), `adgen/gemini.py` (text/image/Veo), `adgen/tts.py` (ElevenLabs → Sarvam), `adgen/cli.py`. Prompts live in `prompts/ugc/`. Standard library + ffmpeg only; keep it that way.

- Skills: `ugc-ad`, `product-setup`, `product-consistency`.
- Always ask the video aspect ratio (9:16 or 16:9) unless the user stated it.
- Voice: ElevenLabs first, Sarvam fallback, never a local model. Ads get English + Hindi by default (`UGC_LANGUAGES`).
- Review before spending: `--until images` before Veo.
- Product truth = real photos in `products/<slug>/refs/` + `product.json`. Never AI images as product references.
- Never let generated content claim clients, results or speeds the business can't back up; label demo ads "concept/spec ad"; don't post ads of real brands you don't work with.
- Long runs exceed the Bash timeout: run them in the background.
