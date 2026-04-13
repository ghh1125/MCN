from __future__ import annotations

import asyncio
from typing import Any

from services.platform_alias import get_platform_search_label
from services.web_search import search_web
from workflow.state import WorkflowState, utc_now_iso


def _dedupe_docs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for item in items:
        key = (item.get("url") or item.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        results.append(item)
    return results


def _flatten_text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        results: list[str] = []
        for item in value:
            results.extend(_flatten_text_values(item))
        return results
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


async def _search_one_platform(
    *,
    platform: str,
    base_query: str,
    category: str,
    search_guidance: str,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    platform_label = get_platform_search_label(platform)
    warnings: list[str] = []

    try:
        retrieved_docs = await search_web(
            query=f"{base_query} 爆款内容 选题 参考案例",
            top_k=8,
            platform=platform,
            search_kind="content",
        )
    except Exception as exc:
        retrieved_docs = []
        warnings.append(f"{platform_label} 内容搜索失败：{exc}")

    trending_topics = [
        f"[{platform_label}] {item.get('title') or item.get('content', '')[:32]}"
        for item in retrieved_docs[:5]
        if item.get("title") or item.get("content")
    ]

    return retrieved_docs, trending_topics[:5], warnings


async def retrieval_node(state: WorkflowState) -> dict:
    intent = state["intent"]
    search_guidance = (state.get("search_guidance") or "").strip()
    keyword_text = " ".join(_flatten_text_values(intent.get("keywords", [])))
    selected_platforms = state.get("search_platforms") or [state["platform"]]
    search_tasks = []
    for platform in selected_platforms:
        platform_label = get_platform_search_label(platform)
        base_query = " ".join(
            str(value).strip()
            for value in [
                platform_label,
                intent.get("category", ""),
                intent.get("style", ""),
                intent.get("content_type", ""),
                keyword_text,
                search_guidance,
            ]
            if str(value).strip()
        ) or state["raw_input"]
        search_tasks.append(
            _search_one_platform(
                platform=platform,
                base_query=base_query,
                category=intent.get("category", ""),
                search_guidance=search_guidance,
            )
        )

    platform_results = await asyncio.gather(*search_tasks)
    retrieved_docs: list[dict[str, Any]] = []
    trending_topics: list[str] = []
    search_warnings: list[str] = []
    for docs, topics, warnings in platform_results:
        retrieved_docs.extend(docs)
        trending_topics.extend(topics)
        search_warnings.extend(warnings)

    retrieved_docs = _dedupe_docs(retrieved_docs)[:12]
    trending_topics = list(dict.fromkeys(trending_topics))[:8]

    return {
        "retrieved_docs": retrieved_docs,
        "trending_topics": trending_topics,
        "search_warnings": search_warnings,
        "updated_at": utc_now_iso(),
    }
