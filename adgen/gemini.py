"""Gemini text, image and Veo video generation. Standard library only.

Uses GEMINI_API_KEY (and optional model overrides) from .env (same loader as tts.py).

    from adgen.gemini import text, image, video
    plan = text("Plan an ad ...", images=["refs/front.jpg"], json_out=True)          # text/vision -> dict
    image("Aanya holding the bar ...", "shot1.png", refs=["refs/front.jpg"])        # text (+refs) -> image
    video("She smiles at the camera. No dialogue.", "shot1.mp4", start="shot1.png")  # image -> video
"""

import base64
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import CFG

API = "https://generativelanguage.googleapis.com/v1beta"
TEXT_MODEL = CFG.get("GEMINI_TEXT_MODEL", "gemini-3.1-pro-preview")
CHECK_MODEL = CFG.get("GEMINI_CHECK_MODEL", "gemini-3-flash-preview")
IMAGE_MODEL = CFG.get("GEMINI_IMAGE_MODEL", "gemini-3-pro-image")
VIDEO_MODEL = CFG.get("VEO_MODEL", "veo-3.1-fast-generate-preview")
VIDEO_RESOLUTION = CFG.get("VEO_RESOLUTION", "720p")
VEO_SECONDS = (4, 6, 8)
RATIOS = ("9:16", "16:9")


class GeminiError(Exception):
    pass


def _key() -> str:
    key = CFG.get("GEMINI_API_KEY")
    if not key:
        raise GeminiError("GEMINI_API_KEY is not set in .env")
    return key


def _call(url: str, body: dict = None, timeout: int = 300, retries: int = 4) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"x-goog-api-key": _key(), "Content-Type": "application/json"}
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data, headers, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:500]
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                wait = 15 * (attempt + 1)
                print(f"  gemini HTTP {e.code}, retrying in {wait}s ...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise GeminiError(f"HTTP {e.code}: {msg}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                time.sleep(10)
                continue
            raise GeminiError(f"could not reach Gemini: {e}") from None


def _inline(path) -> dict:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return {"mimeType": mime, "data": base64.b64encode(Path(path).read_bytes()).decode()}


def text(prompt: str, images=(), system: str = None, json_out: bool = False,
         model: str = TEXT_MODEL, temperature: float = 0.7):
    """Text (optionally with images) -> str, or a parsed dict/list when json_out=True."""
    parts = [{"inlineData": _inline(i)} for i in images] + [{"text": prompt}]
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_out:
        body["generationConfig"]["responseMimeType"] = "application/json"
    resp = _call(f"{API}/models/{model}:generateContent", body)
    out = "".join(p.get("text", "") for c in resp.get("candidates", [])
                  for p in c.get("content", {}).get("parts", []) if not p.get("thought"))
    if not out:
        raise GeminiError(f"no text returned ({resp.get('promptFeedback') or resp.get('candidates')})")
    if not json_out:
        return out.strip()
    out = out.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise GeminiError(f"model did not return valid JSON: {e}\n{out[:500]}") from None


def image(prompt: str, out, refs=(), aspect: str = "9:16", model: str = IMAGE_MODEL) -> Path:
    """Generate one image. `refs` are reference images (product photos, creator) to keep consistent."""
    parts = [{"inlineData": _inline(r)} for r in refs] + [{"text": prompt}]
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": aspect}},
    }
    resp = _call(f"{API}/models/{model}:generateContent", body)
    for cand in resp.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            if "inlineData" in part:
                out = Path(out)
                ext = mimetypes.guess_extension(part["inlineData"].get("mimeType", "image/png")) or ".png"
                out = out.with_suffix(".jpg" if ext in (".jpe", ".jpeg") else ext)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(base64.b64decode(part["inlineData"]["data"]))
                return out
    reason = resp.get("promptFeedback") or [c.get("finishReason") for c in resp.get("candidates", [])]
    raise GeminiError(f"no image returned ({reason})")


def video(prompt: str, out, start=None, aspect: str = "9:16", seconds: int = 8,
          resolution: str = VIDEO_RESOLUTION, negative: str = None,
          model: str = VIDEO_MODEL, poll: int = 10) -> Path:
    """Generate one Veo clip, optionally animating `start` (an image file). Blocks until done."""
    if seconds not in VEO_SECONDS:
        raise GeminiError(f"Veo duration must be one of {VEO_SECONDS}, got {seconds}")
    instance = {"prompt": prompt}
    params = {"aspectRatio": aspect, "durationSeconds": seconds, "resolution": resolution}
    if start:
        d = _inline(start)
        instance["image"] = {"bytesBase64Encoded": d["data"], "mimeType": d["mimeType"]}
        params["personGeneration"] = "allow_adult"
    if negative:
        params["negativePrompt"] = negative
    op = _call(f"{API}/models/{model}:predictLongRunning", {"instances": [instance], "parameters": params})
    name = op["name"]
    began = time.time()
    while not op.get("done"):
        time.sleep(poll)
        op = _call(f"{API}/{name}")
        print(f"  veo {Path(out).name}: {time.time() - began:.0f}s ...", file=sys.stderr)
    if "error" in op:
        raise GeminiError(f"Veo failed: {op['error']}")
    resp = op.get("response", {}).get("generateVideoResponse", {})
    samples = resp.get("generatedSamples") or []
    if not samples:
        raise GeminiError(f"Veo returned no video (likely a safety filter): {json.dumps(resp)[:400]}")
    uri = samples[0]["video"]["uri"]
    req = urllib.request.Request(uri, headers={"x-goog-api-key": _key()})
    out = Path(out).with_suffix(".mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=300) as r:
        out.write_bytes(r.read())
    return out
