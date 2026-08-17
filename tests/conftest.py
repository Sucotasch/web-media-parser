#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pytest bootstrap (TST-4): guarantee the project root (for `src.*`) and the
tests dir (for `helpers`) are importable regardless of the working directory."""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS = os.path.dirname(os.path.abspath(__file__))
for _p in (_ROOT, _TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
