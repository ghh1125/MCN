from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from services.douyin_auth import get_douyin_binding


DOUYIN_OAUTH_BASE = "https://open.douyin.com"


def _extract_error_message(data: dict[str, Any]) -> str:
    data_part = data.get("data") if isinstance(data.get("data"), dict) else {}
    extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
    candidates = [
        str(data_part.get("description", "")).strip(),
        str(extra.get("description", "")).strip(),
        str(extra.get("sub_description", "")).strip(),
    ]
    message = " | ".join(item for item in candidates if item)
    return message or "Douyin API request failed"


def _ensure_success(data: dict[str, Any]) -> None:
    data_part = data.get("data") if isinstance(data.get("data"), dict) else {}
    extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
    error_code = data_part.get("error_code", extra.get("error_code", 0))
    try:
        error_code = int(error_code)
    except (TypeError, ValueError):
        error_code = 0
    if error_code != 0:
        raise RuntimeError(_extract_error_message(data))


def _douyin_headers(access_token: str) -> dict[str, str]:
    return {
        "access-token": access_token,
    }


def _build_publish_text(script: dict[str, Any]) -> str:
    title = str(script.get("title", "")).strip()
    hook = str(script.get("opening_hook", "")).strip()
    cta = str(script.get("cta", "")).strip()
    tags = [str(item).strip() for item in script.get("tags", []) if str(item).strip()]
    parts = [item for item in [title, hook, cta] if item]
    if tags:
        parts.append(" ".join(f"#{item}" for item in tags[:6]))
    return "\n".join(parts).strip()


def upload_douyin_video(local_video_path: str, access_token: str) -> dict[str, Any]:
    path = Path(local_video_path)
    if not path.exists():
        raise RuntimeError("本地视频文件不存在，无法上传到抖音。")

    with path.open("rb") as handle:
        response = httpx.post(
            f"{DOUYIN_OAUTH_BASE}/video/upload/",
            headers=_douyin_headers(access_token),
            files={"video": (path.name, handle, "video/mp4")},
            timeout=120,
        )
    response.raise_for_status()
    data = response.json()
    _ensure_success(data)
    video = data.get("data", {}).get("video", {})
    return {
        "video_id": video.get("video_id", ""),
        "raw": data,
    }


def create_douyin_video(video_id: str, text: str, access_token: str) -> dict[str, Any]:
    payload = {
        "video_id": video_id,
        "text": text,
    }
    response = httpx.post(
        f"{DOUYIN_OAUTH_BASE}/video/create/",
        headers={
            **_douyin_headers(access_token),
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    _ensure_success(data)
    data_part = data.get("data", {})
    return {
        "item_id": data_part.get("item_id", ""),
        "raw": data,
        "request_payload": payload,
    }


def fetch_douyin_video_metrics(access_token: str, item_id: str) -> dict[str, Any]:
    payload = {"item_ids": [item_id]}
    response = httpx.post(
        f"{DOUYIN_OAUTH_BASE}/video/data/",
        headers={
            **_douyin_headers(access_token),
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    _ensure_success(data)
    items = data.get("data", {}).get("list", [])
    item = items[0] if items else {}
    statistics = item.get("statistics", {}) if isinstance(item, dict) else {}
    return {
        "item_id": item.get("item_id", item_id),
        "title": item.get("title", ""),
        "share_url": item.get("share_url", ""),
        "cover": item.get("cover", ""),
        "is_reviewed": item.get("is_reviewed"),
        "create_time": item.get("create_time"),
        "statistics": {
            "digg_count": statistics.get("digg_count", 0),
            "comment_count": statistics.get("comment_count", 0),
            "play_count": statistics.get("play_count", 0),
            "share_count": statistics.get("share_count", 0),
            "download_count": statistics.get("download_count", 0),
            "forward_count": statistics.get("forward_count", 0),
        },
        "raw": data,
    }


def publish_to_douyin(
    *,
    creator_id: str,
    local_video_path: str,
    script: dict[str, Any],
) -> dict[str, Any]:
    binding = get_douyin_binding(creator_id)
    if not binding:
        raise RuntimeError("当前 creator_id 还没有绑定抖音账号。")

    access_token = str(binding.get("access_token", "")).strip()
    if not access_token:
        raise RuntimeError("当前抖音绑定里缺少 access_token。")

    upload_result = upload_douyin_video(local_video_path, access_token)
    video_id = str(upload_result.get("video_id", "")).strip()
    if not video_id:
        raise RuntimeError("抖音视频上传成功，但没有返回 video_id。")

    create_result = create_douyin_video(video_id, _build_publish_text(script), access_token)
    item_id = str(create_result.get("item_id", "")).strip()
    metrics = fetch_douyin_video_metrics(access_token, item_id) if item_id else {}

    return {
        "status": "published",
        "platform": "douyin",
        "creator_id": creator_id,
        "open_id": binding.get("open_id", ""),
        "nickname": binding.get("nickname", ""),
        "video_id": video_id,
        "item_id": item_id,
        "platform_url": metrics.get("share_url", ""),
        "metrics": metrics,
        "upload_raw": upload_result.get("raw", {}),
        "create_raw": create_result.get("raw", {}),
        "metrics_raw": metrics.get("raw", {}) if isinstance(metrics, dict) else {},
    }


def refresh_douyin_publish_metrics(creator_id: str, item_id: str) -> dict[str, Any]:
    binding = get_douyin_binding(creator_id)
    if not binding:
        raise RuntimeError("当前 creator_id 还没有绑定抖音账号。")

    access_token = str(binding.get("access_token", "")).strip()
    if not access_token:
        raise RuntimeError("当前抖音绑定里缺少 access_token。")

    return fetch_douyin_video_metrics(access_token, item_id)
