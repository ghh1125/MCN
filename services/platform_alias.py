from __future__ import annotations

PLATFORM_SEARCH_ALIAS_MAP: dict[str, str] = {
    "xiaohongshu": "小红书",
    "xhs": "小红书",
    "rednote": "小红书",
    "douyin": "抖音",
    "tiktok": "抖音",
    "kuaishou": "快手",
    "bilibili": "B站",
    "b站": "B站",
    "video_account": "微信视频号",
    "wechat_video": "微信视频号",
    "wechat": "微信视频号",
}

PLATFORM_PROVIDER_KEY_MAP: dict[str, str] = {
    "xiaohongshu": "xiaohongshu",
    "xhs": "xiaohongshu",
    "rednote": "xiaohongshu",
    "douyin": "douyin",
    "tiktok": "douyin",
    "bilibili": "bilibili",
    "b站": "bilibili",
}


def get_platform_search_label(platform: str) -> str:
    normalized = platform.strip()
    if not normalized:
        return normalized
    return PLATFORM_SEARCH_ALIAS_MAP.get(normalized.lower(), normalized)


def get_platform_provider_key(platform: str) -> str:
    normalized = platform.strip()
    if not normalized:
        return normalized
    return PLATFORM_PROVIDER_KEY_MAP.get(normalized.lower(), normalized.lower())
