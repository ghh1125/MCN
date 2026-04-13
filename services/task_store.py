from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

from workflow.graph import run_workflow
from workflow.state import build_initial_state


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class TaskHandle:
    id: str
    status: str
    submitted_at: datetime


_LOCK = Lock()
_TASKS: dict[str, dict[str, Any]] = {}
_RUNNING: dict[str, asyncio.Task[Any]] = {}


def _update_task(task_id: str, **updates: Any) -> None:
    with _LOCK:
        record = _TASKS.get(task_id)
        if record is None:
            return
        record.update(updates)
        record["updated_at"] = _utc_now()


async def _run_background_workflow(task_id: str, payload: dict[str, Any]) -> None:
    _update_task(task_id, status="STARTED", ready=False)
    try:
        state = build_initial_state(
            raw_input=payload["raw_input"],
            creator_id=payload["creator_id"],
            platform=payload["platform"],
        )
        result = await run_workflow(state)
        _update_task(
            task_id,
            status="SUCCESS",
            ready=True,
            result=result,
            error=None,
        )
    except Exception as exc:  # pragma: no cover - runtime integration path
        _update_task(
            task_id,
            status="FAILURE",
            ready=True,
            result=None,
            error=str(exc),
        )
    finally:
        with _LOCK:
            _RUNNING.pop(task_id, None)


def enqueue_workflow(payload: dict[str, Any]) -> TaskHandle:
    task_id = uuid4().hex
    submitted_at = _utc_now()
    with _LOCK:
        _TASKS[task_id] = {
            "task_id": task_id,
            "status": "PENDING",
            "ready": False,
            "submitted_at": submitted_at,
            "updated_at": submitted_at,
            "result": None,
            "error": None,
        }

    task = asyncio.create_task(_run_background_workflow(task_id, payload))
    with _LOCK:
        _RUNNING[task_id] = task
    return TaskHandle(id=task_id, status="PENDING", submitted_at=submitted_at)


def get_task_record(task_id: str) -> dict[str, Any] | None:
    with _LOCK:
        record = _TASKS.get(task_id)
        return dict(record) if record is not None else None
