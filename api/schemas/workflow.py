from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WorkflowCreateRequest(BaseModel):
    raw_input: str = Field(min_length=1, description="用户原始创作需求")
    creator_id: str = Field(min_length=1, description="达人 ID，用于多租户隔离")
    platform: str = Field(min_length=1, description="目标平台，如 douyin / xiaohongshu")


class WorkflowLaunchResponse(BaseModel):
    task_id: str
    status: str
    submitted_at: datetime


class WorkflowSyncResponse(BaseModel):
    status: str
    result: dict[str, Any]
