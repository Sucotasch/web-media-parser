#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Main window of the application
"""

import os
import time
import logging
from datetime import datetime
from PySide6.QtWidgets import (
    QMainWindow,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QProgressBar,
    QLabel,
    QStatusBar,
    QDialog,
    QFileDialog,
    QMessageBox,
    QCheckBox,
    QGroupBox,
)
from PySide6.QtCore import Qt, QThread, Signal, QSize, QUrl, QEventLoop
from PySide6.QtGui import QIcon, QDesktopServices
import asyncio
from src.gui.settings_dialog import SettingsDialog
from src.parser.parser_manager import ParserManager
from src.gui.log_handler import GUILogHandler


class MainWindow(QMainWindow):
    """
    Main window class for the application
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Web Media Parser")
        self.resize(900, 700)

        # Initialize variables
        self.settings_dialog = SettingsDialog(self)
        # Restore last used download directory from settings
        self.download_dir = self.settings_dialog.get_last_download_dir()
        self.parser_manager = None
        self.parser_thread = None

        # Set up event loop
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # Initialize UI
        self.init_ui()

        # Initialize logging system
        self.setup_logging()

        # Set initial state
        self.update_ui_state(False)

    def setup_logging(self):
        """
        Set up logging configuration
        """
        # Get the root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)

        # Create GUI handler and set its level
        self.log_handler = GUILogHandler(self.log_text)
        self.log_handler.setLevel(logging.DEBUG)

        # Add handler to the root logger
        root_logger.addHandler(self.log_handler)

        # Log initial message
        logging.info("Application started")
        logging.info(
            f"Log level set to: {logging.getLevelName(root_logger.getEffectiveLevel())}"
        )

    def init_ui(self):
        """
        Initialize the user interface
        """
        # Create central widget and layout
        central_widget = QWidget()
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # URL input section
        url_layout = QHBoxLayout()
        url_label = QLabel("URL:")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter URL to parse...")
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.url_input)
        main_layout.addLayout(url_layout)

        # Directory selection section
        dir_layout = QHBoxLayout()
        dir_label = QLabel("Directory:")
        self.dir_input = QLineEdit()
        self.dir_input.setText(self.download_dir)
        self.dir_input.setReadOnly(True)
        dir_button = QPushButton("Browse")
        dir_button.clicked.connect(self.browse_directory)
        dir_layout.addWidget(dir_label)
        dir_layout.addWidget(self.dir_input)
        dir_layout.addWidget(dir_button)
        main_layout.addLayout(dir_layout)

        # Log filter section
        log_filter_group = QGroupBox("Log Filters")
        log_filter_layout = QHBoxLayout()

        # Create checkboxes for each log level
        self.log_level_checks = {}
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            checkbox = QCheckBox(level)
            checkbox.setChecked(True)
            # Set property for styling
            checkbox.setProperty("level", level)
            # Force style update
            checkbox.style().unpolish(checkbox)
            checkbox.style().polish(checkbox)
            checkbox.stateChanged.connect(
                lambda state, lvl=level: self.on_log_filter_changed(lvl, state)
            )
            log_filter_layout.addWidget(checkbox)
            self.log_level_checks[level] = checkbox

        # Add clear log button
        clear_log_button = QPushButton("Clear Log")
        clear_log_button.clicked.connect(self.clear_log)
        log_filter_layout.addWidget(clear_log_button)

        log_filter_group.setLayout(log_filter_layout)
        main_layout.addWidget(log_filter_group)

        # Control buttons section
        buttons_layout = QHBoxLayout()
        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self.start_parsing)
        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_parsing)
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.show_settings)

        buttons_layout.addWidget(self.start_button)
        buttons_layout.addWidget(self.pause_button)
        buttons_layout.addWidget(self.stop_button)
        buttons_layout.addWidget(self.settings_button)
        main_layout.addLayout(buttons_layout)

        # Progress section
        progress_layout = QVBoxLayout()

        # Total progress
        total_progress_layout = QHBoxLayout()
        total_progress_label = QLabel("Total Progress:")
        self.total_progress_bar = QProgressBar()
        total_progress_layout.addWidget(total_progress_label)
        total_progress_layout.addWidget(self.total_progress_bar)
        progress_layout.addLayout(total_progress_layout)

        # Current file progress
        current_progress_layout = QHBoxLayout()
        current_progress_label = QLabel("Current File:")
        self.current_progress_bar = QProgressBar()
        current_progress_layout.addWidget(current_progress_label)
        current_progress_layout.addWidget(self.current_progress_bar)
        progress_layout.addLayout(current_progress_layout)

        main_layout.addLayout(progress_layout)

        # Log section
        log_label = QLabel("Log:")
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        main_layout.addWidget(log_label)
        main_layout.addWidget(self.log_text)

        # Set central widget
        self.setCentralWidget(central_widget)

        # Set status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def browse_directory(self):
        """
        Open file dialog to select download directory
        """
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "Select download directory",
            self.download_dir,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )

        if dir_path:
            self.download_dir = dir_path
            self.dir_input.setText(dir_path)

    def show_settings(self):
        """
        Show settings dialog and update download directory if changed
        """
        old_dir = self.download_dir
        if self.settings_dialog.exec() == QDialog.Accepted:
            # Update download directory if user changed it in settings
            last_dir = self.settings_dialog.get_last_download_dir()
            if last_dir and last_dir != old_dir:
                self.download_dir = last_dir
                self.dir_input.setText(last_dir)

    async def _load_previous_state(self, state_path: str):
        """Load previous session state"""
        if os.path.exists(state_path):
            await self.parser_manager.load_state(state_path)
            self.log_handler.info(f"Loaded previous session state from: {state_path}")

    def start_parsing(self):
        """
        Start the parsing process
        """
        url = self.url_input.text().strip()
        if not url:
            self.log_handler.error("Please enter a URL to parse")
            return

        if not url.startswith(("http://", "https://")):
            url = "https://" + url
            self.url_input.setText(url)

        # Create download folder with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        domain = url.split("/")[2].replace(".", "_")
        task_folder = f"{domain}_{timestamp}"
        download_path = os.path.join(self.download_dir, task_folder)
        os.makedirs(download_path, exist_ok=True)

        self.log_handler.info(f"Starting parsing {url}")
        self.log_handler.info(f"Files will be saved to {download_path}")

        settings = self.settings_dialog.get_settings()
        self.parser_manager = ParserManager(
            url=url,
            download_path=download_path,
            settings=settings,
            log_handler=self.log_handler,
        )

        # Check for previous session state
        state_dir = os.path.join(self.download_dir, "sessions")
        os.makedirs(state_dir, exist_ok=True)
        state_path = os.path.join(state_dir, "last_session.pkl")

        # Create an event loop for the async load if not already running
        if not self.loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._load_previous_state(state_path), self.loop
            )

        # Connect signals
        self.parser_manager.total_progress_updated.connect(self.update_total_progress)
        self.parser_manager.current_progress_updated.connect(
            self.update_current_progress
        )
        self.parser_manager.parsing_finished.connect(self.on_parsing_finished)
        self.parser_manager.status_updated.connect(self.update_status)

        # Start parsing thread
        self.parser_thread = QThread()
        self.parser_manager.moveToThread(self.parser_thread)
        self.parser_thread.started.connect(self.parser_manager.start_parsing)
        self.parser_thread.start()

        self.update_ui_state(True)
        self.status_bar.showMessage("Parsing started")

    def toggle_pause(self):
        """
        Pause or resume the parsing process
        """
        if not self.parser_manager:
            return

        if self.parser_manager.is_paused:
            self.parser_manager.resume_parsing()
            self.pause_button.setText("Pause")
            self.status_bar.showMessage("Parsing resumed")
            self.log_handler.info("Parsing resumed")
        else:
            self.parser_manager.pause_parsing()
            self.pause_button.setText("Resume")
            self.status_bar.showMessage("Parsing paused")
            self.log_handler.info("Parsing paused")

    def stop_parsing(self):
        """
        Stop the parsing process
        """
        if not self.parser_manager:
            return

        self.parser_manager.stop_parsing()
        self.status_bar.showMessage("Parsing stopped")
        self.log_handler.info("Parsing stopped by user")

        # Wait for thread to finish
        if self.parser_thread and self.parser_thread.isRunning():
            self.parser_thread.quit()
            self.parser_thread.wait()

        self.update_ui_state(False)

    def update_total_progress(self, value):
        """
        Update total progress bar
        """
        self.total_progress_bar.setValue(value)

    def update_current_progress(self, value):
        """
        Update current file progress bar
        """
        self.current_progress_bar.setValue(value)

    def update_status(self, message):
        """
        Update status bar message
        """
        self.status_bar.showMessage(message)

    def update_ui_state(self, is_running):
        """
        Update UI elements based on parsing state
        """
        self.url_input.setEnabled(not is_running)
        self.dir_input.setEnabled(not is_running)
        self.start_button.setEnabled(not is_running)
        self.pause_button.setEnabled(is_running)
        self.stop_button.setEnabled(is_running)
        self.settings_button.setEnabled(not is_running)

        if not is_running:
            self.pause_button.setText("Pause")
            self.total_progress_bar.setValue(0)
            self.current_progress_bar.setValue(0)
            self.status_bar.showMessage("Ready")

    def on_parsing_finished(self):
        """
        Handle parsing finished event
        """
        self.log_handler.info("Parsing finished")
        self.status_bar.showMessage("Parsing finished")
        # Cleanup
        if self.parser_thread and self.parser_thread.isRunning():
            self.parser_thread.quit()
            self.parser_thread.wait()
        self.update_ui_state(False)
        # No popup message, just log
        if self.parser_manager:
            stats = self.parser_manager.get_stats()
            self.log_handler.info(
                f"Pages processed: {stats['pages_processed']} | "
                f"Images found: {stats['images_found']} | "
                f"Videos found: {stats['videos_found']} | "
                f"Downloaded: {stats['files_downloaded']} | "
                f"Skipped: {stats['files_skipped']}"
            )

    async def _save_session_async(self):
        """
        Async helper method to save session state
        """
        try:
            state_dir = os.path.join(self.download_dir, "sessions")
            os.makedirs(state_dir, exist_ok=True)
            state_path = os.path.join(state_dir, "last_session.pkl")
            await self.parser_manager.save_state(state_path)
            self.log_handler.info(f"Session state saved to: {state_path}")
        except Exception as e:
            self.log_handler.error(f"Error saving session state: {str(e)}")

    def run_coroutine(self, coroutine):
        """Run a coroutine in the Qt event loop"""
        try:
            future = asyncio.Future()
            asyncio.create_task(self._run_coroutine(coroutine, future))
            return future
        except Exception as e:
            self.log_handler.error(f"Error running coroutine: {str(e)}")
            return None

    async def _run_coroutine(self, coroutine, future):
        """Helper method to run coroutine and set future result"""
        try:
            result = await coroutine
            future.set_result(result)
        except Exception as e:
            future.set_exception(e)

    def closeEvent(self, event):
        """
        Handle window close event and save session state if parsing is active
        """
        if self.parser_manager and self.parser_manager.is_running:
            # Create event loop to run coroutine
            loop = QEventLoop()

            # Create and run coroutine
            async def save_and_stop():
                await self._save_session_async()
                self.stop_parsing()
                loop.quit()

            asyncio.ensure_future(save_and_stop())
            loop.exec_()

        event.accept()

    def get_download_directory(self):
        """
        Return current download directory (for settings persistence)
        """
        return self.download_dir

    def on_log_filter_changed(self, level, state):
        """
        Handle changes in log filter checkboxes
        """
        if hasattr(self, "log_handler"):
            self.log_handler.set_level_visibility(
                level, state == Qt.CheckState.Checked.value
            )

    def clear_log(self):
        """
        Clear the log display and history
        """
        if hasattr(self, "log_handler"):
            self.log_handler.clear_history()
