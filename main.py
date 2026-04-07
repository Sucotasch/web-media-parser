#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Web Media Parser - Application for parsing and downloading media files from websites
Main entry point for the application
"""

import sys
import os
import asyncio
import logging

# --- CRITICAL: Set playwright browser path BEFORE any playwright/scrapling import ---
# In a frozen PyInstaller app, playwright looks for browsers inside the temp extraction
# dir (_MEI*) which doesn't have them. Point it to the user's actual installed browsers.
_playwright_browsers = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ms-playwright"
)
if os.path.exists(_playwright_browsers):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _playwright_browsers
else:
    # Fallback: disable JS processing if no browser is available
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"  # Playwright sentinel for "don't use"

# Apply fixes for library compatibility issues
from src.fix_lxml import LXMLHTMLCleanFix
from src.fix_brotli import BrotliSupportFix

from qasync import QEventLoop

from src.gui.main_window import MainWindow
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, Qt


def main():
    """
    Main entry point. QApplication is created synchronously here,
    then bound to an asyncio event loop via qasync.QEventLoop.

    IMPORTANT: qasync.run() must NOT be used — it internally creates
    its own QApplication instance, which conflicts with ours and raises
    RuntimeError: Please destroy the QApplication singleton...
    """
    # Apply compatibility patches before anything Qt-related
    LXMLHTMLCleanFix.patch()
    BrotliSupportFix.patch()

    # Set up logging
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Qt metadata must be set before QApplication is instantiated
    QCoreApplication.setApplicationName("Web Media Parser")
    QCoreApplication.setOrganizationName("WebMediaParser")
    QCoreApplication.setApplicationVersion("1.0.0")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # --- CORRECT qasync pattern ---
    # 1. Create QApplication synchronously (only one instance, ever)
    app = QApplication(sys.argv)

    # 2. Bind the Qt event loop to asyncio via QEventLoop
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    # 3. Load stylesheet
    base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    stylesheet_path = os.path.join(base_dir, "resources", "dark_theme.qss")
    if os.path.exists(stylesheet_path):
        with open(stylesheet_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    else:
        logging.warning(f"Stylesheet not found at {stylesheet_path}")

    # 4. Create main window and show it (synchronous — safe here)
    window = MainWindow()
    window.show()

    # 5. Run the unified Qt+asyncio event loop
    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
