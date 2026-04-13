from __future__ import annotations

import asyncio
from typing import Any

from services.publisher import publish_video
from workflow.state import WorkflowState, utc_now_iso


async def publish_node(state: WorkflowState) -> dict[str, Any]:
    if not state.get("video_url"):
        return {
            "publish_result": {},
            "error": "video_url is missing; publish step skipped",
            "updated_at": utc_now_iso(),
        }

    result = await asyncio.to_thread(
        publish_video,
        state["video_url"],
        state.get("local_video_path"),
        state["script"],
        state["creator_id"],
        state["platform"],
    )
    return {
        "publish_result": result,
        "updated_at": utc_now_iso(),
    }
