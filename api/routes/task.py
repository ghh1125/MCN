from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.schemas.task import TaskStatusResponse
from services.task_store import get_task_record

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str) -> TaskStatusResponse:
    task = get_task_record(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(**task)
