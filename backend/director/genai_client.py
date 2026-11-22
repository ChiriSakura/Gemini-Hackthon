"""
Centralized Google GenAI Client, Decisions API (v1beta), and Structured Output Helper (Owned by P1).

Feature & Use Cases:
- Wraps `google-genai` async calls for Gemini 4 Neon & Hydrogen (`generate_structured`,
  `generate_text`) AND the v1beta Decisions API (`run_decisions_noul`, `run_decision_choice`,
  `run_decision_score` using `gemini-decision-1` ~0.64s per call).
- Automatically logs every call at INFO level via `backend/logger.py`, stripping
  inline binary/base64 payloads.
"""

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import Any, Mapping, Type, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from backend import config
from backend.logger import log_genai_call

T = TypeVar("T", bound=BaseModel)

_CLIENT: genai.Client | None = None
_BETA_CLIENT: genai.Client | None = None


class DirectorModelError(RuntimeError):
    """Structured retryable error raised when a director/judge GenAI call times out or fails after retries."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "director_error",
        status_code: int = 503,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


def get_genai_client() -> genai.Client:
    """Return a cached standard `google.genai.Client` initialized from `config.GEMINI_API_KEY`."""
    global _CLIENT
    if _CLIENT is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set in environment or .env")
        _CLIENT = genai.Client(api_key=config.GEMINI_API_KEY)
    return _CLIENT


def get_beta_client() -> genai.Client:
    """Return a cached v1beta `google.genai.Client` required for Decisions API (`gemini-decision-1`)."""
    global _BETA_CLIENT
    if _BETA_CLIENT is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set in environment or .env")
        _BETA_CLIENT = genai.Client(
            api_key=config.GEMINI_API_KEY,
            http_options={"api_version": "v1beta"},
        )
    return _BETA_CLIENT


def _infer_mime_type(file_path: str | Path) -> str:
    """Infer MIME type for common image/audio/video stage assets."""
    path = Path(file_path)
    mime_type, _ = mimetypes.guess_type(str(path))
    if mime_type:
        return mime_type
    return {
        ".webm": "video/webm",
        ".mp4": "video/mp4",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
    }.get(path.suffix.lower(), "application/octet-stream")


def part_from_bytes(data: bytes, mime_type: str) -> types.Part:
    """Build a GenAI multimodal Part from raw bytes and MIME type."""
    return types.Part.from_bytes(data=data, mime_type=mime_type)


def part_from_path(file_path: str | Path) -> types.Part:
    """Load a local image/audio/video file into a `types.Part` with inferred MIME type."""
    path = Path(file_path)
    return types.Part.from_bytes(data=path.read_bytes(), mime_type=_infer_mime_type(path))


def build_decision_input(text: str, media_paths: list[Path] | None = None) -> list[dict[str, Any]]:
    """Build `DecisionInputContentParam` list supporting text + image/audio/video inputs."""
    contents: list[dict[str, Any]] = []
    if text:
        contents.append({"type": "text", "text": text})
    for path in media_paths or []:
        mime = _infer_mime_type(path)
        b64_data = base64.b64encode(path.read_bytes()).decode("ascii")
        if mime.startswith("image/"):
            contents.append({"type": "image", "data": b64_data, "mime_type": mime})
        elif mime.startswith("video/"):
            contents.append({"type": "video", "data": b64_data, "mime_type": mime})
        elif mime.startswith("audio/"):
            contents.append({"type": "audio", "data": b64_data, "mime_type": mime})
    return contents or [{"type": "text", "text": "No input provided."}]


async def create_decision(
    input_contents: str | list[dict[str, Any]],
    questions: Mapping[str, dict[str, Any]],
    timeout_sec: float = 20.0,
) -> Any:
    """Call `v1beta` `decisions.create` asynchronously with timeout and log sanitized trace."""
    beta = get_beta_client()

    def _call() -> Any:
        return beta.decisions.create(
            model=config.MODEL_DECISION,
            input=input_contents,
            questions=dict(questions),
        )

    res = await asyncio.wait_for(asyncio.to_thread(_call), timeout=timeout_sec)
    log_genai_call(
        model=config.MODEL_DECISION,
        prompt={"input": input_contents, "questions": dict(questions)},
        config={"api_version": "v1beta", "timeout_sec": timeout_sec},
        output=res,
    )
    return res


async def generate_structured(
    model: str,
    system_instruction: str,
    contents: list[Any],
    response_schema: Type[T],
    temperature: float = 0.2,
    thinking_level: str | None = "LOW",
    timeout_sec: float | None = None,
    max_retries: int | None = None,
) -> T:
    """Call Gemini asynchronously with strict Pydantic JSON schema output, per-attempt timeout, 1 retry, and logging."""
    client = get_genai_client()
    eff_timeout = config.DIRECTOR_TIMEOUT_SEC if timeout_sec is None else timeout_sec
    eff_retries = config.DIRECTOR_MAX_RETRIES if max_retries is None else max(0, max_retries)
    thinking_cfg = types.ThinkingConfig(thinking_level=thinking_level) if thinking_level else None
    gen_config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=response_schema,
        temperature=temperature,
        thinking_config=thinking_cfg,
    )
    total_attempts = 1 + eff_retries
    last_exc: Exception | None = None
    timed_out = False
    for attempt in range(1, total_attempts + 1):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=gen_config,
                ),
                timeout=eff_timeout,
            )
            raw_text = (response.text or "").strip()
            if not raw_text:
                raise ValueError(f"Empty structured response from model={model}")
            parsed = response_schema.model_validate_json(raw_text)
            log_genai_call(
                model=model,
                prompt={"system": system_instruction, "contents": contents},
                config={
                    "response_schema": response_schema.__name__,
                    "temperature": temperature,
                    "timeout_sec": eff_timeout,
                    "attempt": attempt,
                },
                output=parsed,
            )
            return parsed
        except asyncio.TimeoutError as exc:
            timed_out = True
            last_exc = exc
            log_genai_call(
                model=model,
                prompt={"system": system_instruction[:160]},
                config={"response_schema": response_schema.__name__, "timeout_sec": eff_timeout, "attempt": attempt},
                output={"error": "timeout", "will_retry": attempt < total_attempts},
            )
        except Exception as exc:
            timed_out = False
            last_exc = exc
            log_genai_call(
                model=model,
                prompt={"system": system_instruction[:160]},
                config={"response_schema": response_schema.__name__, "timeout_sec": eff_timeout, "attempt": attempt},
                output={"error": str(exc)[:240], "will_retry": attempt < total_attempts},
            )
        if attempt < total_attempts:
            await asyncio.sleep(0.8)

    if timed_out:
        raise DirectorModelError(
            f"Director model ({model}) timed out after {total_attempts} attempts ({int(eff_timeout)}s each). Please retry.",
            code="director_timeout",
            status_code=504,
            retryable=True,
        ) from last_exc
    detail = str(last_exc)[:180] if last_exc else "unknown error"
    raise DirectorModelError(
        f"Director model ({model}) failed after {total_attempts} attempts: {detail}. Please retry.",
        code="director_error",
        status_code=503,
        retryable=True,
    ) from last_exc


async def generate_text(
    model: str,
    system_instruction: str,
    contents: list[Any],
    temperature: float = 0.3,
    timeout_sec: float | None = None,
    max_retries: int | None = None,
) -> str:
    """Call Gemini asynchronously for concise plain-text output with per-attempt timeout, retry, and logging."""
    client = get_genai_client()
    eff_timeout = config.DIRECTOR_TIMEOUT_SEC if timeout_sec is None else timeout_sec
    eff_retries = config.DIRECTOR_MAX_RETRIES if max_retries is None else max(0, max_retries)
    gen_config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=temperature,
    )
    total_attempts = 1 + eff_retries
    last_exc: Exception | None = None
    timed_out = False
    for attempt in range(1, total_attempts + 1):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=gen_config,
                ),
                timeout=eff_timeout,
            )
            text = (response.text or "").strip()
            log_genai_call(
                model=model,
                prompt={"system": system_instruction, "contents": contents},
                config={"temperature": temperature, "timeout_sec": eff_timeout, "attempt": attempt},
                output=text,
            )
            return text
        except asyncio.TimeoutError as exc:
            timed_out = True
            last_exc = exc
        except Exception as exc:
            timed_out = False
            last_exc = exc
        if attempt < total_attempts:
            await asyncio.sleep(0.8)

    if timed_out:
        raise DirectorModelError(
            f"Director model ({model}) timed out after {total_attempts} attempts ({int(eff_timeout)}s each). Please retry.",
            code="director_timeout",
            status_code=504,
            retryable=True,
        ) from last_exc
    detail = str(last_exc)[:180] if last_exc else "unknown error"
    raise DirectorModelError(
        f"Director model ({model}) failed after {total_attempts} attempts: {detail}. Please retry.",
        code="director_error",
        status_code=503,
        retryable=True,
    ) from last_exc
