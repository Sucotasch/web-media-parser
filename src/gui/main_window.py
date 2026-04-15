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
    QTableView,
    QHeaderView,
)
from PySide6.QtCore import Qt, QThread, Signal, QSize, QUrl, QEventLoop, QItemSelectionModel, QTimer, QMetaObject
from PySide6.QtGui import QIcon, QDesktopServices, QStandardItemModel, QStandardItem, QColor
import asyncio
from src.gui.settings_dialog import SettingsDialog
from src.parser.parser_manager import ParserManager
from src.gui.log_handler import GUILogHandler
from src.core.task_queue_manager import TaskQueueManager
from src.core.task_item import TaskStatus


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

        # Task queue
        self.task_queue = TaskQueueManager(self.download_dir)

        # Periodic timer to update active task stats during download
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._update_active_task_stats)
        self._stats_timer.start(2000)  # every 2 seconds

        # Set up event loop
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # Initialize UI
        self.init_ui()

        # Initialize logging system
        self.setup_logging()

        # Connect queue signals
        self.task_queue.task_added.connect(self._refresh_task_table)
        self.task_queue.task_removed.connect(self._refresh_task_table)
        self.task_queue.task_status_changed.connect(
            lambda tid, _: self._update_task_row(tid)
        )

        # Load queue state from previous session
        self._load_queue_state()

        # Set initial state
        self.update_ui_state(False)
        self._update_start_button_state()

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
        self.add_task_button = QPushButton("+")
        self.add_task_button.setMaximumWidth(40)
        self.add_task_button.setToolTip("Add URL to task queue")
        self.add_task_button.setStyleSheet(
            "QPushButton { background-color: #2E7D32; color: #FFFFFF; "
            "border-radius: 4px; min-width: 36px; }"
            "QPushButton:hover { background-color: #388E3C; }"
            "QPushButton:pressed { background-color: #1B5E20; }"
        )
        self.add_task_button.clicked.connect(self.add_task_to_queue)
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.url_input)
        url_layout.addWidget(self.add_task_button)
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

        # Task queue section
        queue_group = QGroupBox("Task Queue")
        queue_layout = QVBoxLayout(queue_group)
        queue_layout.setContentsMargins(5, 5, 5, 5)

        self.task_table = QTableView()
        self.task_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.task_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.task_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.task_table.verticalHeader().setVisible(False)
        self.task_table.verticalHeader().setDefaultSectionSize(26)
        self.task_table.setStyleSheet(
            "QTableView { background-color: #1E1E1E; color: #FFFFFF; "
            "gridline-color: #333333; border: 1px solid #444444; }"
            "QTableView::item:selected { background-color: #2E7D32; color: #FFFFFF; }"
            "QHeaderView::section { background-color: #2D2D2D; color: #CCCCCC; "
            "border: none; padding: 4px; font-weight: bold; }"
        )

        self.task_model = QStandardItemModel(0, 5)
        self.task_model.setHorizontalHeaderLabels(
            ["#", "URL", "Status", "Progress", "Found"]
        )
        self.task_table.setModel(self.task_model)

        # Column sizing
        header = self.task_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.task_table.setColumnWidth(0, 30)
        self.task_table.setColumnWidth(2, 80)
        self.task_table.setColumnWidth(3, 90)
        self.task_table.setColumnWidth(4, 60)
        self.task_table.setMinimumHeight(120)

        # Connect selection change to update button state
        self.task_table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        queue_layout.addWidget(self.task_table)

        # Queue control buttons
        qctrl_layout = QHBoxLayout()
        self.move_up_btn = QPushButton("▲")
        self.move_up_btn.setToolTip("Move task up in queue")
        self.move_up_btn.setMaximumWidth(40)
        self.move_up_btn.clicked.connect(self._move_task_up)

        self.move_down_btn = QPushButton("▼")
        self.move_down_btn.setToolTip("Move task down in queue")
        self.move_down_btn.setMaximumWidth(40)
        self.move_down_btn.clicked.connect(self._move_task_down)

        self.remove_task_btn = QPushButton("Remove")
        self.remove_task_btn.setToolTip("Remove selected task from queue")
        self.remove_task_btn.clicked.connect(self._remove_selected_task)

        qctrl_layout.addWidget(self.move_up_btn)
        qctrl_layout.addWidget(self.move_down_btn)
        qctrl_layout.addStretch()
        qctrl_layout.addWidget(self.remove_task_btn)
        queue_layout.addLayout(qctrl_layout)

        main_layout.addWidget(queue_group)

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
            # Persist the change immediately so it's not lost on restart
            if hasattr(self, "settings_dialog"):
                self.settings_dialog.settings["last_download_dir"] = dir_path
                self.settings_dialog.save_settings()


    def add_task_to_queue(self):
        """Add the current URL as a new task in the queue."""
        url = self.url_input.text().strip()
        if not url:
            self.log_handler.error("Please enter a URL to add to queue")
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
            self.url_input.setText(url)

        # Create download folder and fix settings snapshot at add-time
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        domain = url.split("/")[2].replace(".", "_")
        task_folder = f"{domain}_{timestamp}"
        download_path = os.path.join(self.download_dir, task_folder)
        os.makedirs(download_path, exist_ok=True)

        # Snapshot settings NOW so queued tasks are not affected by later changes
        settings = self.settings_dialog.get_settings()

        task = self.task_queue.add_task(url, settings, download_path)
        self.log_handler.info(f"Task added to queue: {task.id}  {url}")

        self._refresh_task_table()
        self.url_input.clear()

    def _refresh_task_table(self, preserve_selection=True, selected_task_id=None):
        """Update the task table, preserving selection when possible."""
        queue = self.task_queue.queue

        # Capture task_id BEFORE any changes
        if selected_task_id is None and preserve_selection:
            rows = self.task_table.selectionModel().selectedRows()
            if rows:
                row = rows[0].row()
                tasks = self.task_queue.queue
                if row < len(tasks):
                    selected_task_id = tasks[row].id

        # Adjust row count to match queue
        current = self.task_model.rowCount()
        target = len(queue)
        if target > current:
            for _ in range(target - current):
                self.task_model.appendRow([QStandardItem("") for _ in range(5)])
        elif target < current:
            self.task_model.removeRows(target, current - target)

        # Update items in-place (selection model stays valid)
        for i, task in enumerate(queue):
            vals = [
                str(i + 1),
                task.url,
                task.status.value.capitalize(),
                f"{task.stats.get('files_downloaded', 0)}",
                f"{task.stats.get('images_found', 0) + task.stats.get('videos_found', 0)}",
            ]
            for col, val in enumerate(vals):
                item = self.task_model.item(i, col)
                if item is None:
                    item = QStandardItem()
                    self.task_model.setItem(i, col, item)
                item.setText(val)
                if task.status == TaskStatus.PAUSED and col in (1, 2):
                    item.setForeground(QColor("#64B5F6" if col == 1 else "#FFD54F"))
                elif col in (1, 2):
                    item.setForeground(QColor("#FFFFFF"))

        # Defer selection until Qt finishes processing model changes
        if selected_task_id:
            for i, task in enumerate(queue):
                if task.id == selected_task_id:
                    QTimer.singleShot(0, lambda row=i: self.task_table.selectRow(row))
                    break

    def _load_queue_state(self):
        """Load task queue from disk and refresh the table."""
        queue_path = os.path.join(self.download_dir, "task_queue.json")
        count = self.task_queue.load(queue_path)
        if count > 0:
            self.log_handler.info(f"Loaded {count} tasks from queue file")
            self._refresh_task_table()

    def _update_active_task_stats(self):
        """Called periodically by QTimer to refresh stats of the active task."""
        if self.parser_manager and self.parser_manager.is_running:
            active = self.task_queue.active_task
            if active:
                stats = self.parser_manager.get_stats()
                active.stats = dict(stats)
                self._update_task_row(active.id)

    def _update_task_row(self, task_id: str):
        """Update a single row for the given task_id."""
        task = self.task_queue.find_task(task_id)
        if task is None:
            return
        idx = next(
            (i for i, t in enumerate(self.task_queue.queue) if t.id == task_id), None
        )
        if idx is None:
            return
        is_paused = task.status == TaskStatus.PAUSED
        text_color = QColor("#64B5F6") if is_paused else None
        status_color = QColor("#FFD54F") if is_paused else None
        cols = [
            str(idx + 1),
            (task.url, text_color),
            (task.status.value.capitalize(), status_color),
            (f"{task.stats.get('files_downloaded', 0)}", None),
            (f"{task.stats.get('images_found', 0) + task.stats.get('videos_found', 0)}", None),
        ]
        for col, (val, color) in enumerate(cols):
            item = self.task_model.item(idx, col)
            if item is not None:
                item.setText(val)
                if color:
                    item.setForeground(color)

    # --- Queue control helpers ---

    def _get_selected_task_id(self) -> str | None:
        """Return the task_id of the selected row, or None."""
        rows = self.task_table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        tasks = self.task_queue.queue
        if row < len(tasks):
            return tasks[row].id
        return None

    def _get_selected_task(self) -> 'TaskItem | None':
        """Return the selected TaskItem or None."""
        tid = self._get_selected_task_id()
        if tid:
            return self.task_queue.find_task(tid)
        return None

    def _update_start_button_state(self):
        """Update Start button text and enabled state based on selection."""
        task = self._get_selected_task()
        if task and task.status == TaskStatus.PAUSED:
            self.start_button.setText("Resume")
            self.start_button.setEnabled(True)
        else:
            self.start_button.setText("Start")
            # Enable if nothing is running
            is_running = self.task_queue.active_task is not None
            self.start_button.setEnabled(not is_running)

    def _on_selection_changed(self):
        """Called when table selection changes."""
        self._update_start_button_state()

    def _move_task_up(self):
        tid = self._get_selected_task_id()
        if tid and self.task_queue.move_task_up(tid):
            self._refresh_task_table(selected_task_id=tid)

    def _move_task_down(self):
        tid = self._get_selected_task_id()
        if tid and self.task_queue.move_task_down(tid):
            self._refresh_task_table(selected_task_id=tid)

    def _remove_selected_task(self):
        tid = self._get_selected_task_id()
        if tid:
            self.task_queue.remove_task(tid)
            self._refresh_task_table()
            self.log_handler.info(f"Task removed from queue: {tid}")

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
        """Start the selected task from the queue, or fall back to URL input."""
        task_id = self._get_selected_task_id()
        if task_id:
            self._launch_task_from_queue(task_id)
        else:
            self._start_from_url_input()

    def _launch_task_from_queue(self, task_id: str):
        """Launch a queued task, pausing the current active one if needed."""
        task = self.task_queue.find_task(task_id)
        if task is None:
            self.log_handler.error("Selected task not found")
            return
        if task.status == TaskStatus.RUNNING:
            return  # already running

        # Pause current active task if different
        if self.task_queue.active_task and self.task_queue.active_task.id != task_id:
            self.log_handler.info(
                f"Pausing active task ({self.task_queue.active_task.id}) to start {task_id}"
            )
            self._pause_current_for_switch()

        # Start the task in the queue manager
        self.task_queue.start_task(task_id)

        self.log_handler.info(f"Starting task {task_id}: {task.url}")
        self._launch_parser_for_task(task)
        self.status_bar.showMessage(f"Parsing started: {task.url}")

    def _pause_current_for_switch(self):
        """Stop active task and save state to allow switching."""
        pm = self.parser_manager
        if pm is None:
            self.task_queue.clear_active()
            return

        active = self.task_queue.active_task
        if not active:
            return

        self.log_handler.info(f"Pausing active task {active.id} to switch...")

        # 1. Save State
        state_path_base = active.download_path
        try:
            if pm.loop and not pm.loop.is_closed():
                future = asyncio.run_coroutine_threadsafe(
                    pm.save_state(state_path_base), pm.loop
                )
                future.result(timeout=15)
        except Exception as e:
            self.log_handler.error(f"Error saving state on switch: {e}")

        # 2. Hard Stop
        pm.stop_parsing()

        # 3. Wait
        if self.parser_thread and self.parser_thread.isRunning():
            self.parser_thread.quit()
            self.parser_thread.wait()

        # 4. Mark Paused
        active.mark_paused()
        self._update_task_row(active.id)
        self.task_queue.clear_active()

    def _launch_parser_for_task(self, task):
        """Create ParserManager + QThread for a TaskItem."""
        self.parser_manager = ParserManager(
            url=task.url,
            download_path=task.download_path,
            settings=task.settings,
            log_handler=self.log_handler,
            task_id=task.id,
        )
        self._connect_parser_signals()

        # State loading is now handled inside ParserManager._main_task()
        # to ensure it runs in the correct asyncio event loop thread.

        self.parser_thread = QThread()
        self.parser_manager.moveToThread(self.parser_thread)
        self.parser_thread.started.connect(self.parser_manager.start_parsing)
        self.parser_thread.start()
        self.update_ui_state(True)

    def _start_from_url_input(self):
        """Legacy behavior: start parsing from the URL input field directly."""
        url = self.url_input.text().strip()
        if not url:
            self.log_handler.error("Please enter a URL to parse")
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
            self.url_input.setText(url)

        # If a task is already running, pause it first
        if self.task_queue.active_task:
            self.log_handler.info("Pausing active task before starting new one")
            self._pause_current_for_switch()

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

        # Add to queue manager for tracking (but as a direct-run task)
        task = self.task_queue.add_task(url, settings, download_path)
        self.task_queue.start_task(task.id)

        # Connect signals
        self._connect_parser_signals()

        # Start parsing thread
        self.parser_thread = QThread()
        self.parser_manager.moveToThread(self.parser_thread)
        self.parser_thread.started.connect(self.parser_manager.start_parsing)
        self.parser_thread.start()

        self.update_ui_state(True)
        self.status_bar.showMessage("Parsing started")

    def _connect_parser_signals(self):
        """Connect ParserManager Qt signals to MainWindow slots."""
        self.parser_manager.total_progress_updated.connect(self.update_total_progress)
        self.parser_manager.current_progress_updated.connect(
            self.update_current_progress
        )
        self.parser_manager.parsing_finished.connect(self.on_parsing_finished)
        self.parser_manager.status_updated.connect(self.update_status)
        self.parser_manager.task_ended.connect(self.on_task_ended)

    def toggle_pause(self):
        """Pause active task or Resume selected task."""
        selected = self._get_selected_task()

        # Check if we are resuming a selected paused task
        if selected and selected.status == TaskStatus.PAUSED and not self.task_queue.active_task:
            self._launch_task(selected)
            return

        # Otherwise, pause the active task
        if not self.parser_manager:
            return

        active = self.task_queue.active_task
        if not active:
            return

        self.log_handler.info(f"Pausing task {active.id}...")
        self.status_bar.showMessage("Saving state before pausing...")

        state_path_base = active.download_path

        # 1. Save State
        try:
            if self.parser_manager.loop and not self.parser_manager.loop.is_closed():
                future = asyncio.run_coroutine_threadsafe(
                    self.parser_manager.save_state(state_path_base), self.parser_manager.loop
                )
                future.result(timeout=15)
                self.log_handler.info(f"State saved for {active.id}")
        except Exception as e:
            self.log_handler.error(f"Error saving state on pause: {e}")

        # 2. Hard Stop
        self.parser_manager.stop_parsing()

        # 3. Wait for thread
        if self.parser_thread and self.parser_thread.isRunning():
            self.parser_thread.quit()
            self.parser_thread.wait()

        # 4. Mark Paused & Update UI
        active.mark_paused()
        self._update_task_row(active.id)
        self.task_queue.clear_active()
        self.update_ui_state(False)
        self.status_bar.showMessage("Task paused")
        self._update_start_button_state()

    def _apply_pause_state(self):
        """Deprecated, keeping for safety but logic moved to toggle_pause."""
        pass

    def stop_parsing(self):
        """
        Stop the parsing process — hard stop for current task only.
        Cleans up partial files and state. Other queue items untouched.
        """
        if not self.parser_manager:
            return

        try:
            self.parser_manager.stop_parsing()
            self.status_bar.showMessage("Stopping task...")
            self.log_handler.info("Stopping task...")

            # Wait for thread to finish
            if self.parser_thread and self.parser_thread.isRunning():
                self.parser_thread.quit()
                self.parser_thread.wait()

            # Clean up: delete partial files and state for the stopped task
            active = self.task_queue.active_task
            if active:
                try:
                    self.task_queue.cleanup_partial_files(active)
                except Exception:
                    pass
                # Remove state file
                state_path = self.task_queue.get_state_file_path(active.id)
                if os.path.exists(state_path):
                    try:
                        os.remove(state_path)
                    except OSError:
                        pass
                active.mark_stopped()
                self._update_task_row(active.id)

        finally:
            self.task_queue.clear_active()
            self.update_ui_state(False)
            self._update_start_button_state()

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

    def update_ui_state(self, is_running, is_paused=False):
        """
        Update UI elements based on parsing state.
        is_paused only matters when is_running=True.
        """
        self.url_input.setEnabled(not is_running)
        self.dir_input.setEnabled(not is_running)
        # Start is enabled when: not running, OR running but paused (to start another task)
        self.start_button.setEnabled(not is_running or is_paused)
        self.pause_button.setEnabled(is_running)
        self.stop_button.setEnabled(is_running)
        self.settings_button.setEnabled(not is_running)

        if is_running and is_paused:
            self.pause_button.setText("Resume")
        elif not is_running:
            self.pause_button.setText("Pause")
            self.total_progress_bar.setValue(0)
            self.current_progress_bar.setValue(0)
            self.status_bar.showMessage("Ready")

    def on_parsing_finished(self):
        """
        Handle parsing finished event (natural completion only).
        """
        self.log_handler.info("Parsing finished")
        self.status_bar.showMessage("Parsing finished")
        # Cleanup
        if self.parser_thread and self.parser_thread.isRunning():
            self.parser_thread.quit()
            self.parser_thread.wait()
        self.update_ui_state(False)
        # Log stats
        if self.parser_manager:
            stats = self.parser_manager.get_stats()
            self.log_handler.info(
                f"Pages processed: {stats['pages_processed']} | "
                f"Images found: {stats['images_found']} | "
                f"Videos found: {stats['videos_found']} | "
                f"Downloaded: {stats['files_downloaded']} | "
                f"Skipped: {stats['files_skipped']}"
            )
            # Update stats on the task in the queue
            active = self.task_queue.active_task
            if active:
                active.stats = dict(stats)
                self._update_task_row(active.id)

    def on_task_ended(self, reason: str):
        """Handle task_ended signal — called for completed, stopped, or failed."""
        self.log_handler.info(f"Task ended: {reason}")
        active = self.task_queue.active_task
        if active:
            # Don't overwrite if manually paused
            if active.status != TaskStatus.PAUSED:
                active.stats = dict(self.parser_manager.get_stats()) if self.parser_manager else active.stats
                if reason == "completed":
                    active.mark_completed()
                elif reason == "stopped":
                    active.mark_stopped()
                elif reason == "failed":
                    active.mark_failed("Critical error during parsing")
                self._update_task_row(active.id)

        # Cleanup thread
        if self.parser_thread and self.parser_thread.isRunning():
            self.parser_thread.quit()
            self.parser_thread.wait()
        self.task_queue.clear_active()
        self.update_ui_state(False)
        self._update_start_button_state()

        # Auto-start next queued task if this one completed naturally
        if reason == "completed":
            self.log_handler.info("Task completed, checking queue for next task...")
            if self.task_queue.start_next():
                next_task = self.task_queue.active_task
                if next_task:
                    self.log_handler.info(f"Auto-starting next task: {next_task.url}")
                    self._launch_parser_for_task(next_task)
                    self.status_bar.showMessage(f"Auto-started: {next_task.url}")

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
        """Handle window close event — save queue and active task state."""
        # Always save the queue state
        queue_path = os.path.join(self.download_dir, "task_queue.json")
        try:
            self.task_queue.save(queue_path)
        except Exception as e:
            self.log_handler.error(f"Error saving queue state: {e}")

        pm = self.parser_manager
        if pm and pm.is_running:
            # If paused, state is already saved. If running, save then stop.
            if not pm.is_paused:
                self.status_bar.showMessage("Stopping task and saving state…")
                loop = QEventLoop()

                async def save_and_stop():
                    await self._save_session_async()
                    self.stop_parsing()
                    loop.quit()

                asyncio.ensure_future(save_and_stop())
                loop.exec_()
            else:
                # Just stop the thread, DO NOT clean up state
                self.status_bar.showMessage("Stopping task…")
                pm.stop_parsing()
                if self.parser_thread and self.parser_thread.isRunning():
                    self.parser_thread.quit()
                    self.parser_thread.wait()
                self.task_queue.clear_active()
                self.update_ui_state(False)

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
