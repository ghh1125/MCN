from __future__ import annotations

from fastapi import APIRouter

from api.schemas.workflow import (
    WorkflowCreateRequest,
    WorkflowLaunchResponse,
    WorkflowSyncResponse,
)
from services.task_store import enqueue_workflow
from workflow.graph import run_workflow
from workflow.state import build_initial_state

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post("", response_model=WorkflowLaunchResponse, status_code=202)
async def launch_workflow(request: WorkflowCreateRequest) -> WorkflowLaunchResponse:
    task = enqueue_workflow(request.model_dump())
    return WorkflowLaunchResponse(
        task_id=task.id,
        status=task.status,
        submitted_at=task.submitted_at,
    )


@router.post("/run-sync", response_model=WorkflowSyncResponse)
async def run_workflow_sync(request: WorkflowCreateRequest) -> WorkflowSyncResponse:
    state = build_initial_state(**request.model_dump())
    result = await run_workflow(state)
    return WorkflowSyncResponse(status="SUCCESS", result=result)
