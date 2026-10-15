#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Task queue manager for managing multiple parsing tasks sequentially.
"""

import os
import re
import json
import glob
import copy
import logging
import threading
from typing import List, Optional

from PySide6.QtCore import QObject, Signal

from src.core.task_item import TaskItem, TaskStatus
from src.app_paths import task_state_path

logger = logging.getLogger(__name__)


class TaskQueueManager(QObject):
    """Manages a sequential queue of parsing tasks.

    One active task at a time.  Mutations happen on the GUI thread, but the
    extension's HTTP thread READS the queue (`/api/status`, `/api/queue`), so
    every public entry point holds `_lock` for its mutation/snapshot only and
    emits Qt signals after releasing it (C-08: a slot may call back into the
    manager, possibly from another thread — emitting under the lock would
    deadlock it).
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
        # Re-entrant: several methods call each other (start_next -> start_task,
        # start_task -> _pause_current_active_locked).
        self._lock = threading.RLock()

    # --- Properties ---

    @property
    def active_task(self) -> Optional[TaskItem]:
        """Currently running task."""
        with self._lock:
            if self._active_id is None:
                return None
            for t in self._queue:
                if t.id == self._active_id:
                    return t
            return None

    @property
    def queue(self) -> List[TaskItem]:
        """All tasks in order (read-only view)."""
        with self._lock:
            return list(self._queue)

    def snapshot(self) -> dict:
        """One consistent read of the queue state (C-08).

        The extension status endpoint used to read `active_task` and
        `len(queue)` as separate operations while the GUI thread could be
        inserting or starting a task in between, so one HTTP response could mix
        two different queue states. Everything below is read under a single
        acquisition of the lock — this is the only entry point the HTTP thread
        should use.
        """
        with self._lock:
            active = self.active_task
            return {
                "active_task_id": active.id if active else None,
                "active_task_status": active.status.value if active else None,
                "files_downloaded": (active.stats.get("files_downloaded", 0)
                                     if active else 0),
                "queue_length": len(self._queue),
                "task_ids": [t.id for t in self._queue],
            }

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
            settings=copy.deepcopy(settings),  # deep copy: nested lists (e.g. stop_words)
            download_path=download_path,       # must not be shared between tasks
            one_shot=one_shot,
        )
        with self._lock:
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
        with self._lock:
            if task_id == self._active_id:
                logger.warning(f"Cannot remove active task {task_id}")
                return False
            for i, t in enumerate(self._queue):
                if t.id == task_id:
                    self._queue.pop(i)
                    break
            else:
                return False
        self.task_removed.emit(task_id)
        return True

    def move_task_up(self, task_id: str) -> bool:
        """Move task one position closer to the front."""
        return self._move_task(task_id, -1)

    def move_task_down(self, task_id: str) -> bool:
        """Move task one position towards the back."""
        return self._move_task(task_id, +1)

    def _move_task(self, task_id: str, delta: int) -> bool:
        with self._lock:
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
        with self._lock:
            for t in self._queue:
                if t.id == task_id:
                    return t
            return None

    # --- Lifecycle ---

    def start_task(self, task_id: str) -> bool:
        """Start a specific task.  Pauses the current active task if any."""
        with self._lock:
            task = self.find_task(task_id)
            if task is None:
                logger.warning(f"Cannot start: task {task_id} not found")
                return False
            if task.status == TaskStatus.RUNNING:
                return True  # already running

            # Pause current active if different
            paused = (self._pause_current_active_locked()
                      if self._active_id and self._active_id != task_id else None)

            task.mark_running()
            self._active_id = task_id

        # C-08: every signal below is emitted with the lock released.
        if paused is not None:
            self.active_task_changed.emit("")
            self.task_status_changed.emit(paused.id, paused.status.value)
        self.active_task_changed.emit(task_id)
        self.task_status_changed.emit(task_id, task.status.value)
        logger.info(f"Starting task: {task_id}  url={task.url}")
        return True

    def start_next(self, completed_task_id: str = None) -> bool:
        """Start the next queued (non-completed/stopped/failed) task.

        GUI-9 (owner decision): auto-start advances DOWNWARD from the task
        that just finished — tasks the user placed above it keep their state
        (paused/queued) and are never picked up. Without an anchor (or when
        the anchor is gone after a state restore) it falls back to scanning
        from the top of the list, preserving the historical behavior.
        """
        with self._lock:
            start_idx = 0
            if completed_task_id:
                anchor = next(
                    (i for i, t in enumerate(self._queue) if t.id == completed_task_id),
                    None,
                )
                if anchor is not None:
                    start_idx = anchor + 1
            next_id = next(
                (t.id for t in self._queue[start_idx:]
                 if t.status in (TaskStatus.QUEUED, TaskStatus.PAUSED)),
                None,
            )
            had_active = self._active_id is not None
            if next_id is None:
                self._active_id = None
        if next_id is not None:
            return self.start_task(next_id)
        # Nothing to start
        if had_active:
            self.active_task_changed.emit("")
        return False

    def pause_active_task(self) -> bool:
        """Soft-stop the active task.  State is saved; partial files kept."""
        with self._lock:
            task = self._pause_current_active_locked()
        if task is None:
            return False
        self.active_task_changed.emit("")
        self.task_status_changed.emit(task.id, task.status.value)
        return True

    def _pause_current_active_locked(self) -> Optional[TaskItem]:
        """Internal (call with the lock held): mark the active task paused and
        free the slot.  Returns the paused task so the CALLER can emit the two
        signals after the lock is released (C-08).

        The caller (MainWindow) is responsible for actually calling
        parser_manager.save_state()/stop_parsing() BEFORE this.
        """
        task = self.active_task
        if task is None:
            return None
        task.mark_paused()
        old_id = self._active_id
        self._active_id = None
        logger.info(f"Active task paused: {old_id}")
        return task

    def clear_active(self) -> None:
        """Release the active slot without changing the task's status."""
        with self._lock:
            old_id = self._active_id
            self._active_id = None
        if old_id:
            logger.debug(f"clear_active: cleared {old_id[:8]}")
            self.active_task_changed.emit("")

    def get_state_file_path(self, task: TaskItem) -> str:
        """Return the canonical session pickle path for a task.

        Uses task_state_path so save/load/delete all agree on the path.
        """
        return task_state_path(task.download_path, task.id)

    def cleanup_partial_files(self, task: TaskItem) -> None:
        """Remove *.part* and incomplete files in a task's download folder.

        Called when a task is hard-stopped.
        """
        dp = task.download_path
        if not dp or not os.path.isdir(dp):
            return
        # Remove only the downloader's own temp files (GUI-9): single-thread
        # downloads write '{file}.{tid:x}.partial', MT chunks '{file}.{tid:x}.partN',
        # and the MT assembly writes '{file}.mt.partial' (B-10/NF-3) — the last two
        # are covered by the same two patterns. A broad '*.part*' glob also matched
        # user files like 'foo.partial.png', so the patterns stay narrow.
        for pat in ("*.partial", "*.part[0-9]*"):
            for p in glob.glob(os.path.join(dp, "**", pat), recursive=True):
                try:
                    os.remove(p)
                except OSError:
                    pass
        # Remove stuck empty temp downloads (0 bytes); user files are untouched.
        for root, _, files in os.walk(dp):
            for fn in files:
                if not (fn.endswith(".partial") or re.search(r"\.part\d+$", fn)):
                    continue
                fp = os.path.join(root, fn)
                try:
                    if os.path.getsize(fp) == 0:
                        os.remove(fp)
                except OSError:
                    pass

    # --- Persistence ---

    def save(self, filepath: str) -> None:
        """Save the entire queue state to a JSON file (atomic write, GUI-4)."""
        with self._lock:
            data = {
                "version": 1,
                "active_task_id": self._active_id,
                "tasks": [t.to_dict() for t in self._queue],
            }
        # R-P-1: a bare filename ("task_queue.json") has no directory part;
        # os.makedirs("") raised FileNotFoundError and the queue was never saved.
        dir_path = os.path.dirname(filepath)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        # Write to a temp file and atomically replace — a crash mid-write can
        # no longer leave a corrupt queue file that erases the whole queue.
        tmp = filepath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, filepath)
        logger.info(f"Queue saved to {filepath} ({len(self._queue)} tasks)")

    def load(self, filepath: str) -> int:
        """Load queue from JSON file.  Returns number of tasks loaded."""
        if not os.path.exists(filepath):
            logger.info(f"No queue file at {filepath}, starting empty")
            return 0
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            tasks = []
            for t in data.get("tasks", []):
                try:
                    tasks.append(TaskItem.from_dict(t))
                except Exception as e:
                    # GUI-4: one corrupt entry must not erase the whole queue
                    logger.warning(f"Skipping corrupt task entry: {e}")
            with self._lock:
                self._queue = tasks
                self._active_id = None  # Reset — no task is running after reload
                # Reset tasks that were running (they are not running now)
                for t in self._queue:
                    if t.status == TaskStatus.RUNNING:
                        t.status = TaskStatus.PAUSED
                count = len(self._queue)
            logger.info(f"Queue loaded from {filepath} ({count} tasks)")
            return count
        except Exception as e:
            logger.error(f"Error loading queue from {filepath}: {e}", exc_info=True)
            return 0
