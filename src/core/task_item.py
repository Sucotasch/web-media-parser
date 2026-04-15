#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Task item model for the Web Media Parser task queue system.
"""

import uuid
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


class TaskStatus(str, Enum):
    """Possible states for a task in the queue."""
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class TaskItem:
    """Represents a single parsing task in the queue."""

    url: str
    settings: Dict[str, Any]
    download_path: str

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: TaskStatus = TaskStatus.QUEUED
    stats: Dict[str, int] = field(default_factory=dict)
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Runtime-only — not serialized
    _parser_manager: Any = field(default=None, repr=False, compare=False)
    _thread: Any = field(default=None, repr=False, compare=False)

    # --- Serialization helpers ---

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a JSON-serializable dictionary."""
        return {
            "id": self.id,
            "url": self.url,
            "settings": self.settings,
            "download_path": self.download_path,
            "status": self.status.value,
            "stats": self.stats,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskItem":
        """Restore a TaskItem from a JSON-serializable dictionary."""
        return cls(
            id=data["id"],
            url=data["url"],
            settings=data.get("settings", {}),
            download_path=data.get("download_path", ""),
            status=TaskStatus(data.get("status", "queued")),
            stats=data.get("stats", {}),
            error_message=data.get("error_message"),
            created_at=datetime.fromisoformat(data["created_at"]),
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
        )

    # --- State transitions ---

    def mark_running(self) -> None:
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.now()
        self.completed_at = None
        self.error_message = None
        self.stats = {}

    def mark_paused(self) -> None:
        self.status = TaskStatus.PAUSED

    def mark_completed(self) -> None:
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.now()

    def mark_stopped(self) -> None:
        self.status = TaskStatus.STOPPED
        self.completed_at = datetime.now()

    def mark_failed(self, error: str) -> None:
        self.status = TaskStatus.FAILED
        self.error_message = error
        self.completed_at = datetime.now()
