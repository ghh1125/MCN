from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from typing_extensions import TypedDict

from services.config import get_settings


class WorkflowState(TypedDict, total=False):
    raw_input: str
    creator_id: str
    platform: str
    search_platforms: list[str]
    desired_video_duration_seconds: int
    video_source_image_url: str | None

    search_guidance: str | None
    search_warnings: list[str]
    intent: dict[str, Any]
    retrieved_docs: list[dict[str, Any]]
    trending_topics: list[str]

    topic_report: list[dict[str, Any]]
    selected_topic: dict[str, Any]
    rejected_topics: list[dict[str, Any]]
    topic_feedback: str | None
    topic_feedback_history: list[str]

    script: dict[str, Any]
    script_feedback: str | None
    script_feedback_history: list[str]

    video_job_id: str | None
    video_url: str | None
    local_video_path: str | None
    video_status: str
    video_raw: dict[str, Any]

    publish_result: dict[str, Any]

    error: str | None
    human_review_required: bool
    created_at: str
    updated_at: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_initial_state(
    raw_input: str,
    creator_id: str,
    platform: str,
    search_platforms: list[str] | None = None,
) -> WorkflowState:
    now = utc_now_iso()
    settings = get_settings()
    resolved_search_platforms = [item for item in (search_platforms or [platform]) if item]
    return {
        "raw_input": raw_input,
        "creator_id": creator_id,
        "platform": platform,
        "search_platforms": resolved_search_platforms,
        "desired_video_duration_seconds": max(2, min(15, int(settings.video_duration_seconds))),
        "video_source_image_url": None,
        "search_guidance": None,
        "search_warnings": [],
        "intent": {},
        "retrieved_docs": [],
        "trending_topics": [],
        "topic_report": [],
        "selected_topic": {},
        "rejected_topics": [],
        "topic_feedback": None,
        "topic_feedback_history": [],
        "script": {},
        "script_feedback": None,
        "script_feedback_history": [],
        "video_job_id": None,
        "video_url": None,
        "local_video_path": None,
        "video_status": "pending" if settings.enable_video_pipeline else "skipped",
        "video_raw": {},
        "publish_result": {},
        "error": None,
        "human_review_required": False,
        "created_at": now,
        "updated_at": now,
    }
