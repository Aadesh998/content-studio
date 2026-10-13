"""Shared settings, paths and small helpers for every adgen module."""

import os
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
PROMPTS = ROOT / "prompts"
PRODUCTS = ROOT / "products"     # product photos + locked product bibles
CREATORS = ROOT / "creators"     # on-camera creator faces + voices
RUNS = ROOT / "runs"             # one folder per generated ad
SCRIPTS = ROOT / "scripts"       # voiceover scripts


def load_env() -> dict:
    """.env next to the project; real environment variables with the same names override it."""
    cfg = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                cfg[key.strip()] = value.split(" #")[0].strip().strip('"')
    prefixes = ("SARVAM_", "ELEVEN_", "TTS_", "GEMINI_", "VEO_", "UGC_", "CAPTION_")
    cfg.update({k: v for k, v in os.environ.items() if k.startswith(prefixes)})
    return cfg


CFG = load_env()

_print_lock = threading.Lock()


def log(*a):
    with _print_lock:
        print(*a, flush=True)


def prompt(name: str, **values) -> str:
    """Read prompts/<name> and fill its {{KEY}} placeholders."""
    text = (PROMPTS / name).read_text()
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
