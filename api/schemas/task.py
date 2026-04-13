from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    ready: bool
    submitted_at: datetime
    updated_at: datetime
    result: dict[str, Any] | None = None
    error: str | None = None
