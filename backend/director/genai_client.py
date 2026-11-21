"""
Centralized Google GenAI Client & Structured Output Helper (Owned by P1).

Feature & Use Cases:
- Wraps `google-genai` async calls with centralized API key loading from
  `backend/config.py`, Pydantic structured JSON output enforcement, and
  automatic INFO logging via `backend/logger.py` (stripping inline media bytes).
- Provides safe multimodal `Part` builders for images, audio, and `.webm`/`.mp4`
  video clips used by Hydrogen and Neon.
"""

import mimetypes
from pathlib import Path
from typing import Any, Type, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from backend import config
from backend.logger import log_genai_call

T = TypeVar("T", bound=BaseModel)

_CLIENT: genai.Client | None = None


def get_genai_client() -> genai.Client:
    """Return a cached `google.genai.Client` initialized from `config.GEMINI_API_KEY`."""
    global _CLIENT
    if _CLIENT is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set in environment or .env")
        _CLIENT = genai.Client(api_key=config.GEMINI_API_KEY)
    return _CLIENT


def part_from_bytes(data: bytes, mime_type: str) -> types.Part:
    """Build a GenAI multimodal Part from raw bytes and MIME type."""
    return types.Part.from_bytes(data=data, mime_type=mime_type)


def part_from_path(file_path: str | Path) -> types.Part:
    """Load a local image/audio/video file into a `types.Part` with inferred MIME type."""
    path = Path(file_path)
    mime_type, _ = mimetypes.guess_type(str(path))
    if not mime_type:
        suffix = path.suffix.lower()
        mime_type = {
            ".webm": "video/webm",
            ".mp4": "video/mp4",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
        }.get(suffix, "application/octet-stream")
    return types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type)


async def generate_structured(
    model: str,
    system_instruction: str,
    contents: list[Any],
    response_schema: Type[T],
    temperature: float = 0.2,
) -> T:
    """Call Gemini asynchronously with strict Pydantic JSON schema output and log the call."""
    client = get_genai_client()
    gen_config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=response_schema,
        temperature=temperature,
    )
    response = await client.aio.models.generate_content(
        model=model,
        contents=contents,
        config=gen_config,
    )
    raw_text = response.text or "{}"
    parsed = response_schema.model_validate_json(raw_text)
    log_genai_call(
        model=model,
        prompt={"system": system_instruction, "contents": contents},
        config={"response_schema": response_schema.__name__, "temperature": temperature},
        output=parsed,
    )
    return parsed


async def generate_text(
    model: str,
    system_instruction: str,
    contents: list[Any],
    temperature: float = 0.3,
) -> str:
    """Call Gemini asynchronously for concise plain-text output and log the call."""
    client = get_genai_client()
    gen_config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=temperature,
    )
    response = await client.aio.models.generate_content(
        model=model,
        contents=contents,
        config=gen_config,
    )
    text = (response.text or "").strip()
    log_genai_call(
        model=model,
        prompt={"system": system_instruction, "contents": contents},
        config={"temperature": temperature},
        output=text,
    )
    return text
