#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Build executable for Web Media Parser using PyInstaller
"""

import os
import sys
import shutil
import subprocess


def build_exe():
    """
    Build executable using PyInstaller
    """
    print("Building executable for Web Media Parser...")

    # Install required packages (Already done manually or via requirements.txt)
    # print("Installing required packages...")
    # subprocess.check_call(
    #     [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]
    # )
    # subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # Clean build directories
    for dir_name in ["build", "dist"]:
        if os.path.exists(dir_name):
            shutil.rmtree(dir_name)

    # Build executable
    print("Building executable...")
    cmd = [
        "pyinstaller",
        "--name=WebMediaParser",
        "--windowed",
        "--onefile", # Single standalone executable
        "--noconfirm",
        "--icon=resources/icon.ico",
        "--add-data=resources/dark_theme.qss;resources",
        "--add-data=site_patterns.json;resources/patterns",
        "--hidden-import=PySide6.QtCore",
        "--hidden-import=PySide6.QtGui",
        "--hidden-import=PySide6.QtWidgets",
        "--hidden-import=scrapling",
        "--hidden-import=playwright",
        "--hidden-import=qasync",
        "--hidden-import=bs4",
        "--hidden-import=lxml_html_clean",
        "--hidden-import=aiohttp",
        "--hidden-import=aiofiles",
        "--hidden-import=yarl",
        "--hidden-import=aiodns",
        "--exclude-module=PyQt5",
        "--exclude-module=PyQt5.QtCore",
        "--exclude-module=PyQt5.QtGui",
        "--exclude-module=PyQt5.QtWidgets",
        "--exclude-module=PyQt6",
        "--exclude-module=PySide2",
        "--additional-hooks-dir=hooks",
        "--collect-all=scrapling",
        "--collect-all=playwright",
        "--collect-data=browserforge",
        "--collect-data=apify_fingerprint_datapoints",
        "main.py",
    ]
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True
    )
    
    for line in process.stdout:
        print(line, end="")
    
    process.wait()
    if process.returncode != 0:
        print(f"PyInstaller failed with return code {process.returncode}")
        sys.exit(process.returncode)
        
    print("Build successful!")
    print("Build completed!")
    print(f"Executable path: {os.path.abspath('dist/WebMediaParser.exe')}")


if __name__ == "__main__":
    build_exe()
