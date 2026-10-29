#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Web Media Parser - Application for parsing and downloading media files from websites
Main entry point for the application
"""

import sys
import os
import logging
import traceback

# Apply fixes for library compatibility issues
from src.fix_lxml import LXMLHTMLCleanFix
from src.fix_brotli import BrotliSupportFix

from src.app_paths import get_app_dir, get_resource_dir
from src.gui.main_window import MainWindow
from src import constants as K
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QCoreApplication, Qt, QLockFile


def _report_startup_failure(message: str) -> None:
    """X-02: a windowed build has no stderr — leave a file AND a dialog."""
    logging.exception(message)
    try:
        with open(os.path.join(get_app_dir(), "startup_error.log"), "a",
                  encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
    except OSError:
        pass
    try:
        QMessageBox.critical(None, "Web Media Parser", message)
    except Exception:
        pass


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
    QCoreApplication.setApplicationVersion(K.APP_VERSION)

    # Configure High DPI settings before creating QApplication
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # Create application
    app = QApplication(sys.argv)

    # C-08: two instances used to autosave the SAME task_queue.json every 2 s and
    # silently overwrite each other's queue. Lock next to the exe (portable-aware);
    # a stale lock from a crashed run is reclaimed by QLockFile.
    lock = QLockFile(os.path.join(get_app_dir(), "app.lock"))
    if not lock.tryLock(100):
        if lock.error() == QLockFile.LockError.LockFailedError:
            QMessageBox.warning(
                None, "Web Media Parser",
                "Приложение уже запущено. Закройте другой экземпляр и попробуйте снова.")
            return 1
        # Read-only directory / unknown: fail open rather than block the app.
        logging.warning(f"Could not create the single-instance lock: {lock.error()}")

    try:
        # Set stylesheet from bundled resources
        stylesheet_path = os.path.join(get_resource_dir(), "dark_theme.qss")
        if os.path.exists(stylesheet_path):
            with open(stylesheet_path, "r", encoding="utf-8") as f:
                app.setStyleSheet(f.read())
        else:
            logging.warning(f"Stylesheet not found at {stylesheet_path}")

        # Create and show main window
        window = MainWindow()
        window.show()

        # Run application
        rc = app.exec()
    except SystemExit:
        raise
    except Exception:
        _report_startup_failure(
            "Критическая ошибка при запуске приложения. Подробности — в startup_error.log.")
        rc = 1
    finally:
        lock.unlock()

    return rc


if __name__ == "__main__":
    sys.exit(main())
