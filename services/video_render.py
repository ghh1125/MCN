from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from services.config import get_settings, get_video_output_dir
from services.runtime_credentials import get_runtime_api_key


def _normalize_status(value: str | None) -> str:
    mapping = {
        "done": "done",
        "completed": "done",
        "success": "done",
        "succeeded": "done",
        "successed": "done",
        "pending": "processing",
        "queued": "processing",
        "running": "processing",
        "processing": "processing",
        "submitted": "processing",
        "validating": "processing",
        "failed": "failed",
        "error": "failed",
        "canceled": "failed",
        "cancelled": "failed",
    }
    return mapping.get((value or "").lower(), "processing")


def _mock_video_result(creator_id: str) -> dict[str, Any]:
    asset_id = uuid4().hex
    return {
        "job_id": f"mock-video-{asset_id}",
        "status": "done",
        "video_url": f"https://example.com/videos/{creator_id}/{asset_id}.mp4",
        "raw": {"mock": True},
    }


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_dashscope_video_url(data: dict[str, Any]) -> str | None:
    output = data.get("output")
    if not isinstance(output, dict):
        return None

    direct_url = output.get("video_url") or output.get("videoUrl")
    if isinstance(direct_url, str) and direct_url.strip():
        return direct_url

    results = output.get("results")
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict):
                url = item.get("video_url") or item.get("url") or item.get("videoUrl")
                if isinstance(url, str) and url.strip():
                    return url

    result_video = output.get("result_video")
    if isinstance(result_video, dict):
        url = result_video.get("url") or result_video.get("video_url")
        if isinstance(url, str) and url.strip():
            return url

    return None


def _truncate_text(value: str, limit: int = 600) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


DASHSCOPE_MIN_DURATION_SECONDS = 2
DASHSCOPE_MAX_DURATION_SECONDS = 15


def _normalize_dashscope_duration(value: Any, fallback: int) -> int:
    try:
        duration = int(value)
    except (TypeError, ValueError):
        duration = int(fallback)
    return max(DASHSCOPE_MIN_DURATION_SECONDS, min(DASHSCOPE_MAX_DURATION_SECONDS, duration))


def _extract_error_message(data: dict[str, Any]) -> str | None:
    candidates: list[str] = []

    def _visit(node: Any) -> None:
        if isinstance(node, dict):
            for key in (
                "message",
                "error",
                "error_message",
                "error_msg",
                "msg",
                "code",
                "task_status_msg",
                "detail",
            ):
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())
            for key in ("errors", "error_details", "details"):
                value = node.get(key)
                if isinstance(value, list):
                    for item in value:
                        _visit(item)
                elif isinstance(value, dict):
                    _visit(value)
        elif isinstance(node, list):
            for item in node:
                _visit(item)

    _visit(data)
    output = data.get("output")
    if isinstance(output, dict):
        task_status = output.get("task_status") or output.get("taskStatus")
        if isinstance(task_status, str) and task_status.lower() in {"failed", "error", "canceled", "cancelled"}:
            candidates.append(f"task_status={task_status}")

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        lowered = item.lower()
        if lowered in {"failed", "error"}:
            continue
        if lowered not in seen:
            seen.add(lowered)
            deduped.append(item)

    if deduped:
        return _truncate_text(" | ".join(deduped))
    return None


def _normalize_dashscope_submit(data: dict[str, Any]) -> dict[str, Any]:
    output = data.get("output") if isinstance(data.get("output"), dict) else {}
    task_id = output.get("task_id") or output.get("taskId") or data.get("request_id")
    task_status = output.get("task_status") or output.get("taskStatus") or data.get("status")
    return {
        "job_id": task_id,
        "status": _normalize_status(task_status),
        "video_url": _extract_dashscope_video_url(data),
        "error": _extract_error_message(data),
        "raw": data,
    }


def _normalize_dashscope_poll(data: dict[str, Any], job_id: str) -> dict[str, Any]:
    output = data.get("output") if isinstance(data.get("output"), dict) else {}
    task_status = output.get("task_status") or output.get("taskStatus") or data.get("status")
    return {
        "job_id": job_id,
        "status": _normalize_status(task_status),
        "video_url": _extract_dashscope_video_url(data),
        "error": _extract_error_message(data),
        "raw": data,
    }


def _build_generic_headers() -> dict[str, str]:
    settings = get_settings()
    headers = {"Content-Type": "application/json"}
    api_key = get_runtime_api_key("video") or settings.video_api_key
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _build_dashscope_headers() -> dict[str, str]:
    headers = _build_generic_headers()
    headers["X-DashScope-Async"] = "enable"
    return headers


def _build_generic_payload(script: dict[str, Any], creator_id: str, platform: str) -> dict[str, Any]:
    return {
        "creator_id": creator_id,
        "platform": platform,
        "script": script,
        "title": script.get("title", ""),
        "prompt": script.get("text_to_video_prompt", ""),
        "script_text": script.get("creative_script_text", ""),
        "duration_seconds": script.get("target_duration_seconds"),
        "shot_outline": script.get("shot_outline", []),
    }


def _build_dashscope_payload(script: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    model_name = settings.video_model.strip()
    prompt = _first_non_empty(
        script.get("text_to_video_prompt"),
        script.get("creative_script_text"),
        script.get("opening_hook"),
        script.get("title"),
    )
    # DashScope duration must be between 2 and 15 seconds.
    duration_seconds = _normalize_dashscope_duration(
        script.get("target_duration_seconds"),
        settings.video_duration_seconds,
    )
    if model_name == "wan2.7-t2v":
        return {
            "model": model_name,
            "input": {
                "prompt": prompt,
            },
            "parameters": {
                "resolution": "720P",
                "ratio": settings.video_aspect_ratio,
                "prompt_extend": True,
                "watermark": bool(settings.video_watermark),
                "duration": duration_seconds,
            },
        }

    if model_name == "vidu/viduq3-pro_img2video":
        source_image_url = _first_non_empty(
            script.get("source_image_url"),
            script.get("video_source_image_url"),
        )
        if not source_image_url:
            raise RuntimeError("当前视频模型需要图片输入，请先提供图片 URL。")
        return {
            "model": model_name,
            "input": {
                "media": [
                    {
                        "type": "image",
                        "url": source_image_url,
                    }
                ],
                "prompt": prompt,
            },
            "parameters": {
                "duration": duration_seconds,
                "resolution": "720P",
                "watermark": bool(settings.video_watermark),
            },
        }

    return {
        "model": model_name,
        "input": {
            "prompt": prompt,
        },
        "parameters": {
            "mode": settings.video_mode,
            "aspect_ratio": settings.video_aspect_ratio,
            "duration": duration_seconds,
            "audio": bool(settings.video_audio),
            "watermark": bool(settings.video_watermark),
        },
    }


def submit_video_render(script: dict[str, Any], creator_id: str, platform: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.video_api_url:
        if settings.mock_external_services:
            return _mock_video_result(creator_id)
        raise RuntimeError("VIDEO_API_URL is not configured")

    provider = settings.video_api_provider.lower().strip()
    headers = _build_dashscope_headers() if provider == "dashscope" else _build_generic_headers()
    payload = (
        _build_dashscope_payload(script)
        if provider == "dashscope"
        else _build_generic_payload(script, creator_id, platform)
    )

    try:
        response = httpx.post(
            settings.video_api_url,
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError:
        if settings.mock_external_services:
            return _mock_video_result(creator_id)
        raise

    if provider == "dashscope":
        return _normalize_dashscope_submit(data)

    return {
        "job_id": data.get("job_id") or data.get("video_id") or data.get("id"),
        "status": _normalize_status(data.get("status")),
        "video_url": data.get("video_url"),
        "error": _extract_error_message(data),
        "raw": data,
    }


def poll_video_render(job_id: str) -> dict[str, Any]:
    settings = get_settings()
    provider = settings.video_api_provider.lower().strip()

    if not settings.video_status_api_url:
        if settings.mock_external_services:
            return {
                "job_id": job_id,
                "status": "done",
                "video_url": f"https://example.com/videos/{job_id}.mp4",
                "raw": {"mock": True},
            }
        raise RuntimeError("VIDEO_STATUS_API_URL is not configured")

    headers = _build_generic_headers()
    url = settings.video_status_api_url.format(job_id=job_id)

    try:
        response = httpx.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError:
        if settings.mock_external_services:
            return {
                "job_id": job_id,
                "status": "done",
                "video_url": f"https://example.com/videos/{job_id}.mp4",
                "raw": {"mock": True},
            }
        raise

    if provider == "dashscope":
        return _normalize_dashscope_poll(data, job_id)

    return {
        "job_id": job_id,
        "status": _normalize_status(data.get("status")),
        "video_url": data.get("video_url"),
        "error": _extract_error_message(data),
        "raw": data,
    }


def _slugify_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff_-]+", "-", value).strip("-_")
    return cleaned[:80] or "video"


def _guess_extension(video_url: str) -> str:
    path = urlparse(video_url).path
    suffix = Path(path).suffix.lower()
    if suffix in {".mp4", ".mov", ".webm", ".mkv"}:
        return suffix
    return ".mp4"


def download_video_asset(
    video_url: str,
    creator_id: str,
    platform: str,
    title: str,
) -> str:
    settings = get_settings()
    output_dir = get_video_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_name = _slugify_filename(title or f"{platform}-{creator_id}")
    file_path = output_dir / f"{timestamp}_{creator_id}_{platform}_{base_name}{_guess_extension(video_url)}"

    if settings.mock_external_services and "example.com" in video_url:
        file_path.write_bytes(b"MOCK_VIDEO_PLACEHOLDER")
        return str(file_path)

    with httpx.stream("GET", video_url, timeout=120, follow_redirects=True) as response:
        response.raise_for_status()
        with file_path.open("wb") as handle:
            for chunk in response.iter_bytes():
                if chunk:
                    handle.write(chunk)
    return str(file_path)
