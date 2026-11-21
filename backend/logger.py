"""
Structured Function & GenAI Call Logger for StageState.

Feature & Use Cases:
- Logs every feature function call at INFO level with sanitized parameters.
- Logs every Gemini/GenAI API call at INFO level including model name, prompt,
  config, and response summary while automatically stripping inline binary or
  base64 data payloads so logs remain readable and lightweight.
"""

import functools
import inspect
import json
import logging
from typing import Any, Callable, TypeVar

from backend.config import LOG_LEVEL

F = TypeVar("F", bound=Callable[..., Any])

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("stagestate")


def strip_inline_data(obj: Any, max_str_len: int = 400) -> Any:
    """Recursively strip inline binary/base64 payloads and truncate huge blobs."""
    if isinstance(obj, bytes):
        return f"<bytes len={len(obj)}>"
    if isinstance(obj, bytearray):
        return f"<bytearray len={len(obj)}>"
    if isinstance(obj, str):
        if obj.startswith("data:image/") or obj.startswith("data:video/") or obj.startswith("data:audio/"):
            return f"<data_uri len={len(obj)}>"
        if len(obj) > max_str_len:
            return obj[:max_str_len] + f"... <truncated total={len(obj)}>"
        return obj
    if isinstance(obj, dict):
        sanitized = {}
        for k, v in obj.items():
            if k in {"inline_data", "data", "bytes", "image_bytes", "audio_bytes", "video_bytes"} and isinstance(v, (str, bytes)):
                sanitized[k] = f"<stripped_inline_data len={len(v)}>"
            else:
                sanitized[k] = strip_inline_data(v, max_str_len=max_str_len)
        return sanitized
    if isinstance(obj, (list, tuple)):
        return [strip_inline_data(item, max_str_len=max_str_len) for item in obj]
    if hasattr(obj, "model_dump"):
        return strip_inline_data(obj.model_dump(), max_str_len=max_str_len)
    return repr(obj) if not isinstance(obj, (int, float, bool, type(None))) else obj


def log_function_call(func: F) -> F:
    """Decorator to log function entry, sanitized parameters, and completion at INFO level."""
    is_async = inspect.iscoroutinefunction(func)

    @functools.wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
        params = strip_inline_data(dict(bound.arguments))
        logger.info("CALL %s params=%s", func.__qualname__, json.dumps(params, ensure_ascii=False))
        result = await func(*args, **kwargs)
        logger.info("DONE %s result=%s", func.__qualname__, json.dumps(strip_inline_data(result), ensure_ascii=False))
        return result

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
        params = strip_inline_data(dict(bound.arguments))
        logger.info("CALL %s params=%s", func.__qualname__, json.dumps(params, ensure_ascii=False))
        result = func(*args, **kwargs)
        logger.info("DONE %s result=%s", func.__qualname__, json.dumps(strip_inline_data(result), ensure_ascii=False))
        return result

    return (async_wrapper if is_async else sync_wrapper)  # type: ignore[return-value]


def log_genai_call(model: str, prompt: Any, config: Any, output: Any) -> None:
    """Log a GenAI API invocation with model, prompt, config, and output, stripping inline data."""
    payload = {
        "model": model,
        "prompt": strip_inline_data(prompt, max_str_len=600),
        "config": strip_inline_data(config),
        "output": strip_inline_data(output, max_str_len=600),
    }
    logger.info("GENAI_CALL %s", json.dumps(payload, ensure_ascii=False))
