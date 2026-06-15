#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Task queue manager for managing multiple parsing tasks sequentially.
"""

import os
import json
import glob
import logging
from typing import Dict, List, Optional
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from src.core.task_item import TaskItem, TaskStatus
from src import constants as K

logger = logging.getLogger(__name__)


class TaskQueueManager(QObject):
    """Manages a sequential queue of parsing tasks.

    One active task at a time.  All methods are synchronous and
    must be called from the GUI thread (except signal emissions).
    """

    # --- Signals ---
    task_added = Signal(str)                    # task_id
    task_removed = Signal(str)                  # task_id
    task_status_changed = Signal(str, str)      # task_id, new_status
    active_task_changed = Signal(str)           # task_id or ""
    task_progress_updated = Signal(str, int)    # task_id, percent

    def __init__(self, base_download_dir: str):
        super().__init__()
        self._base_dir = base_download_dir
        self._queue: List[TaskItem] = []
        self._active_id: Optional[str] = None

    # --- Properties ---

    @property
    def active_task(self) -> Optional[TaskItem]:
        """Currently running task."""
        if self._active_id is None:
            return None
        for t in self._queue:
            if t.id == self._active_id:
                return t
        return None

    @property
    def queue(self) -> List[TaskItem]:
        """All tasks in order (read-only view)."""
        return list(self._queue)

    @property
    def base_download_dir(self) -> str:
        return self._base_dir

    # --- CRUD ---

    def add_task(self, url: str, settings: dict, download_path: str, one_shot: bool = False) -> TaskItem:
        """Add a new task to the queue.

        Inserts after the last non-completed task (active/queued/paused),
        so new tasks appear above completed/stopped/failed ones.
        Settings are **snapshotted** at add-time.
        """
        task = TaskItem(
            url=url,
            settings=dict(settings),       # shallow copy is enough
            download_path=download_path,
            one_shot=one_shot,
        )
        terminal = {TaskStatus.COMPLETED, TaskStatus.STOPPED, TaskStatus.FAILED}
        insert_idx = len(self._queue)
        for i in range(len(self._queue) - 1, -1, -1):
            if self._queue[i].status not in terminal:
                insert_idx = i + 1
                break
        else:
            # All tasks are terminal or queue is empty — insert at start
            insert_idx = 0
        self._queue.insert(insert_idx, task)
        logger.info(f"Task added to queue: {task.id}  url={url}  position={insert_idx}")
        self.task_added.emit(task.id)
        return task

    def remove_task(self, task_id: str) -> bool:
        """Remove a task from the queue.  Cannot remove the active task."""
        if task_id == self._active_id:
            logger.warning(f"Cannot remove active task {task_id}")
            return False
        for i, t in enumerate(self._queue):
            if t.id == task_id:
                self._queue.pop(i)
                self.task_removed.emit(task_id)
                return True
        return False

    def move_task_up(self, task_id: str) -> bool:
        """Move task one position closer to the front."""
        return self._move_task(task_id, -1)

    def move_task_down(self, task_id: str) -> bool:
        """Move task one position towards the back."""
        return self._move_task(task_id, +1)

    def _move_task(self, task_id: str, delta: int) -> bool:
        if self._active_id == task_id:
            return False
        idx = next((i for i, t in enumerate(self._queue) if t.id == task_id), None)
        if idx is None:
            return False
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self._queue):
            return False
        self._queue.insert(new_idx, self._queue.pop(idx))
        return True

    def find_task(self, task_id: str) -> Optional[TaskItem]:
        for t in self._queue:
            if t.id == task_id:
                return t
        return None

    # --- Lifecycle ---

    def start_task(self, task_id: str) -> bool:
        """Start a specific task.  Pauses the current active task if any."""
        task = self.find_task(task_id)
        if task is None:
            logger.warning(f"Cannot start: task {task_id} not found")
            return False
        if task.status == TaskStatus.RUNNING:
            return True  # already running

        # Pause current active if different
        if self._active_id and self._active_id != task_id:
            self._pause_current_active()

        task.mark_running()
        self._active_id = task_id
        self.active_task_changed.emit(task_id)
        self.task_status_changed.emit(task_id, task.status.value)
        logger.info(f"Starting task: {task_id}  url={task.url}")
        return True

    def start_next(self) -> bool:
        """Start the first queued (non-completed/stopped/failed) task."""
        for t in self._queue:
            if t.status in (TaskStatus.QUEUED, TaskStatus.PAUSED):
                return self.start_task(t.id)
        # Nothing to start
        if self._active_id:
            self._active_id = None
            self.active_task_changed.emit("")
        return False

    def pause_active_task(self) -> bool:
        """Soft-stop the active task.  State is saved; partial files kept."""
        if self._active_id is None:
            return False
        self._pause_current_active()
        return True

    def _pause_current_active(self) -> None:
        """Internal: mark active task as paused and free the slot.

        The caller (MainWindow) is responsible for actually calling
        parser_manager.pause_parsing() and saving state BEFORE this.
        """
        task = self.active_task
        if task is None:
            return
        task.mark_paused()
        old_id = self._active_id
        self._active_id = None
        self.active_task_changed.emit("")
        self.task_status_changed.emit(old_id, task.status.value)
        logger.info(f"Active task paused: {old_id}")

    def clear_active(self) -> None:
        """Release the active slot without changing the task's status."""
        old_id = self._active_id
        self._active_id = None
        if old_id:
            logger.debug(f"clear_active: cleared {old_id[:8]}")
            self.active_task_changed.emit("")

    def get_state_file_path(self, task_id: str) -> str:
        """Return the full path where a task's session state should be saved."""
        session_dir = os.path.join(self._base_dir, "sessions", task_id)
        os.makedirs(session_dir, exist_ok=True)
        return os.path.join(session_dir, "state.pkl")

    def cleanup_partial_files(self, task: TaskItem) -> None:
        """Remove *.part* and incomplete files in a task's download folder.

        Called when a task is hard-stopped.
        """
        dp = task.download_path
        if not dp or not os.path.isdir(dp):
            return
        # Remove .part* files
        for p in glob.glob(os.path.join(dp, "**", "*.part*"), recursive=True):
            try:
                os.remove(p)
            except OSError:
                pass
        # Remove files with size 0 (stuck single-thread downloads)
        for root, _, files in os.walk(dp):
            for fn in files:
                fp = os.path.join(root, fn)
                try:
                    if os.path.getsize(fp) == 0:
                        os.remove(fp)
                except OSError:
                    pass

    # --- Persistence ---

    def save(self, filepath: str) -> None:
        """Save the entire queue state to a JSON file."""
        data = {
            "version": 1,
            "active_task_id": self._active_id,
            "tasks": [t.to_dict() for t in self._queue],
        }
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"Queue saved to {filepath} ({len(self._queue)} tasks)")

    def load(self, filepath: str) -> int:
        """Load queue from JSON file.  Returns number of tasks loaded."""
        if not os.path.exists(filepath):
            logger.info(f"No queue file at {filepath}, starting empty")
            return 0
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._queue = [TaskItem.from_dict(t) for t in data.get("tasks", [])]
            self._active_id = None  # Reset — no task is actually running after reload
            # Reset any tasks that were running (they are not actually running now)
            for t in self._queue:
                if t.status == TaskStatus.RUNNING:
                    t.status = TaskStatus.PAUSED
            logger.info(f"Queue loaded from {filepath} ({len(self._queue)} tasks)")
            return len(self._queue)
        except Exception as e:
            logger.error(f"Error loading queue from {filepath}: {e}", exc_info=True)
            return 0
