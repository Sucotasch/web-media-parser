#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""A-4: clear_task_sessions removes per-task session dirs, nothing else."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.app_paths import clear_task_sessions, task_state_path


def test_clear_task_sessions_removes_only_sessions(tmp_path):
    # Build the real per-task layout via the canonical path function
    p1 = task_state_path(str(tmp_path / "site_x"), "task-1")
    p2 = task_state_path(str(tmp_path / "site_y"), "task-2")
    for p in (p1, p2):
        with open(p, "w", encoding="utf-8") as f:
            f.write("pickle-data")
    # User files that must survive
    keep1 = tmp_path / "site_x" / "image1.jpg"
    keep1.write_bytes(b"\xff\xd8jpegdata")
    keep2 = tmp_path / "site_y" / "sub" / "video1.mp4"
    keep2.parent.mkdir(parents=True, exist_ok=True)
    keep2.write_bytes(b"mp4data")

    deleted = clear_task_sessions(str(tmp_path))

    assert deleted == 2
    assert not (tmp_path / "site_x" / "sessions").exists()
    assert not (tmp_path / "site_y" / "sessions").exists()
    assert keep1.exists()
    assert keep2.exists()


def test_clear_task_sessions_noop_on_missing_dir(tmp_path):
    assert clear_task_sessions(str(tmp_path / "does-not-exist")) == 0


def test_task_state_path_roundtrip(tmp_path):
    """Sanity: the canonical path formula is what clear_task_sessions reverses."""
    p = task_state_path(str(tmp_path / "site_z"), "task-9")
    assert os.path.basename(p) == "last_session.pkl"
    assert (tmp_path / "site_z" / "sessions" / "task-9").is_dir()
