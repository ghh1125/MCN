from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any
from urllib.parse import quote

import httpx

from services.config import get_search_debug_output_dir, get_settings
from services.platform_alias import get_platform_provider_key
from services.runtime_credentials import get_runtime_api_key


def _mock_results(query: str, top_k: int) -> list[dict[str, Any]]:
    return [
        {
            "title": f"{query} 参考结果 {index + 1}",
            "url": f"https://example.com/search/{index + 1}",
            "content": f"这是与“{query}”相关的模拟搜索结果摘要，用于本地开发。",
            "score": 1.0 - index * 0.05,
            "source": "mock",
        }
        for index in range(top_k)
    ]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_tavily(data: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for item in data.get("results", []):
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
                "score": item.get("score", 0.0),
                "source": "tavily",
            }
        )
    return results


def _normalize_serpapi(data: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for item in data.get("organic_results", []):
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "content": item.get("snippet", ""),
                "score": item.get("position", 0),
                "source": "serpapi",
            }
        )
    return results


def _unwrap_data(data: Any) -> Any:
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return data


def _extract_list_candidates(data: Any, preferred_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if not isinstance(data, dict):
        return []

    for key in preferred_keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_list_candidates(value, preferred_keys)
            if nested:
                return nested

    for value in data.values():
        nested = _extract_list_candidates(value, preferred_keys)
        if nested:
            return nested

    return []


def _pick_first_str(payload: Any, keys: tuple[str, ...]) -> str:
    if not isinstance(payload, dict):
        return ""

    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_text(value)

    for value in payload.values():
        if isinstance(value, dict):
            nested = _pick_first_str(value, keys)
            if nested:
                return nested

    return ""


def _pick_first_number(payload: Any, keys: tuple[str, ...]) -> float:
    if not isinstance(payload, dict):
        return 0.0

    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)

    for value in payload.values():
        if isinstance(value, dict):
            nested = _pick_first_number(value, keys)
            if nested:
                return nested

    return 0.0


def _build_tikhub_url(base_url: str, path: str) -> str:
    normalized_base_url = base_url.rstrip("/")
    if not normalized_base_url or "tavily.com" in normalized_base_url or "serpapi.com" in normalized_base_url:
        normalized_base_url = "https://api.tikhub.io"
    return f"{normalized_base_url}{path}"


def _slugify_debug_fragment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff_-]+", "-", value).strip("-_")
    return cleaned[:60] or "search"


def _write_search_debug_payload(
    *,
    provider: str,
    platform: str,
    search_kind: str,
    query: str,
    endpoint: str,
    method: str,
    request_payload: dict[str, Any] | None,
    request_params: dict[str, Any] | None,
    response_payload: Any,
) -> None:
    settings = get_settings()
    if not settings.search_debug_save_raw:
        return

    output_dir = get_search_debug_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    filename = (
        f"{timestamp}_"
        f"{_slugify_debug_fragment(provider)}_"
        f"{_slugify_debug_fragment(platform or 'unknown')}_"
        f"{_slugify_debug_fragment(search_kind)}_"
        f"{_slugify_debug_fragment(query)}.json"
    )
    file_path = output_dir / filename
    file_path.write_text(
        json.dumps(
            {
                "saved_at": timestamp,
                "provider": provider,
                "platform": platform,
                "search_kind": search_kind,
                "query": query,
                "endpoint": endpoint,
                "method": method,
                "request_params": request_params,
                "request_payload": request_payload,
                "response_payload": response_payload,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _normalize_xiaohongshu_notes(data: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _unwrap_data(data)
    items = _extract_list_candidates(payload, ("items", "notes", "note_list", "list"))
    results: list[dict[str, Any]] = []

    for item in items:
        node = item.get("note_card") if isinstance(item.get("note_card"), dict) else item
        title = _pick_first_str(node, ("display_title", "title", "note_title", "share_title"))
        content = _pick_first_str(node, ("desc", "display_desc", "content", "note_content"))
        note_id = _pick_first_str(node, ("note_id", "id"))
        url = _pick_first_str(node, ("url", "share_url"))
        if not url and note_id:
            url = f"https://www.xiaohongshu.com/explore/{note_id}"
        if not title:
            title = content[:48]
        if title:
            results.append(
                {
                    "title": title,
                    "url": url,
                    "content": content or title,
                    "score": _pick_first_number(node, ("liked_count", "hot_score", "score")),
                    "source": "tikhub:xiaohongshu",
                }
            )

    return results


def _normalize_xiaohongshu_hot(data: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _unwrap_data(data)
    items = _extract_list_candidates(payload, ("items", "list", "hot_list"))
    results: list[dict[str, Any]] = []

    for item in items:
        title = _pick_first_str(item, ("title", "keyword", "query", "word", "name"))
        if title:
            results.append(
                {
                    "title": title,
                    "url": f"https://www.xiaohongshu.com/search_result?keyword={quote(title)}",
                    "content": _pick_first_str(item, ("description", "desc", "subtitle")) or title,
                    "score": _pick_first_number(item, ("hot_score", "score", "rank")),
                    "source": "tikhub:xiaohongshu:hot",
                }
            )

    return results


def _query_terms(query: str) -> list[str]:
    parts = re.split(r"[\s,，。.!！？、/|]+", query)
    return [part.strip().lower() for part in parts if part and len(part.strip()) >= 2]


def _score_relevance(item: dict[str, Any], terms: list[str]) -> float:
    haystack = " ".join(
        [
            str(item.get("title", "")),
            str(item.get("content", "")),
            str(item.get("url", "")),
        ]
    ).lower()
    overlap = sum(1 for term in terms if term in haystack)
    return float(overlap) * 1000.0 + float(item.get("score", 0.0) or 0.0)


def _build_xiaohongshu_hot_fallback(query: str, hot_items: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    terms = _query_terms(query)
    ranked_items = sorted(hot_items, key=lambda item: _score_relevance(item, terms), reverse=True)
    selected = ranked_items[:top_k]
    results: list[dict[str, Any]] = []

    for item in selected:
        title = _clean_text(item.get("title", ""))
        if not title:
            continue
        summary = _clean_text(item.get("content", "")) or f"来自小红书热榜，已按“{query}”做相关性筛选。"
        results.append(
            {
                "title": title,
                "url": item.get("url", f"https://www.xiaohongshu.com/search_result?keyword={quote(title)}"),
                "content": summary,
                "score": item.get("score", 0.0),
                "source": "tikhub:xiaohongshu:hot-fallback",
            }
        )

    return results


def _normalize_douyin_search(data: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _unwrap_data(data)
    items = _extract_list_candidates(payload, ("data", "items", "list", "aweme_list"))
    results: list[dict[str, Any]] = []

    for item in items:
        node = item.get("aweme_info") if isinstance(item.get("aweme_info"), dict) else item
        title = _pick_first_str(node, ("desc", "title", "content"))
        aweme_id = _pick_first_str(node, ("aweme_id", "group_id", "id"))
        url = _pick_first_str(node, ("share_url", "url"))
        if not url and aweme_id:
            url = f"https://www.douyin.com/video/{aweme_id}"
        if title:
            results.append(
                {
                    "title": title,
                    "url": url,
                    "content": title,
                    "score": _pick_first_number(node, ("digg_count", "play_count", "score")),
                    "source": "tikhub:douyin",
                }
            )

    return results


def _normalize_douyin_hot(data: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _unwrap_data(data)
    items = _extract_list_candidates(payload, ("word_list", "items", "list", "data"))
    results: list[dict[str, Any]] = []

    for item in items:
        title = _pick_first_str(item, ("word", "sentence", "title", "keyword", "query"))
        if title:
            results.append(
                {
                    "title": title,
                    "url": f"https://www.douyin.com/search/{quote(title)}",
                    "content": _pick_first_str(item, ("sentence_tag", "content", "subtitle")) or title,
                    "score": _pick_first_number(item, ("hot_value", "score", "position", "rank")),
                    "source": "tikhub:douyin:hot",
                }
            )

    return results


def _normalize_bilibili_search(data: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _unwrap_data(data)
    items = _extract_list_candidates(payload, ("item", "items", "result", "list", "data"))
    results: list[dict[str, Any]] = []

    for item in items:
        title = _pick_first_str(item, ("title", "subject", "show_name"))
        url = _pick_first_str(item, ("arcurl", "url", "share_url"))
        bvid = _pick_first_str(item, ("bvid",))
        if not url and bvid:
            url = f"https://www.bilibili.com/video/{bvid}"
        if not title:
            title = _pick_first_str(item, ("description", "desc"))
        if title:
            results.append(
                {
                    "title": title,
                    "url": url,
                    "content": _pick_first_str(item, ("description", "desc", "content")) or title,
                    "score": _pick_first_number(item, ("play", "score", "rank")),
                    "source": "tikhub:bilibili",
                }
            )

    return results


def _normalize_bilibili_hot(data: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _unwrap_data(data)
    items = _extract_list_candidates(payload, ("list", "items", "data"))
    results: list[dict[str, Any]] = []

    for item in items:
        title = _pick_first_str(item, ("keyword", "show_name", "title", "word"))
        if title:
            results.append(
                {
                    "title": title,
                    "url": f"https://search.bilibili.com/all?keyword={quote(title)}",
                    "content": _pick_first_str(item, ("icon", "description", "desc")) or title,
                    "score": _pick_first_number(item, ("score", "rank", "position")),
                    "source": "tikhub:bilibili:hot",
                }
            )

    return results


async def _search_tikhub(
    query: str,
    platform: str,
    top_k: int,
    search_kind: str,
) -> list[dict[str, Any]]:
    settings = get_settings()
    platform_key = get_platform_provider_key(platform)
    api_key = get_runtime_api_key("search") or settings.search_api_key
    headers = {"Authorization": f"Bearer {api_key}"}
    base_url = settings.search_api_url
    if not base_url:
        raise RuntimeError("SEARCH_API_URL is not configured in the current session or environment")
    xiaohongshu_mode = settings.search_xiaohongshu_content_mode.lower().strip()

    async with httpx.AsyncClient(timeout=settings.search_api_timeout_seconds) as client:
        if platform_key == "xiaohongshu":
            if search_kind == "trending" or xiaohongshu_mode == "hotlist":
                response = await client.get(
                    _build_tikhub_url(base_url, "/api/v1/xiaohongshu/web_v2/fetch_hot_list"),
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
                _write_search_debug_payload(
                    provider="tikhub",
                    platform=platform_key,
                    search_kind=search_kind,
                    query=query,
                    endpoint=str(response.request.url),
                    method="GET",
                    request_payload=None,
                    request_params=None,
                    response_payload=data,
                )
                hot_results = _normalize_xiaohongshu_hot(data)
                if search_kind == "trending":
                    return hot_results[:top_k]
                return _build_xiaohongshu_hot_fallback(query, hot_results, top_k)

            response = await client.get(
                _build_tikhub_url(base_url, "/api/v1/xiaohongshu/app_v2/search_notes"),
                headers=headers,
                params={
                    "keyword": query,
                    "page": 1,
                    "sort_type": "general",
                    "note_type": "不限",
                    "time_filter": "不限",
                },
            )
            response.raise_for_status()
            data = response.json()
            _write_search_debug_payload(
                provider="tikhub",
                platform=platform_key,
                search_kind=search_kind,
                query=query,
                endpoint=str(response.request.url),
                method="GET",
                request_payload=None,
                request_params={
                    "keyword": query,
                    "page": 1,
                    "sort_type": "general",
                    "note_type": "不限",
                    "time_filter": "不限",
                },
                response_payload=data,
            )
            return _normalize_xiaohongshu_notes(data)[:top_k]

        if platform_key == "douyin":
            if search_kind == "trending":
                response = await client.get(
                    _build_tikhub_url(base_url, "/api/v1/douyin/app/v3/fetch_hot_search_list"),
                    headers=headers,
                    params={"board_type": 0, "board_sub_type": ""},
                )
                response.raise_for_status()
                data = response.json()
                _write_search_debug_payload(
                    provider="tikhub",
                    platform=platform_key,
                    search_kind=search_kind,
                    query=query,
                    endpoint=str(response.request.url),
                    method="GET",
                    request_payload=None,
                    request_params={"board_type": 0, "board_sub_type": ""},
                    response_payload=data,
                )
                return _normalize_douyin_hot(data)[:top_k]

            request_payload = {
                "keyword": query,
                "cursor": 0,
                "sort_type": "0",
                "publish_time": "0",
                "filter_duration": "0",
                "content_type": "0",
                "search_id": "",
                "backtrace": "",
            }
            response = await client.post(
                _build_tikhub_url(base_url, "/api/v1/douyin/search/fetch_general_search_v1"),
                headers=headers,
                json=request_payload,
            )
            response.raise_for_status()
            data = response.json()
            _write_search_debug_payload(
                provider="tikhub",
                platform=platform_key,
                search_kind=search_kind,
                query=query,
                endpoint=str(response.request.url),
                method="POST",
                request_payload=request_payload,
                request_params=None,
                response_payload=data,
            )
            return _normalize_douyin_search(data)[:top_k]

        if platform_key == "bilibili":
            if search_kind == "trending":
                response = await client.get(
                    _build_tikhub_url(base_url, "/api/v1/bilibili/web/fetch_hot_search"),
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
                _write_search_debug_payload(
                    provider="tikhub",
                    platform=platform_key,
                    search_kind=search_kind,
                    query=query,
                    endpoint=str(response.request.url),
                    method="GET",
                    request_payload=None,
                    request_params=None,
                    response_payload=data,
                )
                hot_results = _normalize_bilibili_hot(data)[:top_k]
                if hot_results:
                    return hot_results

            response = await client.get(
                _build_tikhub_url(base_url, "/api/v1/bilibili/web/fetch_general_search"),
                headers=headers,
                params={
                    "keyword": query,
                    "order": "totalrank",
                    "page": 1,
                    "page_size": 20,
                    "duration": 0,
                    "pubtime_begin_s": 0,
                    "pubtime_end_s": 0,
                },
            )
            response.raise_for_status()
            data = response.json()
            _write_search_debug_payload(
                provider="tikhub",
                platform=platform_key,
                search_kind=search_kind,
                query=query,
                endpoint=str(response.request.url),
                method="GET",
                request_payload=None,
                request_params={
                    "keyword": query,
                    "order": "totalrank",
                    "page": 1,
                    "page_size": 20,
                    "duration": 0,
                    "pubtime_begin_s": 0,
                    "pubtime_end_s": 0,
                },
                response_payload=data,
            )
            return _normalize_bilibili_search(data)[:top_k]

    if settings.mock_external_services:
        return _mock_results(query, top_k)
    raise RuntimeError(f"TikHub provider does not yet support platform '{platform}'")


async def search_web(
    query: str,
    top_k: int | None = None,
    platform: str = "",
    search_kind: str = "content",
) -> list[dict[str, Any]]:
    settings = get_settings()
    max_results = top_k or settings.search_api_top_k
    provider = settings.search_api_provider.lower().strip()
    api_key = get_runtime_api_key("search") or settings.search_api_key

    if not api_key:
        if settings.mock_external_services:
            return _mock_results(query, max_results)
        raise RuntimeError("SEARCH_API_KEY is not configured in the current session or environment")

    try:
        if provider == "tikhub":
            return await _search_tikhub(
                query=query,
                platform=platform,
                top_k=max_results,
                search_kind=search_kind,
            )

        async with httpx.AsyncClient(timeout=settings.search_api_timeout_seconds) as client:
            if provider == "tavily":
                response = await client.post(
                    settings.search_api_url,
                    json={
                        "api_key": api_key,
                        "query": query,
                        "max_results": max_results,
                        "topic": "general",
                        "search_depth": "basic",
                        "include_raw_content": False,
                    },
                )
                response.raise_for_status()
                return _normalize_tavily(response.json())

            if provider == "serpapi":
                response = await client.get(
                    settings.search_api_url,
                    params={
                        "api_key": api_key,
                        "q": query,
                        "engine": "google",
                        "num": max_results,
                        "hl": "zh-cn",
                        "gl": "cn",
                    },
                )
                response.raise_for_status()
                return _normalize_serpapi(response.json())

            if not settings.search_api_url:
                raise RuntimeError("SEARCH_API_URL is not configured in the current session or environment")
            response = await client.get(settings.search_api_url, params={"q": query, "api_key": api_key, "limit": max_results})
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and isinstance(data.get("results"), list):
                return [
                    {
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "content": item.get("content", item.get("snippet", "")),
                        "score": item.get("score", 0.0),
                        "source": provider or "generic",
                    }
                    for item in data.get("results", [])
                    if isinstance(item, dict)
                ]
    except httpx.HTTPError:
        if settings.mock_external_services:
            return _mock_results(query, max_results)
        raise

    return []
