from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator


TraceSink = Callable[[list[dict[str, Any]]], None]

_TRACE_EVENTS: ContextVar[list[dict[str, Any]] | None] = ContextVar("llm_trace_events", default=None)
_TRACE_SINK: ContextVar[TraceSink | None] = ContextVar("llm_trace_sink", default=None)


@contextmanager
def capture_llm_trace(sink: TraceSink | None = None) -> Iterator[list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    token_events = _TRACE_EVENTS.set(events)
    token_sink = _TRACE_SINK.set(sink)
    try:
        yield events
    finally:
        _TRACE_SINK.reset(token_sink)
        _TRACE_EVENTS.reset(token_events)


def record_llm_event(event: dict[str, Any]) -> None:
    events = _TRACE_EVENTS.get()
    if events is None:
        return

    events.append(event)
    sink = _TRACE_SINK.get()
    if sink is not None:
        sink(list(events))
