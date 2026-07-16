#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Application path utilities for portable builds.
All paths are resolved relative to the executable location,
not hardcoded to any specific directory.
"""

import os
import sys


def get_app_dir() -> str:
    """Return the application root directory.

    - Frozen (PyInstaller onedir): directory containing the .exe
    - Development: project root (2 levels up from this file: src/app_paths.py)
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_resource_dir() -> str:
    """Return the directory containing bundled resources.

    - Frozen: _MEIPASS/resources/ (inside the bundle)
    - Development: project_root/resources/
    """
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "resources")


def resource_path(filename: str) -> str:
    """Return the full path to a resource file by name."""
    return os.path.join(get_resource_dir(), filename)


def settings_path() -> str:
    """Return the path for settings.json (always next to the exe)."""
    return os.path.join(get_app_dir(), "settings.json")


def queue_path() -> str:
    """Return the path for task_queue.json (always next to the exe)."""
    return os.path.join(get_app_dir(), "task_queue.json")


def sessions_dir() -> str:
    """Return the directory for session state files."""
    d = os.path.join(get_app_dir(), "sessions")
    os.makedirs(d, exist_ok=True)
    return d


def default_download_dir() -> str:
    """Return the default download directory (next to the exe)."""
    d = os.path.join(get_app_dir(), "downloads")
    os.makedirs(d, exist_ok=True)
    return d


def task_state_path(task_download_path: str, task_id) -> str:
    """Canonical session pickle path for a task.

    Used by ParserManager (save/load) and MainWindow (stop delete)
    to ensure all paths align.
    """
    session_dir = os.path.join(task_download_path, "sessions")
    if task_id:
        session_dir = os.path.join(session_dir, task_id)
    os.makedirs(session_dir, exist_ok=True)
    return os.path.join(session_dir, "last_session.pkl")
