#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Custom log handler for GUI with filtering support
"""

import logging
from datetime import datetime
from PySide6.QtWidgets import QTextEdit
from PySide6.QtCore import Qt, QMetaObject, Signal, Slot, QObject


class LogFilter:
    """
    Filter for log messages based on log levels
    """
    def __init__(self):
        self.enabled_levels = {
            'DEBUG': True,
            'INFO': True,
            'WARNING': True,
            'ERROR': True,
            'CRITICAL': True
        }

    def is_enabled(self, level):
        """Check if the given log level is enabled"""
        return self.enabled_levels.get(level, True)

    def set_level_enabled(self, level, enabled):
        """Enable or disable a specific log level"""
        self.enabled_levels[level] = enabled


class GUILogHandler(QObject, logging.Handler):
    """
    Custom log handler that outputs to QTextEdit and supports standard Python logging
    """

    log_signal = Signal(str, str)
    clear_signal = Signal()

    def __init__(self, text_edit, level=logging.NOTSET):
        QObject.__init__(self)
        logging.Handler.__init__(self, level)
        self.text_edit = text_edit
        self.text_edit.setReadOnly(True)
        self.text_edit.setAcceptRichText(True)
        self.log_signal.connect(self._append_log)
        self.clear_signal.connect(self._clear_log)
        
        # Initialize log filter
        self.log_filter = LogFilter()

        # Set up formatter
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        self.setFormatter(formatter)

        # Store filtered messages for reapplying filters
        self.message_history = []

    def emit(self, record):
        """
        Emit a record.
        Handle the record by formatting it and sending it to the GUI.
        """
        try:
            msg = self.format(record)
            # Store message in history
            self.message_history.append((record.levelname, msg))
            # Only display if the level is enabled
            if self.log_filter.is_enabled(record.levelname):
                self.log_signal.emit(record.levelname, msg)
        except Exception:
            self.handleError(record)

    def set_level_visibility(self, level, visible):
        """
        Set visibility for a specific log level and reapply filters
        """
        self.log_filter.set_level_enabled(level, visible)
        self.reapply_filters()

    def reapply_filters(self):
        """
        Clear the log and reapply all filters to message history
        """
        self.clear_signal.emit()
        for level, msg in self.message_history:
            if self.log_filter.is_enabled(level):
                self.log_signal.emit(level, msg)

    def clear_history(self):
        """
        Clear message history and log display
        """
        self.message_history.clear()
        self.clear_signal.emit()

    @Slot()
    def _clear_log(self):
        """
        Clear the log display
        """
        self.text_edit.clear()

    def debug(self, message):
        """
        Log debug message
        """
        logging.getLogger().debug(message)

    def info(self, message):
        """
        Log info message
        """
        logging.getLogger().info(message)

    def warning(self, message):
        """
        Log warning message
        """
        logging.getLogger().warning(message)

    def error(self, message):
        """
        Log error message
        """
        logging.getLogger().error(message)

    @Slot(str, str)
    def _append_log(self, level, message):
        """
        Append log message to text edit with color coding and auto-scroll
        """
        # Define colors for different log levels
        colors = {
            "DEBUG": "#808080",    # Gray
            "INFO": "#FFFFFF",     # White
            "WARNING": "#FFA500",  # Orange
            "ERROR": "#FF0000",    # Red
            "CRITICAL": "#FF00FF", # Magenta
            "NOTSET": "#CCCCCC"    # Light Gray
        }

        # Format message with HTML color
        color = colors.get(level, "#FFFFFF")
        formatted_message = f'<span style="color: {color}; font-family: monospace;">{message}</span>'

        # Append message to text edit
        self.text_edit.append(formatted_message)

        # Auto-scroll to bottom
        scrollbar = self.text_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
