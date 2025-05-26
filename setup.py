#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Setup script for Web Media Parser
"""

from setuptools import setup, find_packages

setup(
    name="web_media_parser",
    version="1.0.0",
    description="Web Media Parser - A tool for parsing and downloading media files from websites",
    author="WebMediaParser",
    packages=find_packages(),
    install_requires=[
        "PySide6>=6.4.0",
        "requests>=2.28.0",
        "requests-html>=0.10.0",
        "beautifulsoup4>=4.11.0",
        "html5lib>=1.1",
        "Pillow>=9.0.0"
    ],
    entry_points={
        "console_scripts": [
            "web_media_parser=main:main",
        ],
    },
)