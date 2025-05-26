#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Web Media Parser - Application for parsing and downloading media files from websites
Main entry point for the application
"""

import sys
import os
import logging

# Apply fixes for library compatibility issues
from src.fix_lxml import LXMLHTMLCleanFix
from src.fix_brotli import BrotliSupportFix

from src.gui.main_window import MainWindow
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, Qt


def main():
    """
    Main entry point for the application
    """
    # Apply fixes before anything else
    LXMLHTMLCleanFix.patch()
    BrotliSupportFix.patch()
    
    # Set up logging
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Set application information before creating QApplication
    QCoreApplication.setApplicationName("Web Media Parser")
    QCoreApplication.setOrganizationName("WebMediaParser")
    QCoreApplication.setApplicationVersion("1.0.0")

    # Configure High DPI settings before creating QApplication
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # Create application
    app = QApplication(sys.argv)

    # Set stylesheet
    # Get base directory (works for both normal run and PyInstaller bundle)
    base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    stylesheet_path = os.path.join(base_dir, "resources", "dark_theme.qss")
    
    if os.path.exists(stylesheet_path):
        with open(stylesheet_path, "r") as f:
            app.setStyleSheet(f.read())
    else:
        logging.warning(f"Stylesheet not found at {stylesheet_path}")

    # Create and show main window
    window = MainWindow()
    window.show()

    # Run application
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
