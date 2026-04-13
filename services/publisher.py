from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx

from services.config import get_settings
from services.douyin_publish import publish_to_douyin


def publish_video(
    video_url: str,
    local_video_path: str | None,
    script: dict[str, Any],
    creator_id: str,
    platform: str,
) -> dict[str, Any]:
    if platform == "douyin":
        if not local_video_path:
            raise RuntimeError("发布到抖音需要本地视频文件。")
        return publish_to_douyin(
            creator_id=creator_id,
            local_video_path=local_video_path,
            script=script,
        )

    settings = get_settings()
    if not settings.publish_api_url:
        if settings.mock_external_services:
            post_id = uuid4().hex
            return {
                "status": "published",
                "post_id": post_id,
                "platform": platform,
                "creator_id": creator_id,
                "platform_url": f"https://example.com/{platform}/posts/{post_id}",
                "raw": {"mock": True, "video_url": video_url, "script": script},
            }
        raise RuntimeError("PUBLISH_API_URL is not configured")

    headers = {"Content-Type": "application/json"}
    if settings.publish_api_key:
        headers["Authorization"] = f"Bearer {settings.publish_api_key}"

    payload = {
        "video_url": video_url,
        "script": script,
        "creator_id": creator_id,
        "platform": platform,
    }
    try:
        response = httpx.post(
            settings.publish_api_url,
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError:
        if settings.mock_external_services:
            post_id = uuid4().hex
            return {
                "status": "published",
                "post_id": post_id,
                "platform": platform,
                "creator_id": creator_id,
                "platform_url": f"https://example.com/{platform}/posts/{post_id}",
                "raw": {"mock": True, "video_url": video_url, "script": script},
            }
        raise

    return {
        "status": data.get("status", "published"),
        "post_id": data.get("post_id") or data.get("id"),
        "platform": platform,
        "creator_id": creator_id,
        "platform_url": data.get("platform_url"),
        "raw": data,
    }
