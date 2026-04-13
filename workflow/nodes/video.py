from __future__ import annotations

import asyncio
from typing import Any

from services.config import get_settings
from services.video_render import download_video_asset, poll_video_render, submit_video_render
from workflow.state import WorkflowState, utc_now_iso


async def video_node(state: WorkflowState) -> dict[str, Any]:
    settings = get_settings()
    payload = await asyncio.to_thread(
        submit_video_render,
        {
            **state["script"],
            "video_source_image_url": state.get("video_source_image_url"),
        },
        state["creator_id"],
        state["platform"],
    )
    job_id = payload.get("job_id")
    status = payload.get("status", "processing")
    video_url = payload.get("video_url")
    error = payload.get("error")
    local_video_path = None

    if status == "processing" and job_id:
        for _ in range(settings.video_max_poll_attempts):
            await asyncio.sleep(settings.video_poll_interval_seconds)
            payload = await asyncio.to_thread(poll_video_render, job_id)
            status = payload.get("status", "processing")
            video_url = payload.get("video_url")
            error = payload.get("error")
            if status in {"done", "failed"}:
                break

    if status == "done" and video_url and settings.save_video_to_disk:
        try:
            local_video_path = await asyncio.to_thread(
                download_video_asset,
                video_url,
                state["creator_id"],
                state["platform"],
                state["script"].get("title", ""),
            )
        except Exception as exc:  # pragma: no cover - runtime integration path
            error = f"{error}; save failed: {exc}" if error else f"save failed: {exc}"

    return {
        "video_job_id": job_id,
        "video_status": status,
        "video_url": video_url,
        "local_video_path": local_video_path,
        "video_raw": payload.get("raw", {}),
        "error": error,
        "updated_at": utc_now_iso(),
    }
