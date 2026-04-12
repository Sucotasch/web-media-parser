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
            print(f"Cleaning {dir_name}...")
            try:
                shutil.rmtree(dir_name)
            except PermissionError:
                print(f"Warning: Could not remove {dir_name} (in use). Trying to continue anyway...")
            except Exception as e:
                print(f"Warning: Error cleaning {dir_name}: {e}")

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
            "--add-data=resources/domain_blocklist.txt;resources",
            "--add-data=resources/patterns/site_patterns.json;resources/patterns",
            "--hidden-import=PySide6.QtCore",
            "--hidden-import=PySide6.QtGui",
            "--hidden-import=PySide6.QtWidgets",
            "--hidden-import=requests_html",
            "--hidden-import=pyppeteer",
            "--hidden-import=bs4",
            "--hidden-import=lxml.html.clean",
            "--exclude-module=PyQt6",
            "--exclude-module=matplotlib",
            "--exclude-module=numpy",
            "--exclude-module=pandas",
            "--exclude-module=scipy",
            "--exclude-module=torch",
            "--exclude-module=torchvision",
            "--exclude-module=torchaudio",
            "--exclude-module=pygame",
            "--exclude-module=pyarrow",
            "--exclude-module=pytest",
            "--exclude-module=IPython",
            "--exclude-module=notebook",
            "--exclude-module=jupyter",
            "--exclude-module=boto3",
            "--exclude-module=botocore",
            "--collect-submodules=requests_html",
            "--collect-submodules=bs4",
            "--collect-submodules=lxml.html.clean",
            "main.py",
        ]
    )

    print("Build completed!")
    
    # Copy external data files
    print("Copying external data files...")
    for filename in os.listdir("."):
        if filename.startswith("Imagus_sieve") and filename.endswith(".json"):
            src_path = filename
            dst_path = os.path.join("dist", filename)
            shutil.copy2(src_path, dst_path)
            print(f"Copied {src_path} to dist/")

    print(f"Executable path: {os.path.abspath('dist/WebMediaParser.exe')}")


if __name__ == "__main__":
    build_exe()
