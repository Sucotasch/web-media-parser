#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Main window of the application
"""

import os
import time
import logging
import threading
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
from PySide6.QtCore import Qt, QThread, Signal, QSize, QItemSelectionModel, QTimer, QMetaObject
from PySide6.QtGui import QIcon, QDesktopServices, QStandardItemModel, QStandardItem, QColor
import asyncio
from src.gui.settings_dialog import SettingsDialog
from src.parser.parser_manager import ParserManager
from src.gui.log_handler import GUILogHandler
from src.core.task_queue_manager import TaskQueueManager
from src.core.task_item import TaskStatus
from src.server.http_server import ExtensionServer
from src.parser.utils import normalize_url, is_media_url


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

        # Extension API server
        self.extension_server = ExtensionServer()
        self._server_thread = None

        # Initialize UI
        self.init_ui()

        # Initialize logging system
        self.setup_logging()
        self._apply_file_logging()

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

        # Start extension API server
        self._start_extension_server()

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

        # File handler (disabled by default, enabled via settings)
        self._file_handler = None

        # Log initial message
        logging.info("Application started")
        logging.info(
            f"Log level set to: {logging.getLevelName(root_logger.getEffectiveLevel())}"
        )

    def _apply_file_logging(self):
        """Enable or disable file logging based on current settings."""
        root_logger = logging.getLogger()
        settings = self.settings_dialog.get_settings()
        log_to_file = settings.get("log_to_file", False)
        log_file = settings.get("log_file_path", "web_media_parser.log")

        # Remove existing file handler
        if self._file_handler:
            root_logger.removeHandler(self._file_handler)
            self._file_handler.close()
            self._file_handler = None

        # Add new file handler if enabled
        if log_to_file:
            try:
                # Resolve path relative to app directory if not absolute
                if not os.path.isabs(log_file):
                    from src.app_paths import get_app_dir
                    log_file = os.path.join(get_app_dir(), log_file)

                self._file_handler = logging.FileHandler(log_file, encoding="utf-8")
                self._file_handler.setLevel(logging.DEBUG)
                formatter = logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
                )
                self._file_handler.setFormatter(formatter)
                root_logger.addHandler(self._file_handler)
                logging.info(f"File logging enabled: {log_file}")
            except Exception as e:
                logging.error(f"Failed to enable file logging: {e}")

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

        self.one_shot_check = QCheckBox("Page only")
        self.one_shot_check.setToolTip("Download only media from this page, don't follow links")
        self.one_shot_check.setMaximumWidth(80)

        url_layout.addWidget(url_label)
        url_layout.addWidget(self.url_input)
        url_layout.addWidget(self.one_shot_check)
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

        self.clear_history_btn = QPushButton("Clear History")
        self.clear_history_btn.setToolTip("Clear all tasks, session files and download history")
        self.clear_history_btn.clicked.connect(self._clear_download_history)

        self.export_csv_btn = QPushButton("Export CSV")
        self.export_csv_btn.setToolTip("Export task queue to CSV file")
        self.export_csv_btn.clicked.connect(self._export_history_csv)

        self.import_csv_btn = QPushButton("Import CSV")
        self.import_csv_btn.setToolTip("Import tasks from CSV file")
        self.import_csv_btn.clicked.connect(self._import_history_csv)

        qctrl_layout.addWidget(self.move_up_btn)
        qctrl_layout.addWidget(self.move_down_btn)
        qctrl_layout.addStretch()
        qctrl_layout.addWidget(self.remove_task_btn)
        qctrl_layout.addWidget(self.clear_history_btn)
        qctrl_layout.addWidget(self.export_csv_btn)
        qctrl_layout.addWidget(self.import_csv_btn)
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

        task = self.task_queue.add_task(url, settings, download_path, one_shot=self.one_shot_check.isChecked())
        self.log_handler.info(f"Task added: {'[Page only] ' if task.one_shot else ''}{url}")

        self._refresh_task_table()
        self.url_input.clear()
        self.one_shot_check.setChecked(False)  # Auto-reset after adding

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
            url_display = f"↗ {task.url}" if task.one_shot else task.url
            vals = [
                str(i + 1),
                url_display,
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
            (str(idx + 1), None),
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

    def _clear_download_history(self):
        """Clear all tasks, session files and download history."""
        # Confirm with user
        reply = QMessageBox.question(
            self,
            "Clear History",
            "Remove all tasks, session files and download history?\n"
            "Downloaded files will NOT be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Stop active task if any
        if self.task_queue.active_task:
            self.stop_parsing()

        # Clear task queue
        count = len(self.task_queue.queue)
        self.task_queue._queue.clear()
        self.task_queue._active_id = None
        self._refresh_task_table()
        self.update_ui_state(False)
        self.log_handler.info(f"Cleared {count} tasks from queue")

        # Delete task_queue.json
        queue_path = os.path.join(self.download_dir, "task_queue.json")
        try:
            if os.path.exists(queue_path):
                os.remove(queue_path)
                self.log_handler.info("Deleted task_queue.json")
        except OSError as e:
            self.log_handler.error(f"Error deleting task_queue.json: {e}")

        # Delete all session files
        sessions_dir = os.path.join(self.download_dir, "sessions")
        deleted = 0
        if os.path.isdir(sessions_dir):
            import shutil
            try:
                shutil.rmtree(sessions_dir)
                deleted = 1
                self.log_handler.info("Deleted all session files")
            except OSError as e:
                self.log_handler.error(f"Error deleting sessions: {e}")

        self.status_bar.showMessage(f"History cleared ({count} tasks, {deleted} session dirs)")
        self.log_handler.info("Download history cleared")

    def _export_history_csv(self):
        """Export task queue to CSV file."""
        if not self.task_queue.queue:
            QMessageBox.information(self, "Export CSV", "No tasks to export.")
            return

        default_name = f"download_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export History to CSV",
            os.path.join(self.download_dir, default_name),
            "CSV Files (*.csv);;All Files (*)",
        )
        if not file_path:
            return

        try:
            import csv
            with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "#", "ID", "URL", "Status", "Downloaded", "Found",
                    "Created", "Started", "Completed", "Path"
                ])
                for i, task in enumerate(self.task_queue.queue, 1):
                    stats = task.stats
                    found = stats.get("images_found", 0) + stats.get("videos_found", 0)
                    writer.writerow([
                        i,
                        task.id,
                        task.url,
                        task.status.value,
                        stats.get("files_downloaded", 0),
                        found,
                        task.created_at.strftime("%Y-%m-%d %H:%M:%S") if task.created_at else "",
                        task.started_at.strftime("%Y-%m-%d %H:%M:%S") if task.started_at else "",
                        task.completed_at.strftime("%Y-%m-%d %H:%M:%S") if task.completed_at else "",
                        task.download_path,
                    ])

            self.log_handler.info(f"Exported {len(self.task_queue.queue)} tasks to {file_path}")
            self.status_bar.showMessage(f"Exported to {os.path.basename(file_path)}")
        except Exception as e:
            self.log_handler.error(f"Error exporting CSV: {e}")
            QMessageBox.critical(self, "Export Error", f"Failed to export: {e}")

    def _import_history_csv(self):
        """Import tasks from CSV file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Tasks from CSV",
            self.download_dir,
            "CSV Files (*.csv);;All Files (*)",
        )
        if not file_path:
            return

        try:
            import csv
            added = 0
            skipped = 0
            restored = 0
            with open(file_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    url = (row.get("URL") or "").strip()
                    if not url:
                        skipped += 1
                        continue

                    csv_path = (row.get("Path") or "").strip()
                    csv_status = (row.get("Status") or "").strip().lower()

                    # If original path exists with files — restore as completed
                    if csv_path and os.path.isdir(csv_path) and os.listdir(csv_path):
                        settings = self.settings_dialog.get_settings()
                        task = self.task_queue.add_task(url, settings, csv_path)
                        task.mark_completed()
                        restored += 1
                        continue

                    # Otherwise create new folder and queue for download
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    try:
                        domain = url.split("/")[2].replace(".", "_")
                    except IndexError:
                        domain = "unknown"
                    task_folder = f"{domain}_{timestamp}"
                    download_path = os.path.join(self.download_dir, task_folder)
                    os.makedirs(download_path, exist_ok=True)

                    settings = self.settings_dialog.get_settings()
                    self.task_queue.add_task(url, settings, download_path)
                    added += 1

            self._refresh_task_table()
            parts = [f"{added} queued"]
            if restored:
                parts.append(f"{restored} restored")
            if skipped:
                parts.append(f"{skipped} skipped")
            summary = ", ".join(parts)
            self.log_handler.info(f"Imported from {os.path.basename(file_path)}: {summary}")
            self.status_bar.showMessage(f"Imported: {summary}")
        except Exception as e:
            self.log_handler.error(f"Error importing CSV: {e}")
            QMessageBox.critical(self, "Import Error", f"Failed to import: {e}")

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
            # Apply file logging setting
            self._apply_file_logging()

    async def _load_previous_state(self, state_path: str):
        """Load previous session state"""
        if os.path.exists(state_path):
            await self.parser_manager.load_state(state_path)
            self.log_handler.info(f"Loaded previous session state from: {state_path}")

    def start_parsing(self):
        """Start the selected task from the queue, or auto-select the first queued task."""
        task_id = self._get_selected_task_id()
        if not task_id:
            # Auto-select first non-terminal task
            for task in self.task_queue.queue:
                if task.status in (TaskStatus.QUEUED, TaskStatus.PAUSED):
                    task_id = task.id
                    idx = self.task_queue.queue.index(task)
                    self.task_table.selectRow(idx)
                    break
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
        try:
            if pm.loop and not pm.loop.is_closed():
                future = asyncio.run_coroutine_threadsafe(
                    pm.save_state(active.download_path), pm.loop
                )
                future.result(timeout=15)
        except Exception as e:
            self.log_handler.error(f"Error saving state on switch: {e}")

        # 2. Signal stop (non-blocking)
        pm.stop_parsing()

        # 3. Mark Paused
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
            one_shot=task.one_shot,
            pending_downloads=getattr(task, '_pending_downloads', None),
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

        # Resume path
        if selected and selected.status == TaskStatus.PAUSED and not self.task_queue.active_task:
            self._launch_task_from_queue(selected.id)
            return

        if not self.parser_manager:
            return

        active = self.task_queue.active_task
        if not active:
            return

        self.log_handler.info(f"Pausing task {active.id} ({active.url})...")
        self.status_bar.showMessage("Saving state before pausing...")

        # 1. Mark Paused FIRST so other signals (like on_task_ended) know we're pausing
        active.mark_paused()
        self._update_task_row(active.id)

        # 2. Save State
        try:
            if self.parser_manager.loop and not self.parser_manager.loop.is_closed():
                future = asyncio.run_coroutine_threadsafe(
                    self.parser_manager.save_state(active.download_path), self.parser_manager.loop
                )
                future.result(timeout=15)
                self.log_handler.info(f"State saved for {active.id}")
        except Exception as e:
            self.log_handler.error(f"Error saving state on pause: {e}")

        # 3. Signal stop (non-blocking — sets _stop_event via call_soon_threadsafe)
        self.parser_manager.stop_parsing()

        # 4. Update UI immediately — don't block on thread cleanup
        #    on_task_ended() will handle parser_thread cleanup asynchronously
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

    def _start_extension_server(self):
        """Start the HTTP API server for browser extension communication."""
        def add_tasks_from_extension(urls, one_shot=False, user_agent="", cookies=""):
            """Callback: add tasks from extension to queue. Runs in HTTP thread."""
            added = 0
            settings = self.settings_dialog.get_settings()

            # Override settings with browser context from extension
            if user_agent:
                settings["user_agent"] = user_agent
            if cookies:
                settings["extension_cookies"] = cookies

            if one_shot and urls:
                source_page = urls[0].get("source", "") if urls else ""
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                try:
                    domain = source_page.split("/")[2].replace(".", "_") if source_page else "extension"
                except IndexError:
                    domain = "extension"
                task_folder = f"{domain}_{timestamp}"
                download_path = os.path.join(self.download_dir, task_folder)
                os.makedirs(download_path, exist_ok=True)

                task = self.task_queue.add_task(source_page or urls[0]["url"], settings, download_path, one_shot=True)

                items = []
                for item in urls:
                    url = item.get("url", "").strip()
                    if not url or not url.startswith(("http://", "https://", "//")):
                        continue
                    if url.startswith("//"):
                        url = "https:" + url
                    url = normalize_url(url)
                    media_type = item.get("type", "image" if is_media_url(url) else "image")
                    basename = os.path.basename(url.split("?")[0])
                    if not basename or "." not in basename:
                        basename = f"media_{len(items)}.jpg"
                    items.append({
                        "url": url,
                        "source_url": source_page,
                        "media_type": media_type,
                        "original_url": item.get("original_url"),
                        "transformed": item.get("transformed", False),
                        "attrs": {},
                        "filepath": os.path.join(download_path, basename),
                    })

                task._pending_downloads = items
                added = len(items)
                self.log_handler.info(f"One-shot: {added} items -> {task_folder}")
            else:
                for item in urls:
                    url = item.get("url", "").strip()
                    if not url or not url.startswith(("http://", "https://")):
                        continue
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    try:
                        domain = url.split("/")[2].replace(".", "_")
                    except IndexError:
                        domain = "unknown"
                    task_folder = f"{domain}_{timestamp}"
                    download_path = os.path.join(self.download_dir, task_folder)
                    os.makedirs(download_path, exist_ok=True)
                    self.task_queue.add_task(url, settings, download_path)
                    added += 1

            # Update UI on GUI thread
            def _do_update():
                self._refresh_task_table()
                if self.task_queue.queue:
                    self.task_table.selectRow(0)
                    self._update_start_button_state()
            QTimer.singleShot(100, _do_update)
            return {"added": added}

        def get_status():
            active = self.task_queue.active_task
            return {
                "active_task": active.id if active else None,
                "queue_length": len(self.task_queue.queue),
                "files_downloaded": active.stats.get("files_downloaded", 0) if active else 0,
            }

        self.extension_server.set_callbacks(add_tasks_from_extension, get_status)

        def run_server():
            """Run the server in a background thread."""
            import asyncio as aio
            loop = aio.new_event_loop()
            aio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.extension_server.start())
                loop.run_forever()
            except Exception as e:
                logger.error(f"Extension server error: {e}")
            finally:
                try:
                    loop.run_until_complete(self.extension_server.stop())
                except Exception:
                    pass
                loop.close()

        self._server_thread = threading.Thread(target=run_server, name="ExtensionServer", daemon=True)
        self._server_thread.start()

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
            if not pm.is_paused:
                # Save parser state before stopping
                active = self.task_queue.active_task
                if active and pm.loop and not pm.loop.is_closed():
                    self.status_bar.showMessage("Saving state and stopping task...")
                    try:
                        future = asyncio.run_coroutine_threadsafe(
                            pm.save_state(active.download_path), pm.loop
                        )
                        future.result(timeout=5)
                    except Exception as e:
                        self.log_handler.error(f"Error saving state on close: {e}")

            # Stop the task
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

    def _updateUiFromExtension(self):
        """Slot: update task table after extension adds tasks (called via QMetaObject)."""
        self._refresh_task_table()
        if self.task_queue.queue:
            self.task_table.selectRow(0)
            self._update_start_button_state()

    def clear_log(self):
        """
        Clear the log display and history
        """
        if hasattr(self, "log_handler"):
            self.log_handler.clear_history()
