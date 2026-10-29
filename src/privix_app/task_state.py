"""Privix Task Lifecycle States and Grounded Execution Verification."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class TaskState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class TaskEvent:
    timestamp: str
    state: TaskState
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskExecutionResult:
    task_id: str
    state: TaskState
    success: bool
    execution_time_seconds: float
    grounded_summary: str
    actual_page_title: str | None = None
    actual_page_url: str | None = None
    audit_events_count: int = 0
    history: list[TaskEvent] = field(default_factory=list)
    error: str | None = None


class TaskLifecycleTracker:
    """Manages explicit task states, history timeline, and verification."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.current_state = TaskState.QUEUED
        self.history: list[TaskEvent] = []
        self._record_state(TaskState.QUEUED, "Task created and queued")

    def _record_state(self, state: TaskState, message: str, details: dict[str, Any] | None = None) -> TaskEvent:
        self.current_state = state
        event = TaskEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            state=state,
            message=message,
            details=details or {},
        )
        self.history.append(event)
        return event

    def transition_to(self, state: TaskState, message: str, details: dict[str, Any] | None = None) -> TaskEvent:
        return self._record_state(state, message, details)

    def get_timeline(self) -> list[dict[str, Any]]:
        return [
            {
                "timestamp": e.timestamp,
                "state": e.state.value,
                "message": e.message,
                "details": e.details,
            }
            for e in self.history
        ]

