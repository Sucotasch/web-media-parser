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

    # Install required packages
    print("Installing required packages...")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]
    )
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # Clean build directories
    for dir_name in ["build", "dist"]:
        if os.path.exists(dir_name):
            shutil.rmtree(dir_name)

    # Build executable
    print("Building executable...")
    subprocess.check_call(
        [
            "pyinstaller",
            "--name=WebMediaParser",
            "--windowed",
            "--onefile",
            "--icon=resources/icon.ico",
            "--add-data=resources/dark_theme.qss;resources",
            "--add-data=resources/patterns/site_patterns.json;resources/patterns",
            "--hidden-import=PySide6.QtCore",
            "--hidden-import=PySide6.QtGui",
            "--hidden-import=PySide6.QtWidgets",
            "--hidden-import=requests_html",
            "--hidden-import=pyppeteer",
            "--hidden-import=bs4",
            "--hidden-import=lxml_html_clean",
            "--exclude-module=PyQt6",
            "--collect-submodules=requests_html",
            "--collect-submodules=bs4",
            "--collect-submodules=lxml_html_clean",
            "main.py",
        ]
    )

    print("Build completed!")
    print(f"Executable path: {os.path.abspath('dist/WebMediaParser.exe')}")


if __name__ == "__main__":
    build_exe()
