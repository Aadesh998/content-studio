"""./studio - one command for the whole content studio."""

import argparse
import shutil
import subprocess
import sys

from . import tts, ugc
from .config import CFG, ENV_FILE
from .gemini import GeminiError
from .tts import TTSError

EXAMPLES = """
quick start:
  ./studio doctor                                         check keys, ffmpeg, fonts
  ./studio product add <slug> --name "..." --images front.jpg   register a product
  ./studio creator add <name> --describe "..."            create an on-camera creator
  ./studio make <product> scripts/<script>.txt            make a UGC ad (asks 9:16 / 16:9; English + Hindi)
  ./studio voice "Hello" -p                               test a voice

every command has --help, e.g. ./studio make --help
"""


def cmd_doctor(args):
    ok = True

    def row(name, good, detail=""):
        nonlocal ok
        ok &= good
        print(f"  {'OK ' if good else 'MISSING'}  {name}{'  - ' + detail if detail and (not good or name.startswith('python')) else ''}")

    print("Tools")
    row("python >= 3.10", sys.version_info >= (3, 10), sys.version.split()[0])
    row("ffmpeg", bool(shutil.which("ffmpeg")), "install ffmpeg (with libass) from your package manager")
    row("ffprobe", bool(shutil.which("ffprobe")))
    if shutil.which("ffmpeg"):
        filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        row("ffmpeg subtitles filter (libass)", " subtitles " in filters, "needed for burned-in captions")
    if shutil.which("fc-list"):
        fonts = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True).stdout
        for lang, info in ugc.LANGS.items():
            row(f"caption font for {info['name']}: {info['font']}", info["font"] in fonts,
                "install it or set CAPTION_FONT_" + lang.upper())
    print("Keys (.env)")
    row(".env file", ENV_FILE.exists(), "cp .env.example .env and fill it in")
    for key in ("GEMINI_API_KEY", "ELEVEN_API_KEY", "SARVAM_API_KEY"):
        row(key, len(CFG.get(key, "")) > 10)
    if args.online:
        print("API check")
        try:
            from .gemini import text
            row("Gemini", text("Reply with OK") != "")
        except GeminiError as e:
            row("Gemini", False, str(e)[:120])
        for engine in ("eleven", "sarvam"):
            try:
                import tempfile
                with tempfile.TemporaryDirectory() as tmp:
                    tts.speak("Test.", engine=engine, fallback=[], out=f"{tmp}/t.wav")
                row(f"TTS {engine}", True)
            except TTSError as e:
                row(f"TTS {engine}", False, str(e)[:120])
    print("\nAll good." if ok else "\nFix the MISSING items above.")
    sys.exit(0 if ok else 1)


def main():
    p = argparse.ArgumentParser(prog="studio", description="AI content studio: UGC ad videos",
                                epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")
    d = sub.add_parser("doctor", help="Check tools, fonts and API keys")
    d.add_argument("--online", action="store_true", help="Also make tiny test calls to Gemini/ElevenLabs/Sarvam")
    d.set_defaults(func=cmd_doctor)
    ugc.register(sub)
    tts.register(sub)
    args = p.parse_args()
    try:
        args.func(args)
    except (GeminiError, TTSError, RuntimeError) as e:
        sys.exit(f"ERROR: {e}")
    except KeyboardInterrupt:
        sys.exit("\nStopped. Rerun the same command to resume - finished steps are kept.")
