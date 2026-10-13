"""UGC ad video pipeline: script -> shot plan -> voiceover -> frames -> Veo clips -> final video.

Gemini plans the shots and makes the images and video; ElevenLabs (Sarvam fallback) makes the voice;
ffmpeg puts it together with captions. Standard library + ffmpeg only.

    ./studio product add protein-bar --name "Whey Crunch Bar" --images front.jpg back.jpg
    ./studio creator add aanya --describe "Indian woman, 24, gym-fit, ponytail, warm smile"
    ./studio make protein-bar script.txt --creator aanya          # asks 9:16 or 16:9
    ./studio make protein-bar script.txt --ratio 9:16 --until images   # stop to review frames
    ./studio make --run runs/protein-bar-20261013-1810            # resume / finish a run
    ./studio make --run runs/... --redo-frames 3 --redo-clips 5   # regenerate single shots
    ./studio status runs/protein-bar-20261013-1810
    ./studio hero protein-bar --ratio 16:9                        # one 8s cinematic product ad

Every stage saves its output in the run folder and is skipped when it already exists, so a run
can be stopped, inspected (plan.json is editable), and resumed at any point.
"""

import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import CFG, CREATORS, PRODUCTS, RUNS, log, prompt, slugify
from .gemini import CHECK_MODEL, RATIOS, VEO_SECONDS, GeminiError, image, text, video
from .tts import TTSError, speak

STAGES = ("plan", "voice", "images", "videos", "assemble")
SHOT_TYPES = ("creator", "product", "lifestyle")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
AUDIO_EXTS = (".wav", ".mp3")
MAX_REFS = 4             # product photos sent with every image request
MIN_SCORE = 8            # consistency score a frame needs to pass QA
GAP = 0.3                # breathing room after each voiceover line (seconds)
SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080)}
# Voiceover languages: TTS language code, caption font, script, translation style.
LANGS = {
    "en": {"name": "English", "code": "en-IN", "font": "Liberation Sans", "script": "Latin",
           "style": "Indian English as spoken by young creators."},
    "hi": {"name": "Hindi", "code": "hi-IN", "font": "Noto Sans Devanagari", "script": "Devanagari", "size": 1.15,
           "style": "Everyday spoken Hindi as used in India (Hindustani), not shuddh/textbook Hindi. Keep common "
                    "English words Indians naturally use in Hindi speech (exam, hostel, pack, link in bio) but write "
                    "them in Devanagari."},
}
NEGATIVE = ("talking, speaking, lip sync, mouth forming words, dialogue, subtitles, captions, on-screen text, "
            "watermark, logo distortion, morphing packaging, changing label text, garbled text, extra products, "
            "duplicate products, deformed hands, extra fingers, cartoon, low quality, blurry")









def orientation(ratio: str) -> str:
    return "vertical, mobile-first" if ratio == "9:16" else "horizontal, widescreen"


def find(folder: Path, stem: str, exts) -> Path | None:
    for ext in exts:
        p = folder / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def images_in(folder: Path) -> list:
    return sorted(p for p in folder.glob("*") if p.suffix.lower() in IMAGE_EXTS) if folder.exists() else []


# --- ffmpeg helpers -------------------------------------------------------------

def run_ff(args: list, cwd=None):
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
                       cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.strip()[-800:]}")


def duration(path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def has_audio(path) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return bool(r.stdout.strip())


# --- products & creators ---------------------------------------------------------

def load_product(slug: str) -> dict:
    f = PRODUCTS / slug / "product.json"
    if not f.exists():
        sys.exit(f"Unknown product {slug!r}. Add it with: ./studio product add {slug} --name ... --images ...")
    bible = json.loads(f.read_text())
    bible["_dir"] = PRODUCTS / slug
    return bible


def product_refs(bible: dict) -> list:
    refs = images_in(bible["_dir"] / "refs")[:MAX_REFS]
    if not refs:
        sys.exit(f"No reference photos in {bible['_dir'] / 'refs'}")
    return refs


def bible_text(b: dict) -> str:
    colors = "; ".join(f"{c.get('name')} {c.get('hex', '')} ({c.get('where', '')})" for c in b.get("colors", []))
    lines = [
        f"Name: {b.get('name')}",
        f"Brand: {b.get('brand')}",
        f"Category: {b.get('category')}",
        f"Locked description: {b.get('locked_description')}",
        f"Exact label text: {' | '.join(b.get('label_text', []))}",
        f"Colors: {colors}",
        f"Shape and size: {b.get('shape_and_size')}",
        f"Materials: {b.get('materials')}",
        f"Inside / in use: {b.get('inside_product')}",
        f"Must never: {'; '.join(b.get('must_never', []))}",
        f"Best angles: {'; '.join(b.get('best_angles', []))}",
        f"Ad style: {b.get('ad_style')}",
    ]
    return "\n".join(lines)


def product_lock(b: dict) -> str:
    return (f"The product is the exact same {b.get('name')} by {b.get('brand')} from the reference images. "
            f"{b.get('locked_description')} Printed label text, exactly: \"{' / '.join(b.get('label_text', []))}\". "
            f"Never: {'; '.join(b.get('must_never', []))}.")


def video_lock(b: dict) -> str:
    return (f"The {b.get('name')} stays exactly as in the first frame for the whole shot: same "
            f"{b.get('shape_and_size')}, same colors, same logo and same printed label text; the packaging never "
            f"morphs, warps, melts, duplicates or changes its text, and it never leaves the frame.")


def load_creator(name: str | None) -> dict | None:
    if not name:
        return None
    d = CREATORS / name
    f = d / "creator.json"
    if not f.exists():
        sys.exit(f"Unknown creator {name!r}. Add one with: ./studio creator add {name} --describe ...")
    c = json.loads(f.read_text())
    c["_refs"] = [p for p in (find(d, "portrait", IMAGE_EXTS), find(d, "sheet", IMAGE_EXTS)) if p]
    return c


def cmd_product(args):
    if args.action == "list":
        for f in sorted(PRODUCTS.glob("*/product.json")):
            b = json.loads(f.read_text())
            log(f"{f.parent.name:24} {b.get('name')}  ({len(images_in(f.parent / 'refs'))} refs, "
                f"creator: {b.get('creator') or '-'})")
        return
    if not args.slug:
        sys.exit("product slug is required")
    slug = slugify(args.slug)
    pdir = PRODUCTS / slug
    if args.action == "show":
        log(json.dumps({k: v for k, v in load_product(slug).items() if not k.startswith("_")}, indent=2))
        return
    # add / rebuild
    if (pdir / "product.json").exists() and not args.force and args.action == "add":
        sys.exit(f"{slug} already exists. Use `product rebuild {slug}` (or --force) to regenerate its bible.")
    refs_dir = pdir / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    start = len(images_in(refs_dir))
    for i, src in enumerate(args.images or [], start + 1):
        src = Path(src).expanduser()
        if not src.exists():
            sys.exit(f"image not found: {src}")
        shutil.copy(src, refs_dir / f"{i:02d}{src.suffix.lower()}")
    refs = images_in(refs_dir)
    if not refs:
        sys.exit("Give at least one product photo with --images (front of the pack, label readable).")
    old = json.loads((pdir / "product.json").read_text()) if (pdir / "product.json").exists() else {}
    name = args.name or old.get("name") or args.slug
    log(f"Analysing {len(refs)} reference photo(s) with Gemini ...")
    bible = text(prompt("ugc/product_bible.md", NAME=name, NOTES=args.notes or old.get("notes") or "none"),
                 images=refs[:MAX_REFS], json_out=True, temperature=0.2)
    bible["name"] = name
    bible["slug"] = slug
    bible["notes"] = args.notes or old.get("notes", "")
    bible["creator"] = args.creator or old.get("creator")
    bible["voice"] = args.voice or old.get("voice")
    (pdir / "product.json").write_text(json.dumps(bible, indent=2, ensure_ascii=False))
    log(bible_text(bible))
    log(f"\nSaved {pdir / 'product.json'}\nCheck 'label_text' and 'colors' against the real pack and fix "
        f"anything wrong by editing that file - every shot is generated from it.")


def cmd_creator(args):
    if args.action == "list":
        for f in sorted(CREATORS.glob("*/creator.json")):
            c = json.loads(f.read_text())
            log(f"{f.parent.name:16} voice={c.get('voice') or '-':22} {c.get('description', '')[:80]}")
        return
    if not args.name:
        sys.exit("creator name is required")
    name = slugify(args.name)
    d = CREATORS / name
    if (d / "creator.json").exists() and not args.force:
        sys.exit(f"Creator {name} exists (use --force to recreate).")
    d.mkdir(parents=True, exist_ok=True)
    if args.image:
        src = Path(args.image).expanduser()
        portrait = d / f"portrait{src.suffix.lower()}"
        shutil.copy(src, portrait)
        description = args.describe or text(
            "Describe this person for an AI casting sheet in one dense sentence: apparent age, gender, ethnicity, "
            "skin tone, face shape, hair (color, length, style), build, distinctive features, and clothing style. "
            "No names.", images=[portrait], temperature=0.2)
    elif args.describe:
        description = args.describe
        log("Generating creator portrait ...")
        portrait = image(
            f"Photorealistic smartphone photo of a real UGC content creator for a casting sheet: {description}. "
            "Head and shoulders, facing the camera, relaxed natural smile, plain light background, soft natural "
            "daylight, natural skin texture, no makeup filter, no text, no watermark.",
            d / "portrait.png", aspect="3:4")
    else:
        sys.exit("Give --describe \"...\" and/or --image face.jpg")
    if not args.no_sheet:
        log("Generating character turnaround sheet (front, three-quarter, profile, full body) ...")
        image("Character reference sheet of the exact same person from the reference photo: four photorealistic "
              "views side by side on a plain light gray background - front close-up, three-quarter view, side "
              "profile, and full body standing. Identical face, hair, skin tone, build and outfit in every view. "
              "Natural daylight, no text, no labels, no watermark.", d / "sheet.png", refs=[portrait], aspect="16:9")
    info = {"name": name, "description": description, "voice": args.voice}
    (d / "creator.json").write_text(json.dumps(info, indent=2, ensure_ascii=False))
    log(f"Saved creator {name}: {description}\nLook at {d} and recreate with --force if the face is not right.")


# --- run settings ------------------------------------------------------------------

def parse_langs(value: str, script: str) -> list:
    langs = [l.strip().lower() for l in value.split(",") if l.strip()]
    bad = [l for l in langs if l not in LANGS]
    if bad:
        sys.exit(f"Unsupported language {bad[0]!r}; available: {', '.join(LANGS)}")
    return list(dict.fromkeys([script, *langs]))  # the script's language always comes first


def ask_ratio() -> str:
    if not sys.stdin.isatty():
        sys.exit("Pick the video ratio with --ratio 9:16 (vertical: Reels/Shorts/TikTok) or --ratio 16:9 (YouTube).")
    print("Video aspect ratio?\n  1) 9:16  vertical - Instagram Reels, Shorts, TikTok\n  2) 16:9  horizontal - YouTube, web")
    while True:
        a = input("Choose 1 or 2: ").strip().lower()
        if a in ("1", "9:16", "vertical", "v"):
            return "9:16"
        if a in ("2", "16:9", "horizontal", "h"):
            return "16:9"


def new_run(args) -> Path:
    if not args.product or not args.script:
        sys.exit("Usage: ugc.py make <product> <script.txt>   (or --run runs/<folder> to resume)")
    bible = load_product(slugify(args.product))
    script_path = Path(args.script).expanduser()
    script = script_path.read_text().strip() if script_path.exists() else args.script.strip()
    if len(script.split()) < 5:
        sys.exit("The script looks empty or too short; pass a .txt file with the voiceover script.")
    ratio = args.ratio or ask_ratio()
    creator = args.creator or bible.get("creator")
    if creator == "none":
        creator = None
    elif not creator:
        known = sorted(p.parent.name for p in CREATORS.glob("*/creator.json"))
        creator = known[0] if len(known) == 1 else None
        if len(known) > 1:
            sys.exit(f"Several creators exist ({', '.join(known)}); pick one with --creator NAME (or --creator none).")
    c = load_creator(creator)
    run = RUNS / f"{bible['slug']}-{time.strftime('%Y%m%d-%H%M%S')}"
    run.mkdir(parents=True)
    (run / "script.txt").write_text(script + "\n")
    settings = {
        "product": bible["slug"], "ratio": ratio, "creator": creator, "script_lang": args.script_lang,
        "languages": parse_langs(args.languages or CFG.get("UGC_LANGUAGES") or args.script_lang, args.script_lang),
        "voice": args.voice or (c or {}).get("voice") or bible.get("voice") or CFG.get("TTS_VOICE"),
        "engine": args.engine or CFG.get("TTS_ENGINE", "eleven"),
        "captions": not args.no_captions, "music": str(Path(args.music).resolve()) if args.music else None,
        "ambient": 0.15 if args.ambient is None else args.ambient, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (run / "settings.json").write_text(json.dumps(settings, indent=2))
    log(f"New run: {run}  ({ratio}, creator: {creator or 'none'}, voice: {settings['voice']})")
    return run


# --- stages ------------------------------------------------------------------------

def stage_plan(run, s, bible, creator):
    plan_file = run / "plan.json"
    if plan_file.exists():
        return json.loads(plan_file.read_text())
    script = (run / "script.txt").read_text().strip()
    if creator:
        creator_text = (f"Name: {creator['name']}. {creator['description']} (Reference photo attached after the "
                        f"product photos.) Use this exact person in every 'creator' shot.")
    else:
        creator_text = ("NO CREATOR. Use only 'product' and 'lifestyle' shots; hands are fine but never show a "
                        "face.")
    prompt = prompt("ugc/master_prompt.md", RATIO=s["ratio"], ORIENTATION=orientation(s["ratio"]),
                  PRODUCT_BIBLE=bible_text(bible), CREATOR=creator_text, SCRIPT=script)
    imgs = product_refs(bible) + (creator["_refs"][:1] if creator else [])
    log("Planning shots with Gemini (master prompt) ...")
    plan = text(prompt, images=imgs, json_out=True, temperature=0.8)
    shots = plan.get("shots") or []
    if not shots:
        raise GeminiError(f"plan has no shots: {json.dumps(plan)[:300]}")
    for i, shot in enumerate(shots, 1):
        shot["id"] = i
        if shot.get("type") not in SHOT_TYPES:
            shot["type"] = "product"
        if shot["type"] == "creator" and not creator:
            shot["type"] = "lifestyle"
        for key in ("voiceover", "image_prompt", "video_prompt"):
            if not shot.get(key):
                raise GeminiError(f"shot {i} is missing {key!r}")
    norm = lambda t: re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
    if norm(" ".join(sh["voiceover"] for sh in shots)) != norm(script):
        log("WARNING: the planned voiceover lines don't match the script word for word. "
            "Edit 'voiceover' in plan.json if needed, then rerun with --run.")
    plan_file.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    log(f"Plan: {plan.get('title')} - {len(shots)} shots ({plan.get('style')})")
    for sh in shots:
        log(f"  {sh['id']:>2}. [{sh.get('beat', ''):7}] {sh['type']:9} \"{sh['voiceover']}\"")
    return plan


def stage_voice(run, s, plan, lang):
    vdir = run / f"voice{sfx(s, lang)}"
    for sh, line in zip(plan["shots"], lines_for(plan, s, lang)):
        stem = f"shot_{sh['id']:02d}"
        if find(vdir, stem, AUDIO_EXTS):
            continue
        path = speak(line, engine=s["engine"], voice=s["voice"], out=vdir / f"{stem}.wav",
                     language=LANGS[lang]["code"])
        log(f"  voice {lang} {stem}: {duration(path):.1f}s")


def script_lang(s) -> str:
    return s.get("script_lang", "en")


def languages(s) -> list:
    return s.get("languages") or [script_lang(s)]


def sfx(s, lang) -> str:
    """File suffix for a language: the script's own language keeps the plain names (voice/, final.mp4)."""
    return "" if lang == script_lang(s) else f"_{lang}"


def lines_for(plan, s, lang) -> list:
    if lang == script_lang(s):
        return [sh["voiceover"] for sh in plan["shots"]]
    return plan["translations"][lang]


def stage_translate(run, s, plan, bible, lang):
    if lang == script_lang(s) or len(plan.get("translations", {}).get(lang, [])) == len(plan["shots"]):
        return
    info = LANGS[lang]
    src = [sh["voiceover"] for sh in plan["shots"]]
    log(f"Adapting voiceover to {info['name']} ...")
    creator = load_creator(s.get("creator"))
    speaker = (creator["description"] if creator else
               "an off-screen narrator; use gender-neutral phrasing where the language allows")
    out = text(prompt("ugc/translate_voiceover.md", LANGUAGE=info["name"], COUNT=len(src),
                    STYLE=info["style"], SCRIPT=info["script"], PRODUCT=bible.get("name"), SPEAKER=speaker,
                    LINES=json.dumps(src, ensure_ascii=False)), json_out=True, temperature=0.5)
    lines = out.get("lines") or []
    if len(lines) != len(src):
        raise GeminiError(f"{info['name']} adaptation returned {len(lines)} lines for {len(src)} shots")
    plan.setdefault("translations", {})[lang] = lines
    (run / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False))
    for i, line in enumerate(lines, 1):
        log(f"  {i:>2}. {line}")


def voice_len(run, shot_id, s=None) -> float:
    """Longest voice line for this shot across the run's languages (the clip must fit all of them)."""
    dirs = [run / f"voice{sfx(s, l)}" for l in languages(s)] if s else [run / "voice"]
    found = [find(d, f"shot_{shot_id:02d}", AUDIO_EXTS) for d in dirs]
    return max(duration(p) for p in found if p)


def check_frame(frame, bible, shot, creator) -> dict:
    refs = product_refs(bible)
    creator_refs = creator["_refs"][:1] if creator and shot["type"] == "creator" else []
    prompt = prompt("ugc/consistency_check.md", CREATOR_NOTE=", then the creator reference photo" if creator_refs else "",
                  CREATOR_CHECK=("The person is the same as the creator reference (face, hair, skin tone, build)"
                                 if creator_refs else "Any people look natural (correct hands and fingers)"),
                  SHOT=shot["image_prompt"][:600], PRODUCT_BIBLE=bible_text(bible), MIN_SCORE=MIN_SCORE)
    qa = text(prompt, images=[*refs, *creator_refs, frame], json_out=True, model=CHECK_MODEL, temperature=0)
    qa["pass"] = bool(qa.get("pass")) and (qa.get("score") or 0) >= MIN_SCORE
    return qa


def image_prompt(shot, bible, creator, ratio, fix="") -> str:
    n = len(product_refs(bible))
    who = ""
    if creator and shot["type"] == "creator":
        who = (f" The next reference image(s) show the creator: keep the exact same person - same face, hair, "
               f"skin tone and build ({creator['description']}).")
    return (f"REFERENCE IMAGES: the first {n} image(s) are real photos of the product - the ground truth. Wherever "
            f"the product appears, render it as if that exact real pack were photographed in this scene: reuse its "
            f"exact artwork, logo and lettering, only adapting angle, lighting and perspective.{who}\n\n"
            f"CREATE ONE PHOTOREALISTIC IMAGE in {ratio} ({orientation(ratio)}) framing:\n{shot['image_prompt']}\n\n"
            f"PRODUCT LOCK: {product_lock(bible)}\n"
            f"No captions, no subtitles, no added text or graphics, no watermarks. The only text allowed is the text "
            f"physically printed on the product.{fix}")


def make_frame(run, s, bible, creator, shot, tries):
    fdir = run / "frames"
    stem = f"shot_{shot['id']:02d}"
    if find(fdir, stem, IMAGE_EXTS):
        return
    refs = product_refs(bible) + (creator["_refs"] if creator and shot["type"] == "creator" else [])
    attempts, fix = [], ""
    for n in range(1, tries + 1):
        path = image(image_prompt(shot, bible, creator, s["ratio"], fix), fdir / f"{stem}_try{n}.png",
                     refs=refs, aspect=s["ratio"])
        try:
            qa = check_frame(path, bible, shot, creator)
        except GeminiError as e:
            qa = {"score": None, "pass": True, "issues": [f"QA unavailable: {e}"], "fix": ""}
        qa["file"] = path.name
        attempts.append(qa)
        log(f"  frame {stem} try {n}: score {qa.get('score')} {'PASS' if qa['pass'] else 'FAIL'}"
            f"{'' if qa['pass'] else ' - ' + '; '.join(qa.get('issues', []))[:160]}")
        if qa["pass"]:
            break
        fix = (f"\n\nFIX THESE PROBLEMS FROM THE LAST ATTEMPT: {'; '.join(qa.get('issues', []))}. "
               f"{qa.get('fix', '')}")
    best = max(attempts, key=lambda a: (a["pass"], a.get("score") or 0))
    src = fdir / best["file"]
    shutil.copy(src, fdir / f"{stem}{src.suffix}")
    (fdir / f"{stem}.qa.json").write_text(json.dumps({"chosen": best["file"], "attempts": attempts}, indent=2))
    if not best["pass"]:
        log(f"  frame {stem}: no attempt passed QA; kept the best one ({best.get('score')}). Review it.")


def video_prompt(shot, bible) -> str:
    who = ("The person never speaks and their mouth does not form words; they react naturally. "
           if shot["type"] == "creator" else "")
    return (f"{shot['video_prompt']} {video_lock(bible)} {who}Audio: natural ambient sound only, no speech, "
            f"no voiceover, no music. No on-screen text, subtitles, captions or watermarks.")


def make_clip(run, s, bible, shot):
    cdir = run / "clips"
    stem = f"shot_{shot['id']:02d}"
    if (cdir / f"{stem}.mp4").exists():
        return
    frame = find(run / "frames", stem, IMAGE_EXTS)
    need = voice_len(run, shot["id"], s) + GAP
    seconds = next((d for d in VEO_SECONDS if d >= need), VEO_SECONDS[-1])
    log(f"  clip {stem}: Veo {seconds}s for {need:.1f}s of voiceover ...")
    video(video_prompt(shot, bible), cdir / f"{stem}.mp4", start=frame, aspect=s["ratio"], seconds=seconds,
          negative=NEGATIVE)
    log(f"  clip {stem}: done")


def parallel(fn, shots, workers, label):
    errors = []

    def one(sh):
        try:
            fn(sh)
        except (GeminiError, TTSError, RuntimeError, OSError) as e:
            errors.append(f"shot {sh['id']}: {e}")
            log(f"  {label} shot {sh['id']} FAILED: {e}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, shots))
    if errors:
        raise SystemExit(f"{label}: {len(errors)} shot(s) failed - fix and rerun with --run (finished shots are "
                         f"kept):\n  " + "\n  ".join(errors))


def ass_time(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def write_captions(path: Path, timing, ratio, font: str, scale: float = 1.0):
    w, h = SIZES[ratio]
    vertical = ratio == "9:16"
    size, margin = (78, int(h * 0.24)) if vertical else (62, 90)
    size = round(size * scale)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: UGC,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,"
        f"60,60,{margin},1", "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for start, spoken, words in timing:
        chunks, cur = [], []
        for word in words.split():
            cur.append(word)
            if len(cur) >= 3 or len(" ".join(cur)) >= 16 or word[-1] in ".,!?;:।":
                chunks.append(" ".join(cur))
                cur = []
        if cur:
            chunks.append(" ".join(cur))
        total = sum(len(c) for c in chunks) or 1
        t = start
        for c in chunks:
            d = spoken * len(c) / total
            safe = c.replace("{", "(").replace("}", ")")
            lines.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + d)},UGC,,0,0,0,,{safe}")
            t += d
    path.write_text("\n".join(lines) + "\n")


def stage_assemble(run, s, plan, lang):
    w, h = SIZES[s["ratio"]]
    x = sfx(s, lang)
    pdir = run / f"parts{x}"
    pdir.mkdir(exist_ok=True)
    timing, offset, parts = [], 0.0, []
    for sh, line in zip(plan["shots"], lines_for(plan, s, lang)):
        stem = f"shot_{sh['id']:02d}"
        clip, voice = run / "clips" / f"{stem}.mp4", find(run / f"voice{x}", stem, AUDIO_EXTS)
        spoken = duration(voice)
        length = max(spoken + GAP, 1.5)
        slow = min(max(length / duration(clip), 1.0), 1.25)   # stretch a bit before freezing the last frame
        vf = (f"[0:v]setpts={slow:.4f}*PTS,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
              f"fps=30,setsar=1,format=yuv420p,tpad=stop_mode=clone:stop_duration={length:.2f},"
              f"trim=duration={length:.3f},setpts=PTS-STARTPTS[v]")
        af = f"[1:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={length:.3f}[vo]"
        if s.get("ambient", 0) > 0 and has_audio(clip):
            af += (f";[0:a]aresample=48000,aformat=channel_layouts=stereo,volume={s['ambient']},apad,"
                   f"atrim=duration={length:.3f}[amb];[vo][amb]amix=inputs=2:duration=first:normalize=0[a]")
        else:
            af += ";[vo]anull[a]"
        out = pdir / f"{stem}.mp4"
        run_ff(["-i", str(clip), "-i", str(voice), "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(out)])
        parts.append(out)
        timing.append((offset, spoken, line))
        offset += length
    (pdir / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in parts))
    run_ff(["-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", f"../joined{x}.mp4"], cwd=pdir)

    final = run / f"final{x}.mp4"
    inputs, vf, af = ["-i", f"joined{x}.mp4"], None, None
    if s.get("captions", True):
        font = CFG.get(f"CAPTION_FONT_{lang.upper()}") or LANGS[lang]["font"]
        write_captions(run / f"captions{x}.ass", timing, s["ratio"], font, LANGS[lang].get("size", 1.0))
        vf = f"subtitles=captions{x}.ass"
    if s.get("music"):
        inputs += ["-stream_loop", "-1", "-i", s["music"]]
        af = (f"[1:a]volume=0.12,afade=t=out:st={max(offset - 1.5, 0):.2f}:d=1.5[m];"
              f"[0:a][m]amix=inputs=2:duration=first:normalize=0[a]")
    args = list(inputs)
    if af:
        args += ["-filter_complex", af, "-map", "0:v", "-map", "[a]"]
    if vf:
        args += ["-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "18"]
    else:
        args += ["-c:v", "copy"]
    args += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", final.name]
    run_ff(args, cwd=run)
    log(f"\nFINAL VIDEO ({LANGS[lang]['name']}): {final}  ({offset:.1f}s, {s['ratio']}, {len(parts)} shots)")


def redo(run, ids, what):
    for i in ids:
        stem = f"shot_{int(i):02d}"
        targets = [run / "clips" / f"{stem}.mp4"] if what in ("frames", "clips") else []
        if what == "frames":  # a new frame needs a new clip too
            targets += list((run / "frames").glob(f"{stem}*"))
        if what == "voice":
            targets += [p for d in run.glob("voice*") for p in d.glob(f"{stem}.*")]
        for t in targets:
            t.unlink(missing_ok=True)
        log(f"  cleared {what} for {stem}")


def cmd_make(args):
    run = Path(args.run).resolve() if args.run else new_run(args)
    if not (run / "settings.json").exists():
        sys.exit(f"{run} is not a run folder")
    s = json.loads((run / "settings.json").read_text())
    if args.ratio and args.ratio != s["ratio"]:
        sys.exit(f"This run is {s['ratio']}; start a new run for {args.ratio} (frames are composed per ratio).")
    if args.run:  # allow changing finishing options when resuming
        if args.no_captions:
            s["captions"] = False
        if args.music:
            s["music"] = str(Path(args.music).resolve())
        if args.ambient is not None:
            s["ambient"] = args.ambient
        if args.languages:
            s["languages"] = parse_langs(args.languages, script_lang(s))
    (run / "settings.json").write_text(json.dumps(s, indent=2))
    bible = load_product(s["product"])
    creator = load_creator(s.get("creator"))
    for what in ("frames", "clips", "voice"):
        ids = getattr(args, f"redo_{what}")
        if ids:
            redo(run, ids.split(","), what)

    until = STAGES.index(args.until) if args.until else len(STAGES) - 1
    plan = stage_plan(run, s, bible, creator)
    if until == 0:
        return log(f"\nStopped after plan. Review/edit {run / 'plan.json'}, then: ./studio make --run {run}")
    shots = plan["shots"]
    if args.only:
        wanted = {int(x) for x in args.only.split(",")}
        shots = [sh for sh in shots if sh["id"] in wanted]
    for lang in languages(s):
        stage_translate(run, s, plan, bible, lang)
    log(f"Voiceover in {', '.join(LANGS[l]['name'] for l in languages(s))} (ElevenLabs -> Sarvam fallback) ...")
    for lang in languages(s):
        stage_voice(run, s, plan, lang)
    if until == 1:
        return log(f"\nStopped after voice: {run / 'voice'}")
    log(f"Frames ({len(shots)} shots, consistency QA on each) ...")
    parallel(lambda sh: make_frame(run, s, bible, creator, sh, args.tries), shots, args.workers, "frames")
    if until == 2:
        return log(f"\nStopped after images. Review {run / 'frames'} (and *.qa.json), redo bad ones with "
                   f"--redo-frames N, then: ./studio make --run {run}")
    log(f"Video clips with Veo ({len(shots)} shots) ...")
    parallel(lambda sh: make_clip(run, s, bible, sh), shots, args.workers, "videos")
    if until == 3 or args.only:
        return log(f"\nClips ready in {run / 'clips'}. Finish with: ./studio make --run {run}")
    missing = [sh["id"] for sh in plan["shots"] if not (run / "clips" / f"shot_{sh['id']:02d}.mp4").exists()]
    if missing:
        sys.exit(f"Missing clips for shots {missing}; rerun without --only.")
    for lang in languages(s):
        log(f"Assembling {LANGS[lang]['name']} ...")
        stage_assemble(run, s, plan, lang)


def cmd_status(args):
    run = Path(args.run)
    s = json.loads((run / "settings.json").read_text())
    log(json.dumps(s, indent=2))
    if not (run / "plan.json").exists():
        return log("plan: not made yet")
    plan = json.loads((run / "plan.json").read_text())
    log(f"\n{plan.get('title')} - {plan.get('style')}")
    for sh in plan["shots"]:
        stem = f"shot_{sh['id']:02d}"
        qa_file = run / "frames" / f"{stem}.qa.json"
        qa = json.loads(qa_file.read_text()) if qa_file.exists() else None
        score = next((a.get("score") for a in qa["attempts"] if a["file"] == qa["chosen"]), None) if qa else None
        log(f"  {sh['id']:>2}. {sh['type']:9} voice:{'Y' if find(run / 'voice', stem, AUDIO_EXTS) else '-'} "
            f"frame:{'Y' if find(run / 'frames', stem, IMAGE_EXTS) else '-'}"
            f"{f' (QA {score})' if score is not None else ''} "
            f"clip:{'Y' if (run / 'clips' / f'{stem}.mp4').exists() else '-'}  \"{sh['voiceover'][:60]}\"")
    for lang in languages(s):
        f = run / f"final{sfx(s, lang)}.mp4"
        log(f"final ({LANGS[lang]['name']}): {f if f.exists() else 'not assembled'}")


def cmd_hero(args):
    bible = load_product(slugify(args.product))
    ratio = args.ratio or ask_ratio()
    run = RUNS / f"{bible['slug']}-hero-{time.strftime('%Y%m%d-%H%M%S')}"
    run.mkdir(parents=True)
    refs = product_refs(bible)
    idea = f"CREATIVE DIRECTION FROM THE CLIENT: {args.idea}" if args.idea else ""
    log("Writing cinematic prompt (universal product-ad prompt) ...")
    vprompt = text(prompt("ugc/universal_hero.md", SECONDS=args.seconds, RATIO=ratio,
                        ORIENTATION=orientation(ratio), PRODUCT_BIBLE=bible_text(bible), IDEA=idea),
                   images=refs, temperature=0.9)
    (run / "video_prompt.txt").write_text(vprompt + "\n")
    log(vprompt)
    shot = {"id": 1, "type": "product", "image_prompt":
            f"The opening frame of this commercial, before any motion starts: {vprompt[:1200]}"}
    s = {"ratio": ratio}
    make_frame(run, s, bible, None, shot, args.tries)
    frame = find(run / "frames", "shot_01", IMAGE_EXTS)
    log(f"Veo {args.seconds}s ...")
    out = video(f"{vprompt} {video_lock(bible)} No on-screen text, captions or watermarks.", run / "hero.mp4",
                start=frame, aspect=ratio, seconds=args.seconds, negative=NEGATIVE)
    log(f"\nHERO VIDEO: {out}")


def register(sub):

    pp = sub.add_parser("product", help="Register products and build their locked product bible")
    pp.add_argument("action", choices=("add", "rebuild", "show", "list"))
    pp.add_argument("slug", nargs="?")
    pp.add_argument("--name", help="Product name, e.g. 'Whey Crunch Protein Bar - Choco Peanut'")
    pp.add_argument("--images", nargs="+", help="Real product photos (front with readable label first)")
    pp.add_argument("--notes", help="Anything the photos don't show (flavor, inside texture, claims allowed)")
    pp.add_argument("--creator", help="Default creator for this product")
    pp.add_argument("--voice", help="Default voice for this product")
    pp.add_argument("--force", action="store_true")
    pp.set_defaults(func=cmd_product)

    pc = sub.add_parser("creator", help="Create a consistent on-camera creator (face reference + sheet)")
    pc.add_argument("action", choices=("add", "list"))
    pc.add_argument("name", nargs="?")
    pc.add_argument("--describe", help="e.g. 'Indian woman, 24, athletic, long black ponytail, grey sports bra'")
    pc.add_argument("--image", help="Use a real photo instead of generating a face")
    pc.add_argument("--voice", help="ElevenLabs voice name/id (or Sarvam name) for this creator")
    pc.add_argument("--no-sheet", action="store_true", help="Skip the turnaround sheet")
    pc.add_argument("--force", action="store_true")
    pc.set_defaults(func=cmd_creator)

    pm = sub.add_parser("make", help="Make a UGC ad from a script (resumable)")
    pm.add_argument("product", nargs="?")
    pm.add_argument("script", nargs="?", help="Script .txt file (or the script text itself)")
    pm.add_argument("--run", help="Resume an existing run folder")
    pm.add_argument("--ratio", choices=RATIOS, help="9:16 or 16:9 (asked if omitted)")
    pm.add_argument("--creator", help="Creator name, or 'none' for product-only ads")
    pm.add_argument("--voice", help="Voice name/id (default: creator/product voice, then TTS_VOICE)")
    pm.add_argument("--engine", choices=("eleven", "sarvam"), help="First TTS engine (default eleven)")
    pm.add_argument("--languages", help="Voiceover languages, e.g. en,hi -> final.mp4 + final_hi.mp4 (same visuals)")
    pm.add_argument("--script-lang", default="en", choices=tuple(LANGS), help="Language the script is written in")
    pm.add_argument("--until", choices=STAGES, help="Stop after this stage (e.g. images, to review frames)")
    pm.add_argument("--only", help="Only generate these shot ids for frames/clips, e.g. 2,5")
    pm.add_argument("--redo-frames", help="Regenerate frames (and their clips) for shot ids, e.g. 3,4")
    pm.add_argument("--redo-clips", help="Regenerate Veo clips for shot ids")
    pm.add_argument("--redo-voice", help="Regenerate voice lines for shot ids")
    pm.add_argument("--tries", type=int, default=3, help="Image attempts per shot until QA passes (default 3)")
    pm.add_argument("--workers", type=int, default=3, help="Parallel Gemini/Veo jobs (default 3)")
    pm.add_argument("--no-captions", action="store_true")
    pm.add_argument("--music", help="Background music file (mixed quietly under the voice)")
    pm.add_argument("--ambient", type=float, default=None, help="Veo ambient sound level 0-1 (default 0.15)")
    pm.set_defaults(func=cmd_make)

    ps = sub.add_parser("status", help="Show what a run has finished")
    ps.add_argument("run")
    ps.set_defaults(func=cmd_status)

    ph = sub.add_parser("hero", help="One cinematic product clip from the universal product-ad prompt")
    ph.add_argument("product")
    ph.add_argument("--ratio", choices=RATIOS)
    ph.add_argument("--seconds", type=int, choices=VEO_SECONDS, default=8)
    ph.add_argument("--idea", help="Optional direction, e.g. 'exploded view that reforms'")
    ph.add_argument("--tries", type=int, default=3)
    ph.set_defaults(func=cmd_hero)
