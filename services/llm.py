from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import re
from typing import Any
from uuid import uuid4

import httpx

from services.config import get_settings
from services.llm_trace import record_llm_event
from services.runtime_credentials import get_runtime_api_key

JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json_text(text: str) -> str:
    text = text.strip()
    if not text:
        raise ValueError("Empty LLM response")

    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    fenced_match = JSON_FENCE_PATTERN.search(text)
    if fenced_match:
        candidate = fenced_match.group(1).strip()
        json.loads(candidate)
        return candidate

    first_object = text.find("{")
    last_object = text.rfind("}")
    if first_object != -1 and last_object > first_object:
        candidate = text[first_object : last_object + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    first_array = text.find("[")
    last_array = text.rfind("]")
    if first_array != -1 and last_array > first_array:
        candidate = text[first_array : last_array + 1]
        json.loads(candidate)
        return candidate

    raise ValueError("Could not extract valid JSON from LLM response")


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)
    return str(content)


def _format_llm_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        body = exc.response.text.strip()
        if len(body) > 400:
            body = f"{body[:400]}..."
        return f"HTTP {status_code}: {body or exc.response.reason_phrase}"

    if isinstance(exc, httpx.TimeoutException):
        return f"{type(exc).__name__}: request timed out"

    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _call_llm_internal(
    prompt: str,
    system_prompt: str | None = None,
    trace_name: str | None = None,
) -> tuple[str, str]:
    settings = get_settings()
    api_key = get_runtime_api_key("planning") or settings.planning_api_key
    if not api_key:
        raise RuntimeError("PLANNING_API_KEY is not configured in the current session or environment")

    call_id = uuid4().hex
    url = f"{settings.planning_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    trace_label = trace_name or "llm_call"

    record_llm_event(
        {
            "call_id": call_id,
            "trace_name": trace_label,
            "kind": "request",
            "timestamp": _utc_now_iso(),
            "base_url": settings.planning_base_url,
            "model": settings.planning_model,
            "system_prompt": system_prompt or "",
            "prompt": prompt,
        }
    )

    last_error_message = ""
    for attempt in range(1, settings.planning_max_retries + 1):
        payload: dict[str, Any] = {
            "model": settings.planning_model,
            "messages": messages,
            "temperature": settings.planning_temperature,
        }
        if attempt == 1:
            payload["response_format"] = {"type": "json_object"}

        record_llm_event(
            {
                "call_id": call_id,
                "trace_name": trace_label,
                "kind": "attempt",
                "timestamp": _utc_now_iso(),
                "attempt": attempt,
                "request_payload": {
                    "model": payload["model"],
                    "temperature": payload["temperature"],
                    "response_format": payload.get("response_format"),
                },
            }
        )

        try:
            async with httpx.AsyncClient(timeout=settings.planning_timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            raw_text = _message_content_to_text(content)
            record_llm_event(
                {
                    "call_id": call_id,
                    "trace_name": trace_label,
                    "kind": "response",
                    "timestamp": _utc_now_iso(),
                    "attempt": attempt,
                    "raw_text": raw_text,
                }
            )
            return call_id, raw_text
        except httpx.HTTPStatusError as exc:
            last_error_message = _format_llm_error(exc)
            record_llm_event(
                {
                    "call_id": call_id,
                    "trace_name": trace_label,
                    "kind": "attempt_error",
                    "timestamp": _utc_now_iso(),
                    "attempt": attempt,
                    "error": last_error_message,
                }
            )
            if attempt == 1 and exc.response.status_code == 400:
                continue
        except Exception as exc:  # pragma: no cover - runtime integration path
            last_error_message = _format_llm_error(exc)
            record_llm_event(
                {
                    "call_id": call_id,
                    "trace_name": trace_label,
                    "kind": "attempt_error",
                    "timestamp": _utc_now_iso(),
                    "attempt": attempt,
                    "error": last_error_message,
                }
            )

        await asyncio.sleep(min(attempt, 3))

    final_error = (
        "LLM request failed after retries: "
        f"{last_error_message or 'unknown error'} "
        f"(base_url={settings.planning_base_url}, model={settings.planning_model})"
    )
    record_llm_event(
        {
            "call_id": call_id,
            "trace_name": trace_label,
            "kind": "final_error",
            "timestamp": _utc_now_iso(),
            "error": final_error,
        }
    )
    raise RuntimeError(final_error)


async def call_llm(prompt: str, system_prompt: str | None = None, trace_name: str | None = None) -> str:
    _, content = await _call_llm_internal(prompt=prompt, system_prompt=system_prompt, trace_name=trace_name)
    return content


async def call_llm_json(prompt: str, system_prompt: str | None = None, trace_name: str | None = None) -> Any:
    call_id, content = await _call_llm_internal(prompt=prompt, system_prompt=system_prompt, trace_name=trace_name)
    parsed = json.loads(_extract_json_text(content))
    record_llm_event(
        {
            "call_id": call_id,
            "trace_name": trace_name or "llm_call",
            "kind": "parsed_json",
            "timestamp": _utc_now_iso(),
            "parsed_json": parsed,
        }
    )
    return parsed
