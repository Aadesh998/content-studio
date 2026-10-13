"""Text-to-speech for the whole project. Standard library only.

Engines (cloud only, no local model):
  eleven  ElevenLabs API (ELEVEN_API_KEY)   <- default
  sarvam  Sarvam API (SARVAM_API_KEY)       <- fallback

speak() tries TTS_ENGINE first, then each engine in TTS_FALLBACK until one works.
Default voice is TTS_VOICE (ElevenLabs Monika; Sarvam uses SARVAM_VOICE when it falls back).
Settings come from .env next to this file; real environment variables override it.

From code:
    from adgen.tts import speak
    speak("Hi, I am Aanya!", out="voice.wav")                       # ElevenLabs, Sarvam if it fails
    speak("Hello!", engine="sarvam", voice="neha", out="voice.wav")
    speak("Hello!", engine="eleven", fallback=[], out="voice.wav")  # no fallback

From the shell:
    ./studio voice "Hi, I am Aanya!" -o voice.wav -p
    ./studio voice "नमस्ते!" -e sarvam -v neha -l hi-IN --no-fallback
    ./studio voice --voices
"""

import base64
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import wave
from pathlib import Path

from .config import CFG

ENGINES = ("eleven", "sarvam")
# Sarvam bulbul:v3 speakers
SARVAM_VOICES = (
    # female
    "simran", "neha", "priya", "kavya", "pooja", "ritu", "ishita", "shreya", "roopa", "tanya",
    "shruti", "suhani", "kavitha", "rupali",
    # male
    "aditya", "ashutosh", "rahul", "rohan", "amit", "dev", "ratan", "varun", "manan", "sumit", "kabir",
    "aayan", "shubh", "advait", "anand", "tarun", "sunny", "mani", "gokul", "vijay", "mohit", "rehan", "soham",
)
PLAYERS = [["pw-play"], ["paplay"], ["aplay", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]]


class TTSError(Exception):
    pass


def _request(url: str, headers: dict, body: dict = None, timeout: int = 120):
    data = json.dumps(body).encode() if body is not None else None
    headers = {**headers, "Content-Type": "application/json"} if data else headers
    req = urllib.request.Request(url, data, headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), r.headers
    except urllib.error.HTTPError as e:
        raise TTSError(f"{url} -> HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from None
    except urllib.error.URLError as e:
        raise TTSError(f"could not reach {url}: {e.reason}") from None


def _write_pcm_wav(path: Path, pcm: bytes, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


# --- Sarvam ---------------------------------------------------------------------

def _sarvam(text: str, voice: str, out: Path, language: str = None) -> Path:
    key = CFG.get("SARVAM_API_KEY")
    if not key:
        raise TTSError("SARVAM_API_KEY is not set in .env")
    speaker = (voice or "").lower()
    if speaker not in SARVAM_VOICES:  # e.g. an ElevenLabs-only name or voice id
        speaker = CFG.get("SARVAM_VOICE") or "simran"
    body = {
        "text": text,
        "target_language_code": language or CFG.get("SARVAM_LANGUAGE", "en-IN"),
        "speaker": speaker,
        "model": CFG.get("SARVAM_MODEL", "bulbul:v3"),
        "speech_sample_rate": 24000,
    }
    data, _ = _request("https://api.sarvam.ai/text-to-speech", {"api-subscription-key": key}, body)
    audios = json.loads(data).get("audios") or []
    if not audios:
        raise TTSError(f"Sarvam returned no audio: {data[:200]!r}")
    # Long text comes back as several WAV chunks; join their PCM into one file.
    frames, rate = [], 24000
    for chunk in audios:
        tmp = out.with_suffix(".part.wav")
        tmp.write_bytes(base64.b64decode(chunk))
        with wave.open(str(tmp), "rb") as w:
            rate = w.getframerate()
            frames.append(w.readframes(w.getnframes()))
        tmp.unlink()
    out = out.with_suffix(".wav")
    _write_pcm_wav(out, b"".join(frames), rate)
    return out


# --- ElevenLabs -----------------------------------------------------------------

_eleven_voices = None


def eleven_voices() -> dict:
    """{lowercase first name: voice_id} for every voice on the ElevenLabs account."""
    global _eleven_voices
    if _eleven_voices is None:
        data, _ = _request("https://api.elevenlabs.io/v1/voices", {"xi-api-key": CFG.get("ELEVEN_API_KEY", "")})
        _eleven_voices = {}
        for v in json.loads(data).get("voices", []):
            _eleven_voices.setdefault(re.split(r"[\s\-|–]", v["name"].strip())[0].lower(), v["voice_id"])
    return _eleven_voices


def _eleven_voice_id(voice: str) -> str:
    if voice and re.fullmatch(r"[A-Za-z0-9]{20}", voice):
        return voice  # raw voice id
    if voice:
        found = eleven_voices().get(voice.lower())
        if found:
            return found
    return CFG.get("ELEVEN_VOICE_ID") or eleven_voices().get("jessica") or next(iter(eleven_voices().values()))


def _eleven(text: str, voice: str, out: Path, language: str = None) -> Path:  # multilingual model reads the script
    key = CFG.get("ELEVEN_API_KEY")
    if not key:
        raise TTSError("ELEVEN_API_KEY is not set in .env")
    voice_id = _eleven_voice_id(voice)
    body = {"text": text, "model_id": CFG.get("ELEVEN_MODEL", "eleven_multilingual_v2")}
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format="
    if out.suffix.lower() != ".mp3":
        try:  # raw PCM -> WAV, same format as Sarvam
            data, _ = _request(url + "pcm_24000", {"xi-api-key": key}, body)
            out = out.with_suffix(".wav")
            _write_pcm_wav(out, data, 24000)
            return out
        except TTSError as e:
            if "HTTP 4" not in str(e) or "voice" in str(e).lower():
                raise
            # Plan doesn't allow PCM output; fall back to MP3.
    data, _ = _request(url + "mp3_44100_128", {"xi-api-key": key}, body)
    out = out.with_suffix(".mp3")
    out.write_bytes(data)
    return out


# --- Public API -----------------------------------------------------------------

_ENGINE_FUNCS = {"eleven": _eleven, "sarvam": _sarvam}


def _engine_list(value) -> list:
    if isinstance(value, str):
        value = value.split(",")
    return [v.strip().lower() for v in value or [] if v.strip()]


def speak(text: str, engine: str = None, voice: str = None, out="output.wav", fallback=None,
          language: str = None) -> Path:
    """Turn text into audio and return the saved file path.

    Tries `engine` (default TTS_ENGINE, i.e. eleven), then each engine in `fallback`
    (default TTS_FALLBACK, i.e. sarvam). Pass fallback=[] to use only `engine`.
    `language` is a BCP-47 code such as "hi-IN" or "en-IN" (Sarvam needs it; default SARVAM_LANGUAGE).
    The extension may change: WAV normally, MP3 when ElevenLabs can't return PCM on your plan.
    """
    engine = (engine or CFG.get("TTS_ENGINE") or "eleven").lower()
    fallback = _engine_list(CFG.get("TTS_FALLBACK", "sarvam") if fallback is None else fallback)
    voice = voice or CFG.get("TTS_VOICE") or "simran"
    order = list(dict.fromkeys([engine, *fallback]))
    unknown = [e for e in order if e not in ENGINES]
    if unknown:
        raise TTSError(f"unknown engine {unknown[0]!r}; use one of: {', '.join(ENGINES)}")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    for name in order:
        try:
            path = _ENGINE_FUNCS[name](text, voice, out, language)
            if errors:
                print(f"tts: used fallback engine {name!r} ({errors[-1]})", file=sys.stderr)
            return path
        except TTSError as e:
            errors.append(f"{name}: {e}")
    raise TTSError("TTS failed:\n  " + "\n  ".join(errors))


def play(path) -> None:
    for cmd in PLAYERS:
        if shutil.which(cmd[0]):
            subprocess.run([*cmd, str(path)], check=False)
            return
    print("No audio player found (install pipewire, pulseaudio-utils, alsa-utils, or ffmpeg).")


def register(sub):
    p = sub.add_parser("voice", help="Text to speech only (ElevenLabs, Sarvam fallback)")
    p.add_argument("text", nargs="?", help="Text to speak ('-' reads stdin)")
    p.add_argument("-e", "--engine", choices=ENGINES, help="Default: TTS_ENGINE in .env (eleven)")
    p.add_argument("--no-fallback", action="store_true", help="Don't fall back to TTS_FALLBACK engines")
    p.add_argument("-v", "--voice", help="Default: TTS_VOICE. Sarvam: neha, priya, ... | "
                                         "ElevenLabs: simran, monika, jessica, ... or a voice id")
    p.add_argument("-l", "--language", help="e.g. hi-IN, en-IN (default SARVAM_LANGUAGE)")
    p.add_argument("-o", "--output", default="output.wav")
    p.add_argument("-p", "--play", action="store_true", help="Play the audio after saving")
    p.add_argument("--voices", action="store_true", help="List voices for each engine")
    p.set_defaults(func=cmd_voice)


def cmd_voice(args):
    if args.voices:
        print("sarvam:", ", ".join(SARVAM_VOICES))
        print("eleven:", ", ".join(sorted(eleven_voices())))
        return
    text = sys.stdin.read().strip() if args.text == "-" else args.text
    if not text:
        sys.exit("text is required")
    path = speak(text, args.engine, args.voice, args.output,
                 fallback=[] if args.no_fallback else None, language=args.language)
    print(f"Saved {path}")
    if args.play:
        play(path)
