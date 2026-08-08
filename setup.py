#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Setup script for Web Media Parser

NOTE: requirements.txt is the canonical dependency source (used by the app,
build_exe.py, and README). This file mirrors it so `pip install .` does not
produce a broken environment (previously it listed unused packages and missed
aiohttp/PySide6/filetype).
"""

from setuptools import setup, find_packages

setup(
    name="web_media_parser",
    version="1.0.0",
    description="Web Media Parser - A tool for parsing and downloading media files from websites",
    author="WebMediaParser",
    packages=find_packages(),
    install_requires=[
        "PySide6>=6.5.0",
        "requests>=2.31.0",
        "aiohttp>=3.9.0",
        "aiofiles>=23.2.0",
        "beautifulsoup4>=4.12.0",
        "lxml>=5.0.0",
        "filetype>=1.2.0",
        "chardet>=5.0.0; sys_platform == 'win32'",
        "cchardet>=2.1.7; sys_platform != 'win32'",
        "brotli>=1.1.0",
        "brotlicffi>=1.1.0",
        "certifi>=2024.0.0",
        "cryptography>=43.0.0",
    ],
    entry_points={
        "console_scripts": [
            "web_media_parser=main:main",
        ],
    },
)