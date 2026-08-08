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
    # Use the interpreter's own PyInstaller (not a PATH-dependent bare name).
    print("Building (onedir mode)...")
    subprocess.check_call([
        sys.executable, "-m", "PyInstaller",
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
        "main.py",
    ])

    # Copy Imagus sieve files next to the exe
    print("Copying Imagus sieve files...")
    dist_dir = os.path.join("dist", "WebMediaParser")
    for filename in os.listdir("."):
        if filename.startswith("Imagus_sieve") and filename.endswith(".json"):
            shutil.copy2(filename, os.path.join(dist_dir, filename))
            print(f"  {filename}")

    # Junk-filter allowlist template (P2-lite), next to the exe for editing
    allowlist_src = os.path.join("resources", "junk_allowlist.txt")
    if os.path.exists(allowlist_src):
        shutil.copy2(allowlist_src, os.path.join(dist_dir, "junk_allowlist.txt"))
        print("  junk_allowlist.txt (P2-lite template)")

    # Bundle the Deno JS engine workers (P0 sieve worker + P1 DOM worker)
    worker_src = os.path.join("src", "parser", "js_engine", "worker.js")
    dom_worker_src = os.path.join("src", "parser", "js_engine", "dom_worker.js")
    if os.path.exists(worker_src):
        bin_dir = os.path.join(dist_dir, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        shutil.copy2(worker_src, os.path.join(bin_dir, "worker.js"))
        print("  worker.js (Deno JS engine, P0)")
        if os.path.exists(dom_worker_src):
            shutil.copy2(dom_worker_src, os.path.join(bin_dir, "dom_worker.js"))
            print("  dom_worker.js (happy-dom, P1)")
        # Also bundle the Deno binary so the engine works on any machine.
        deno_bin = os.environ.get("WEB_MEDIA_PARSER_DENO")
        if not deno_bin or not os.path.exists(deno_bin):
            # Common location: media-downloader's bundled Deno.
            appdata = os.environ.get("APPDATA") or ""
            cand = os.path.join(appdata, "media-downloader", "bin", "deno.exe")
            if os.path.exists(cand):
                deno_bin = cand
        if deno_bin and os.path.exists(deno_bin):
            shutil.copy2(deno_bin, os.path.join(bin_dir, "deno.exe"))
            print(f"  deno.exe ({os.path.getsize(deno_bin) // (1024 * 1024)} MB)")
        else:
            print("  WARNING: deno.exe not found — JS engine will fall back to static")
        # Bundle the happy-dom npm cache (DENO_DIR) for the DOM worker: reuse
        # the dev cache when present, otherwise populate it at build time
        # (requires network once; the worker runs fully offline afterwards).
        # Bundle the happy-dom npm cache (DENO_DIR/npm) for the DOM worker:
        # reuse the dev cache when present, otherwise populate it at build
        # time (requires network once; the worker runs fully offline after).
        # Only the npm/ subtree is copied — Deno's other caches regenerate.
        deno_cache_src = os.path.join("src", "parser", "js_engine", "deno_cache")
        cache_target = os.path.join(bin_dir, "deno_cache")
        npm_src = os.path.join(deno_cache_src, "npm")
        if os.path.isdir(npm_src):
            shutil.copytree(npm_src, os.path.join(cache_target, "npm"), dirs_exist_ok=True)
            print("  deno_cache/npm (happy-dom)")
        elif deno_bin and os.path.exists(deno_bin) and os.path.exists(dom_worker_src):
            try:
                os.makedirs(cache_target, exist_ok=True)
                env = dict(os.environ)
                env["DENO_DIR"] = cache_target
                req = ('{"id":0,"html":"<html></html>","pageUrl":"https://x/",'
                       '"code":"return 1","groups":[],"href":""}\n')
                subprocess.run(
                    [deno_bin, "run", "--quiet", os.path.join(bin_dir, "dom_worker.js")],
                    input=req, capture_output=True, text=True, env=env, timeout=300,
                )
                print("  deno_cache/npm (populated at build time)")
            except Exception as e:
                print(f"  WARNING: could not populate deno_cache: {e}")

    # Create empty directories for first run
    os.makedirs(os.path.join(dist_dir, "sessions"), exist_ok=True)

    print(f"\nBuild complete: {os.path.abspath(dist_dir)}")
    print("Contents:")
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
