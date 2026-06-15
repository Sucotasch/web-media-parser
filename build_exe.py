#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Build portable executable for Web Media Parser using PyInstaller.

Produces a single directory dist/WebMediaParser/ containing:
  - WebMediaParser.exe
  - resources/          (bundled themes, patterns, blocklist)
  - Imagus_sieve_*.json (copied from project root if present)
  - settings.json       (created at runtime, next to exe)
  - sessions/           (created at runtime, next to exe)
  - downloads/          (default download dir, next to exe)
"""

import os
import sys
import shutil
import subprocess


def build_exe():
    print("Building portable WebMediaParser...")

    # Install required packages
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # Clean build directories
    for d in ["build", "dist"]:
        if os.path.exists(d):
            print(f"Cleaning {d}/...")
            try:
                shutil.rmtree(d)
            except PermissionError:
                print(f"  Warning: {d}/ in use, skipping")
            except Exception as e:
                print(f"  Warning: {e}")

    # Build with --onedir (all files in one directory, no temp extraction)
    print("Building (onedir mode)...")
    subprocess.check_call([
        "pyinstaller",
        "--name=WebMediaParser",
        "--windowed",
        "--onedir",
        "-y",
        "--icon=resources/icon.ico",
        # Bundle resources into the package
        "--add-data=resources/dark_theme.qss;resources",
        "--add-data=resources/domain_blocklist.txt;resources",
        "--add-data=resources/patterns/site_patterns.json;resources/patterns",
        # Hidden imports
        "--hidden-import=PySide6.QtCore",
        "--hidden-import=PySide6.QtGui",
        "--hidden-import=PySide6.QtWidgets",
        "--hidden-import=bs4",
        "--hidden-import=lxml.html.clean",
        "--hidden-import=src.core",
        "--hidden-import=src.core.task_item",
        "--hidden-import=src.core.task_queue_manager",
        "--hidden-import=src.app_paths",
        # Exclude unused
        "--exclude-module=PyQt6",
        "--exclude-module=matplotlib",
        "--exclude-module=numpy",
        "--exclude-module=pandas",
        "--exclude-module=scipy",
        "--exclude-module=torch",
        "--exclude-module=pygame",
        "--exclude-module=pyarrow",
        "--exclude-module=pytest",
        "--exclude-module=IPython",
        "--exclude-module=notebook",
        "--exclude-module=jupyter",
        # Collect submodules
        "--collect-submodules=bs4",
        "--collect-submodules=lxml.html.clean",
        "main.py",
    ])

    # Copy Imagus sieve files next to the exe
    print("Copying Imagus sieve files...")
    dist_dir = os.path.join("dist", "WebMediaParser")
    for filename in os.listdir("."):
        if filename.startswith("Imagus_sieve") and filename.endswith(".json"):
            shutil.copy2(filename, os.path.join(dist_dir, filename))
            print(f"  {filename}")

    # Create empty directories for first run
    os.makedirs(os.path.join(dist_dir, "sessions"), exist_ok=True)

    print(f"\nBuild complete: {os.path.abspath(dist_dir)}")
    print(f"Contents:")
    for item in sorted(os.listdir(dist_dir)):
        size = os.path.getsize(os.path.join(dist_dir, item))
        if size > 1024 * 1024:
            print(f"  {item:40s} {size / 1024 / 1024:.1f} MB")
        elif size > 1024:
            print(f"  {item:40s} {size / 1024:.0f} KB")
        else:
            print(f"  {item:40s} {size} B")


if __name__ == "__main__":
    build_exe()
