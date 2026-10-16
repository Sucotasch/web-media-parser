#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Main window of the application
"""

import os
import logging
import re
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
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QStandardItemModel, QStandardItem, QColor
import asyncio
from src.gui.settings_dialog import SettingsDialog
from src.parser.parser_manager import ParserManager
from src.gui.log_handler import GUILogHandler
from src.core.task_queue_manager import TaskQueueManager
from src.core.task_item import TaskStatus, TaskItem
from src.server.http_server import ExtensionServer
from src.parser.utils import normalize_url
from src.app_paths import queue_path

logger = logging.getLogger(__name__)

# Live s8 finding (2026-10-15): a NETLOC is not a filename. The task folder was
# built from `url.split("/")[2]`, so a page on a port produced
# `127_0_0_1:9811_20261015_193650` — ':' is illegal in a Windows path, and
# `os.makedirs` raised NotADirectoryError (WinError 267) inside
# `_apply_extension_task_payload`. The payload had already been drained, so the
# task simply vanished while the extension got `ok: true`. Strip the port (and
# any userinfo/IPv6 brackets) and neutralise the reserved characters.
_ILLEGAL_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_domain_folder(netloc: str, fallback: str) -> str:
    """Turn a URL netloc into a path-safe, stable folder component."""
    host = (netloc or "").rsplit("@", 1)[-1]          # user:pass@host -> host
    if host.startswith("["):                          # [::1]:8080 -> ::1
        host = host[1:host.index("]")] if "]" in host[1:] else host.lstrip("[")
    else:
        host = host.split(":", 1)[0]                 # host:port -> host
    host = _ILLEGAL_PATH_CHARS.sub("_", host).replace(".", "_")
    host = host.strip(" _")
    return host or fallback


# s8 guard: a folder name Windows refuses must be caught while the payload is
# still pure — a task materialized later fails silently (NF-11). Today the
# sanitizer cannot produce such a name, so this cannot fire; it is here so a
# future regression surfaces as an honest "nothing added" instead of a task
# that vanishes after the HTTP reply said ok:true.
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _folder_name_is_safe(name: str) -> bool:
    """True when `name` is a legal, unambiguous Windows path component."""
    if not name or name != name.strip(" .") or _ILLEGAL_PATH_CHARS.search(name):
        return False
    return name.split(".", 1)[0].upper() not in _WINDOWS_RESERVED_NAMES


def apply_extension_payloads(payloads, apply_one, on_error):
    """s8: materialize every buffered extension payload independently.

    The payloads are drained by the GUI thread and each one creates a folder and
    a queue entry. Before this, ANY exception inside one payload propagated out
    of the Qt slot: the remaining payloads were lost with it (they had already
    been drained), and the only trace was a PySide6 traceback on stderr —
    invisible in a --windowed build, while the extension had been told ok:true
    (NF-11). One bad payload must not take the batch down.

    Returns `(payload, result)` pairs for the payloads that succeeded (the
    caller still needs the payload to know whether it was a one-shot);
    `on_error(payload, exc)` is called once per failure.
    """
    applied = []
    for payload in payloads:
        try:
            applied.append((payload, apply_one(payload)))
        except Exception as e:  # noqa: BLE001 — isolation is the point of this loop
            try:
                on_error(payload, e)
            except Exception:
                logger.exception("extension payload error handler failed")
    return applied


def build_extension_task_settings(base_settings, user_agent="", cookies=""):
    """A-2: one-shot settings for an extension task.

    get_settings() returns the dialog's LIVE dict; injecting one-shot extension
    UA/cookies directly into it leaked the values into every subsequent task
    and into settings.json on next save. Returns an independent copy with the
    overrides applied — the original dict is never mutated.
    """
    settings = dict(base_settings)
    if user_agent:
        settings["user_agent"] = user_agent
    if cookies:
        settings["extension_cookies"] = cookies
    return settings


def build_extension_task_payload(urls, settings, download_dir, one_shot=False, timestamp=None):
    """A-5: pure payload computation for extension task creation.

    Runs on the HTTP server thread; performs NO filesystem or queue mutations
    (TaskQueueManager is GUI-thread-only by contract). The returned dict is
    materialized by MainWindow._apply_extension_task_payload on the GUI thread.
    Returns None when nothing valid was requested.
    """
    if not urls:
        return None
    if one_shot:
        # EXT-4: the popup sends the page as `referer` (scan results use
        # `source` for ORIGIN — two meanings on one key).
        source_page = (urls[0].get("referer") or urls[0].get("source") or "")
        ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            domain = _safe_domain_folder(source_page.split("/")[2], "extension") if source_page else "extension"
        except IndexError:
            domain = "extension"
        task_folder = f"{domain}_{ts}"
        if not _folder_name_is_safe(task_folder):
            logger.error(f"Refusing an uncreatable task folder name: {task_folder!r}")
            return None
        download_path = os.path.join(download_dir, task_folder)
        items = []
        for item in urls:
            url = item.get("url", "").strip()
            if not url or not url.startswith(("http://", "https://", "//")):
                continue
            if url.startswith("//"):
                url = "https:" + url
            url = normalize_url(url)
            # GUI-9: the old ternary had "image" on both branches — dead conditional
            media_type = item.get("type", "image")
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
        if not items:
            return None
        return {
            "one_shot": True,
            "task_url": source_page or urls[0]["url"],
            "settings": settings,
            "download_path": download_path,
            "task_folder": task_folder,
            "items": items,
            "added": len(items),
        }
    tasks = []
    # One timestamp for the whole batch (BUG-2 note): same-second folder names
    # per domain are intentional — legacy behavior kept, per-item timestamps
    # only differed when requests crossed a second boundary mid-batch.
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    for item in urls:
        url = item.get("url", "").strip()
        if not url or not url.startswith(("http://", "https://")):
            continue
        try:
            domain = _safe_domain_folder(url.split("/")[2], "unknown")
        except IndexError:
            domain = "unknown"
        folder = f"{domain}_{ts}"
        if not _folder_name_is_safe(folder):
            logger.error(f"Skipping an uncreatable task folder name: {folder!r}")
            continue
        tasks.append((url, settings, os.path.join(download_dir, folder)))
    if not tasks:
        return None
    return {"one_shot": False, "tasks": tasks, "added": len(tasks)}


class MainWindow(QMainWindow):
    """
    Main window class for the application
    """

    # GUI-1: emitted from the extension HTTP server thread to run UI work on
    # the GUI thread — that thread has no Qt event loop, so QTimer.singleShot
    # created there never fired reliably. Payload: id of the one-shot task to
    # auto-start, or "" when the task table should just refresh.
    extension_tasks_added = Signal(str)

    # C-01: emitted from the extension HTTP server thread when the API server
    # could not start (port 19876 busy). Payload: the error text.
    extension_server_failed = Signal(str)

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

        # A-5: payloads computed on the HTTP thread, materialized on the GUI
        # thread (see _updateUiFromExtension). C-08: the list is written by the
        # HTTP thread and drained by the GUI thread, so it has its own lock —
        # the swap in _drain_pending_extension_payloads must not race an append.
        self._pending_extension_payloads = []
        self._payload_lock = threading.Lock()

        # Periodic timer to update active task stats during download
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._update_active_task_stats)
        self._stats_timer.start(2000)  # every 2 seconds

        # Note: no GUI-thread event loop is created here anymore. All async work
        # runs in ParserManager's own loop (pm.loop) or the extension server's
        # thread-local loop; the removed run_coroutine helpers were the only
        # consumers of a MainWindow-level loop.

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

        # C-09: persist queue changes as they happen. task_queue.json used to be
        # written only on a clean window close, so a crash / TaskKill / "End
        # task" lost every task added since startup (and orphaned the per-task
        # download folders already on disk). Writes are debounced onto the
        # existing 2 s stats timer instead of one file write per signal.
        self._queue_dirty = False
        self.task_queue.task_added.connect(self._mark_queue_dirty)
        self.task_queue.task_removed.connect(self._mark_queue_dirty)
        self.task_queue.task_status_changed.connect(self._mark_queue_dirty)

        # GUI-1: marshals UI updates from the extension HTTP server thread.
        self.extension_tasks_added.connect(self._updateUiFromExtension)
        # C-01: report an extension-server start failure on the GUI thread.
        self.extension_server_failed.connect(self._on_extension_server_failed)

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
        count = self.task_queue.load(queue_path())
        # C-09: loading emits the queue signals (add_task per item); the state we
        # just read is by definition already on disk, so don't rewrite it.
        self._queue_dirty = False
        if count > 0:
            self.log_handler.info(f"Loaded {count} tasks from queue file")
            self._refresh_task_table()

    def _mark_queue_dirty(self, *_args):
        """C-09: note that task_queue.json needs a write (debounced)."""
        self._queue_dirty = True

    def _autosave_queue(self):
        """C-09: write task_queue.json if anything changed since the last write.

        Called from the 2 s stats timer; `save()` is atomic (temp + replace).
        """
        if not self._queue_dirty:
            return
        self._queue_dirty = False
        try:
            self.task_queue.save(queue_path())
        except Exception as e:
            self._queue_dirty = True  # retry on the next tick
            self.log_handler.error(f"Error autosaving queue state: {e}")

    def _on_extension_server_failed(self, message: str):
        """C-01: the extension API server could not bind its port.

        Without this the window looked healthy while every extension request
        failed with "Desktop app not reachable" — the failure was only a
        logger.error in a background thread.
        """
        text = (f"Extension API server failed to start on port "
                f"{self.extension_server.port}.\n\n{message}\n\n"
                f"Another instance of Web Media Parser is probably already "
                f"running — the browser extension will talk to that one.")
        self.log_handler.error(text.replace("\n", " "))
        self.status_bar.showMessage("Extension API unavailable (port busy)")
        QMessageBox.warning(self, "Extension API unavailable", text)

    def _update_active_task_stats(self):
        """Called periodically by QTimer to refresh stats of the active task."""
        self._autosave_queue()  # C-09: debounced queue persistence
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

        # A-5 follow-up: drop any unmaterialized extension payloads — the user
        # just asked to clear everything.
        with self._payload_lock:
            self._pending_extension_payloads = []

        # Clear task queue.
        # F-02: this used to clear the manager's private queue list and null its
        # private active-id field directly, bypassing clear_active() — the only
        # path that emits active_task_changed — so every consumer of that signal
        # kept a stale active task.
        count = len(self.task_queue.queue)
        if self.task_queue.active_task is not None:
            self.task_queue.clear_active()
        for task_id in [t.id for t in self.task_queue.queue]:
            self.task_queue.remove_task(task_id)
        self._refresh_task_table()
        self.update_ui_state(False)
        self.log_handler.info(f"Cleared {count} tasks from queue")

        # Delete task_queue.json (canonical path next to the exe)
        qpath = queue_path()
        try:
            if os.path.exists(qpath):
                os.remove(qpath)
                self.log_handler.info("Deleted task_queue.json")
        except OSError as e:
            self.log_handler.error(f"Error deleting task_queue.json: {e}")

        # Delete all session files — A-4: per-task layout is
        # {download_dir}/{task_folder}/sessions/{task_id}/, so walk task
        # folders (the old code removed a nonexistent top-level dir).
        from src.app_paths import clear_task_sessions
        deleted = clear_task_sessions(self.download_dir)
        if deleted:
            self.log_handler.info(f"Deleted {deleted} task session dir(s)")

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

        # 1. Mark PAUSED FIRST — so on_task_ended sees PAUSED and doesn't overwrite
        active.mark_paused()
        self._update_task_row(active.id)

        # 2. C-07: save then stop, both on the parser loop (the GUI thread used
        #    to block here for up to 15 s before the switch could proceed).
        self._save_then_stop(pm, active.download_path)

        # 4. Free active slot
        self.task_queue.clear_active()

    def _launch_parser_for_task(self, task):
        """Create ParserManager + QThread for a TaskItem."""
        # Disconnect old parser signals to prevent old task_ended from corrupting new task
        old_pm = getattr(self, 'parser_manager', None)
        if old_pm is not None:
            try:
                old_pm.task_ended.disconnect(self.on_task_ended)
            except (RuntimeError, TypeError):
                pass  # signal not connected

        # GUI-2/C-06: make sure the previous parser thread is truly dead before
        # starting a new one, and hand the old wrapper to Qt BEFORE dropping our
        # reference. If wait() times out the thread is still running, and letting
        # the Python wrapper be collected would destroy a live QThread ("QThread:
        # Destroyed while thread is still running"). deleteLater() fires only
        # after finished(), so the wrapper outlives the thread either way.
        old_thread = self.parser_thread
        self.parser_thread = None
        if old_thread is not None:
            old_thread.finished.connect(old_thread.deleteLater)
            if old_thread.isRunning():
                old_thread.quit()
                if not old_thread.wait(5000):
                    self.log_handler.warning("Old parser thread did not stop in 5s")

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
        one_shot = self.one_shot_check.isChecked()

        # Add to queue manager for tracking, then launch through the SHARED
        # path (GUI-2 п.3): the URL-input flow must build ParserManager/QThread
        # exactly like queue-started tasks — the old hand-rolled construction
        # duplicated the lifecycle and skipped the old-thread teardown.
        task = self.task_queue.add_task(url, settings, download_path, one_shot=one_shot)
        self._launch_task_from_queue(task.id)

        self.status_bar.showMessage("Parsing started")
        self.one_shot_check.setChecked(False)

    def _connect_parser_signals(self):
        """Connect ParserManager Qt signals to MainWindow slots."""
        self.parser_manager.total_progress_updated.connect(self.update_total_progress)
        self.parser_manager.current_progress_updated.connect(
            self.update_current_progress
        )
        self.parser_manager.parsing_finished.connect(self.on_parsing_finished)
        self.parser_manager.status_updated.connect(self.update_status)
        self.parser_manager.task_ended.connect(self.on_task_ended)

    def _save_then_stop(self, pm, download_path: str) -> None:
        """C-07: save the session on the parser loop, THEN stop the task.

        The pause path used to be `run_coroutine_threadsafe(...).result(timeout=15)`
        executed ON the GUI thread — measured 5.47 s of a frozen window while a
        download was active, which is what "Pause" must never do.

        The save still has to finish before the loop dies: an abandoned
        `state.pkl` cannot be resumed, so the stop is chained AFTER it inside
        the loop instead of being fired side by side from the GUI thread. This
        returns immediately; `task_ended` reports the actual end.
        """
        if pm is None:
            return
        loop = getattr(pm, "loop", None)
        if loop is None or loop.is_closed():
            # No loop to save on (already torn down) — just stop.
            pm.stop_parsing()
            return

        async def _save_then_stop_on_loop() -> None:
            try:
                await pm.save_state(download_path)
            except Exception as e:
                # The task still has to stop (otherwise Pause leaves it
                # running), but the user must see that the session is lost.
                self.log_handler.error(f"Error saving state on pause: {e}")
                logger.error(f"Error saving state on pause: {e}", exc_info=True)
            finally:
                pm.stop_parsing()

        asyncio.run_coroutine_threadsafe(_save_then_stop_on_loop(), loop)

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

        # 2. C-07: hand the save (and the stop that follows it) to the parser
        #    loop — the GUI thread no longer waits for it.
        self._save_then_stop(self.parser_manager, active.download_path)

        # 3. Update UI immediately — don't block on thread cleanup.
        #    on_task_ended() sees the PAUSED status, so it will not overwrite it,
        #    and it does the parser_thread cleanup asynchronously.
        self.task_queue.clear_active()
        self.update_ui_state(False)
        self.status_bar.showMessage("Pausing… saving state in background")
        self._update_start_button_state()

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

            # GUI-3: wait for the parser thread to actually stop before cleaning
            # up — on Windows, deleting files still being written fails, and new
            # .part files appear after the sweep otherwise.
            if self.parser_thread and self.parser_thread.isRunning():
                self.parser_thread.quit()
                if not self.parser_thread.wait(10000):
                    self.log_handler.warning("Parser thread did not stop within 10s")

            # Clean up: delete partial files and state for the stopped task
            active = self.task_queue.active_task
            if active:
                try:
                    self.task_queue.cleanup_partial_files(active)
                except Exception:
                    pass
                # Remove state file
                state_path = self.task_queue.get_state_file_path(active)
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
        # Cleanup — A-7: bounded wait; an unbounded wait on the GUI thread
        # freezes the window if the QThread is stuck in a blocking call.
        if self.parser_thread and self.parser_thread.isRunning():
            self.parser_thread.quit()
            if not self.parser_thread.wait(10000):
                self.log_handler.warning("Parser thread did not stop within 10s")
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

    def on_task_ended(self, task_id: str, reason: str):
        """Handle task_ended signal — called for completed, stopped, or failed.

        GUI-2: the signal now carries the task id; a late signal from a
        PREVIOUS task (rapid pause→start) must not corrupt the new active task.
        """
        active = self.task_queue.active_task
        if active and task_id and active.id != task_id:
            self.log_handler.info(f"Ignoring task_ended for stale task {task_id} (active: {active.id})")
            return
        self.log_handler.info(f"Task ended: {reason}")
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

        # Cleanup thread — A-7: bounded wait (see on_parsing_finished).
        if self.parser_thread and self.parser_thread.isRunning():
            self.parser_thread.quit()
            if not self.parser_thread.wait(10000):
                self.log_handler.warning("Parser thread did not stop within 10s")
        self.task_queue.clear_active()
        self.update_ui_state(False)
        self._update_start_button_state()

        # Auto-start next queued task if this one completed naturally.
        # GUI-9: pass the finished task's id so the queue advances DOWNWARD
        # from it — tasks placed above keep their state.
        if reason == "completed":
            self.log_handler.info("Task completed, checking queue for next task...")
            if self.task_queue.start_next(active.id if active else None):
                next_task = self.task_queue.active_task
                if next_task:
                    self.log_handler.info(f"Auto-starting next task: {next_task.url}")
                    self._launch_parser_for_task(next_task)
                    self.status_bar.showMessage(f"Auto-started: {next_task.url}")

    def _drain_pending_extension_payloads(self) -> list:
        """C-08: atomically take the payloads buffered by the HTTP thread.

        The old `payloads, self._pending_extension_payloads = (list, [])`
        tuple swap could drop an append that landed between the read and the
        rebind, and a payload dropped at window close was never materialized.
        """
        with self._payload_lock:
            payloads, self._pending_extension_payloads = (
                self._pending_extension_payloads, [])
        return payloads

    def _start_extension_server(self):
        """Start the HTTP API server for browser extension communication."""
        def add_tasks_from_extension(urls, one_shot=False, user_agent="", cookies=""):
            """Callback: add tasks from extension to queue. Runs in HTTP thread."""
            auto_start_id = ""  # one-shot task to auto-start on the GUI thread (GUI-1)
            # A-2: build an independent copy — the dialog's live settings dict
            # must never be mutated from the HTTP thread.
            settings = build_extension_task_settings(
                self.settings_dialog.get_settings(), user_agent, cookies
            )

            # A-5: compute the payload without touching the queue or the
            # filesystem (TaskQueueManager is GUI-thread-only by contract);
            # materialization happens in _apply_extension_task_payload.
            payload = build_extension_task_payload(
                urls, settings, self.download_dir, one_shot=one_shot)
            if payload is None:
                # s8: nothing usable — do NOT claim success. The handler merges
                # this dict over its own {"ok": True}, and the popup already
                # renders resp.error; before this the badge said "✓ Sent to app"
                # for a request that queued nothing.
                logger.warning(f"Extension request produced no task ({len(urls)} item(s))")
                return {"ok": False, "added": 0, "error": "Nothing usable to send"}
            with self._payload_lock:
                self._pending_extension_payloads.append(payload)

            # GUI-1/A-5: hand the payload to the GUI thread via a signal. The
            # aiohttp worker thread has no Qt event loop and must not mutate
            # TaskQueueManager directly.
            self.extension_tasks_added.emit(auto_start_id)
            return {"added": payload["added"]}

        def get_status():
            # C-08: this runs on the HTTP thread while the GUI thread mutates
            # the queue. Three separate reads used to mix states (queue length
            # from before an insert, active task from after a start); the
            # manager's lock gives one consistent snapshot instead.
            snap = self.task_queue.snapshot()
            return {
                "active_task": snap["active_task_id"],
                "queue_length": snap["queue_length"],
                "files_downloaded": snap["files_downloaded"],
            }

        self.extension_server.set_callbacks(add_tasks_from_extension, get_status)

        def run_server():
            """Run the server in a background thread."""
            import asyncio as aio
            loop = aio.new_event_loop()
            aio.set_event_loop(loop)
            self._server_loop = loop  # GUI-5: so closeEvent can stop it gracefully
            try:
                loop.run_until_complete(self.extension_server.start())
                loop.run_forever()
            except Exception as e:
                # C-01: surface the failure on the GUI thread instead of dying
                # quietly in this background thread.
                logger.error(f"Extension server error: {e}", exc_info=True)
                self.extension_server_failed.emit(
                    self.extension_server.start_error or str(e)
                )
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
        # A-5 follow-up: materialize any extension payloads still buffered on
        # the HTTP thread BEFORE saving the queue — otherwise tasks requested
        # seconds before shutdown are silently lost.
        try:
            for payload in self._drain_pending_extension_payloads():
                self._apply_extension_task_payload(payload)
        except Exception as e:
            self.log_handler.error(f"Error materializing extension payloads on close: {e}")

        # Always save the queue state (canonical path next to the exe)
        try:
            self.task_queue.save(queue_path())
        except Exception as e:
            self.log_handler.error(f"Error saving queue state: {e}")

        pm = self.parser_manager
        if pm and pm.is_running:
            if not pm.is_paused:
                # Save parser state before stopping
                active = self.task_queue.active_task
                if active and pm.loop and not pm.loop.is_closed():
                    self.status_bar.showMessage("Saving state and stopping task...")
                    # C-07 note: unlike Pause, closing the window MAY block —
                    # the process is about to exit, so a background save would
                    # simply be lost. The wait is bounded (5 s).
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
                # GUI-9: bounded wait — a stuck worker must not freeze window close
                if not self.parser_thread.wait(10000):
                    self.log_handler.warning("Parser thread did not stop within 10s")
            self.task_queue.clear_active()
            self.update_ui_state(False)

        # GUI-5: stop the extension server gracefully instead of hard-killing the
        # daemon thread (the graceful stop() code was otherwise dead).
        is_server_alive = (
            self._server_thread.is_alive()
            if hasattr(self._server_thread, "is_alive")
            else self._server_thread.isRunning()
        ) if self._server_thread else False
        if is_server_alive:
            try:
                loop = getattr(self, "_server_loop", None)
                if loop is not None and not loop.is_closed():
                    asyncio.run_coroutine_threadsafe(
                        self.extension_server.stop(), loop
                    ).result(timeout=3)
                    loop.call_soon_threadsafe(loop.stop)
                self._server_thread.join(timeout=3)
            except Exception as e:
                self.log_handler.error(f"Error stopping extension server: {e}")

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

    def _updateUiFromExtension(self, task_id=""):
        """Slot (GUI thread): materialize buffered extension payloads (add_task
        + download-dir creation — A-5: queue mutations are GUI-thread-only),
        auto-start an extension one-shot task if the queue is idle, then
        refresh the task table (GUI-1 — connected to extension_tasks_added,
        emitted from the HTTP server thread)."""
        def _on_payload_error(payload, exc):
            # s8: the payload is gone by now (it was drained), so this message is
            # the only record the user gets. Keep it in the GUI log AND on screen.
            detail = payload.get("task_folder") or payload.get("task_url")
            if not detail:
                tasks = payload.get("tasks") or []
                detail = f"{len(tasks)} task(s)" if tasks else "?"
            self.log_handler.error(f"Extension task was not created ({detail}): {exc}")
            self.status_bar.showMessage(f"Extension task failed: {exc}", 8000)

        for payload, applied_task_id in apply_extension_payloads(
            self._drain_pending_extension_payloads(),
            self._apply_extension_task_payload,
            _on_payload_error,
        ):
            if payload.get("one_shot") and not task_id:
                task_id = applied_task_id or ""
        if task_id and self.task_queue.active_task is None:
            task = self.task_queue.find_task(task_id)
            if task is not None:
                self.task_queue.start_task(task.id)
                self._launch_parser_for_task(task)
                self.update_ui_state(True)
        self._refresh_task_table()
        if self.task_queue.queue:
            self.task_table.selectRow(0)
            self._update_start_button_state()

    def _apply_extension_task_payload(self, payload):
        """GUI thread (A-5): materialize one buffered extension payload —
        create the download dir, add the task, attach pending downloads.
        Returns the new task id (one-shot) or None."""
        if payload.get("one_shot"):
            os.makedirs(payload["download_path"], exist_ok=True)
            task = self.task_queue.add_task(
                payload["task_url"], payload["settings"],
                payload["download_path"], one_shot=True)
            task._pending_downloads = payload["items"]
            self.log_handler.info(
                f"One-shot: {payload['added']} items -> {payload['task_folder']}")
            return task.id
        for url, settings, download_path in payload["tasks"]:
            os.makedirs(download_path, exist_ok=True)
            self.task_queue.add_task(url, settings, download_path)
        return None

    def clear_log(self):
        """
        Clear the log display and history
        """
        if hasattr(self, "log_handler"):
            self.log_handler.clear_history()
