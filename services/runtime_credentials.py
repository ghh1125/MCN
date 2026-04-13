from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


_RUNTIME_API_KEYS: ContextVar[dict[str, str]] = ContextVar("runtime_api_keys", default={})


def get_runtime_api_key(kind: str) -> str:
    return _RUNTIME_API_KEYS.get().get(kind, "")


@contextmanager
def use_runtime_api_keys(
    *,
    planning_api_key: str = "",
    search_api_key: str = "",
    video_api_key: str = "",
) -> Iterator[None]:
    current = dict(_RUNTIME_API_KEYS.get())
    if planning_api_key:
        current["planning"] = planning_api_key
    if search_api_key:
        current["search"] = search_api_key
    if video_api_key:
        current["video"] = video_api_key

    token = _RUNTIME_API_KEYS.set(current)
    try:
        yield
    finally:
        _RUNTIME_API_KEYS.reset(token)
