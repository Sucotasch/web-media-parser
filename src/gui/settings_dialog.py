#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Settings dialog for the application
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QPushButton,
    QTabWidget,
    QWidget,
    QGroupBox,
    QGridLayout,
    QSlider,
    QPlainTextEdit,
    QComboBox,
    QScrollArea,
    QFileDialog,
)
from PySide6.QtCore import Qt
import json
import os
import sys
import logging
import src.constants as K
from src.app_paths import settings_path as _settings_path

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """
    Dialog for application settings
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(600, 700)

        # Default settings — single source of truth is constants.py
        self.default_settings = {
            # Parser settings
            "search_depth": K.DEFAULT_SEARCH_DEPTH,
            "page_limit": 1000,
            "stay_in_domain": K.DEFAULT_STAY_IN_DOMAIN,
            "process_js": K.DEFAULT_PROCESS_JS,
            "page_timeout": K.DEFAULT_PAGE_TIMEOUT,
            # Pattern settings
            "use_patterns": K.DEFAULT_USE_PATTERNS,
            "custom_pattern_path": None,
            "imagus_sieve_path": None,
            # Bypass settings
            "bypass_cookie_consent": K.DEFAULT_BYPASS_COOKIE_CONSENT,
            "bypass_js_redirects": K.DEFAULT_BYPASS_JS_REDIRECTS,
            # Filters
            "min_image_width": K.DEFAULT_MIN_IMAGE_WIDTH,
            "min_image_height": K.DEFAULT_MIN_IMAGE_HEIGHT,
            "min_image_size": K.DEFAULT_MIN_IMAGE_SIZE_KB,
            "min_video_size": K.DEFAULT_MIN_VIDEO_SIZE_KB,
            # Performance
            "parser_threads": K.DEFAULT_PARSER_THREADS,
            "downloader_threads": K.DEFAULT_DOWNLOADER_THREADS,
            "max_download_speed": 0,
            "threads_per_file": K.DEFAULT_THREADS_PER_FILE,
            # Remember last used directory
            "last_download_dir": os.path.expanduser("~"),
            "user_agent": K.DEFAULT_USER_AGENT,
            "referrer": "auto",
            "accept_language": K.DEFAULT_ACCEPT_LANGUAGE,
            "timeout": K.DEFAULT_TIMEOUT,
            "retry_count": K.DEFAULT_RETRY_COUNT,
            "proxy": "",
            # Stop words
            "stop_words": [
                "login",
                "signin",
                "signup",
                "register",
                "password",
                "account",
                "payment",
                "checkout",
                "subscribe",
                "join",
                "login",
                "admin",
                "analytics",
                "pixel",
                "tracking",
                "advertisement",
                "banner",
                "popup",
                "faq",
                "help",
                "support",
                "contact",
                "about",
                "terms",
                "privacy",
            ],
            # Logging
            "log_to_file": False,
            "log_file_path": "web_media_parser.log",
        }

        # Load settings from file or use defaults
        self.settings = self.load_settings()

        # Initialize UI
        self.init_ui()

        # Apply settings to UI
        self.apply_settings_to_ui()

    def init_ui(self):
        """
        Initialize the user interface (all labels in English)
        """
        main_layout = QVBoxLayout(self)

        # Create tab widget
        self.tab_widget = QTabWidget()

        # Parser tab
        parser_tab = QWidget()
        parser_layout = QVBoxLayout(parser_tab)

        # Parser settings group
        parser_group = QGroupBox("Parser Settings")
        parser_grid = QGridLayout(parser_group)

        # Search depth
        parser_grid.addWidget(QLabel("Search Depth:"), 0, 0)
        self.search_depth_spin = QSpinBox()
        self.search_depth_spin.setRange(0, 10)
        self.search_depth_spin.setToolTip("Maximum link depth for parsing")
        parser_grid.addWidget(self.search_depth_spin, 0, 1)

        # Content limit (pages from which files were downloaded)
        parser_grid.addWidget(QLabel("Content Limit:"), 1, 0)
        self.page_limit_spin = QSpinBox()
        self.page_limit_spin.setRange(1, 1000)
        self.page_limit_spin.setToolTip("Stop after downloading files from N pages (0 = unlimited)")
        parser_grid.addWidget(self.page_limit_spin, 1, 1)

        # Page timeout
        parser_grid.addWidget(QLabel("Page Timeout (sec):"), 2, 0)
        self.page_timeout_spin = QSpinBox()
        self.page_timeout_spin.setRange(10, 300)
        self.page_timeout_spin.setToolTip("Timeout for page loading (seconds)")
        parser_grid.addWidget(self.page_timeout_spin, 2, 1)

        # Stay in domain
        parser_grid.addWidget(QLabel("Stay in Domain:"), 3, 0)
        self.stay_in_domain_check = QCheckBox()
        self.stay_in_domain_check.setToolTip("Stay within initial domain only")
        parser_grid.addWidget(self.stay_in_domain_check, 3, 1)

        # Process JavaScript
        parser_grid.addWidget(QLabel("Process JavaScript:"), 4, 0)
        self.process_js_check = QCheckBox()
        self.process_js_check.setToolTip("Process JavaScript-generated content")
        parser_grid.addWidget(self.process_js_check, 4, 1)
        
        # Use image patterns
        parser_grid.addWidget(QLabel("Use Image Patterns:"), 5, 0)
        self.use_patterns_check = QCheckBox()
        self.use_patterns_check.setToolTip(
            "Use site patterns for extracting fullsize images from thumbnails (improves image quality)"
        )
        parser_grid.addWidget(self.use_patterns_check, 5, 1)
        
        # Custom pattern file
        parser_grid.addWidget(QLabel("Custom Pattern File:"), 7, 0)
        pattern_layout = QHBoxLayout()
        self.custom_pattern_edit = QLineEdit()
        self.custom_pattern_edit.setPlaceholderText("Path to custom pattern file (optional)")
        self.custom_pattern_edit.setToolTip("Path to custom site pattern file in JSON format")
        self.custom_pattern_browse = QPushButton("Browse")
        self.custom_pattern_browse.clicked.connect(self.browse_pattern_file)
        pattern_layout.addWidget(self.custom_pattern_edit)
        pattern_layout.addWidget(self.custom_pattern_browse)
        parser_grid.addLayout(pattern_layout, 7, 1)
        
        # Pattern info
        parser_grid.addWidget(QLabel("Pattern Info:"), 8, 0)
        self.pattern_info_label = QLabel("No patterns loaded")
        self.pattern_info_label.setWordWrap(True)
        parser_grid.addWidget(self.pattern_info_label, 8, 1)

        # Imagus Sieve file
        parser_grid.addWidget(QLabel("Imagus Sieve File:"), 9, 0)
        imagus_layout = QHBoxLayout()
        self.imagus_sieve_edit = QLineEdit()
        self.imagus_sieve_edit.setPlaceholderText("Path to Imagus sieve file (optional)")
        self.imagus_sieve_edit.setToolTip("Path to Imagus-style sieve file in JSON format")
        self.imagus_sieve_browse = QPushButton("Browse")
        self.imagus_sieve_browse.clicked.connect(self.browse_imagus_file)
        imagus_layout.addWidget(self.imagus_sieve_edit)
        imagus_layout.addWidget(self.imagus_sieve_browse)
        parser_grid.addLayout(imagus_layout, 9, 1)
        
        # Bypass options
        parser_grid.addWidget(QLabel("Bypass Cookie Consent:"), 10, 0)
        self.bypass_cookie_consent_check = QCheckBox()
        self.bypass_cookie_consent_check.setToolTip(
            "Automatically bypass cookie consent prompts by setting common cookie values"
        )
        parser_grid.addWidget(self.bypass_cookie_consent_check, 10, 1)
        
        parser_grid.addWidget(QLabel("Bypass JS Redirects:"), 11, 0)
        self.bypass_js_redirects_check = QCheckBox()
        self.bypass_js_redirects_check.setToolTip(
            "Automatically follow JavaScript redirects by analyzing page content"
        )
        parser_grid.addWidget(self.bypass_js_redirects_check, 11, 1)

        # Gateway & Visibility Bypass
        parser_grid.addWidget(QLabel("Filter Hidden Links:"), 12, 0)
        self.filter_hidden_links_check = QCheckBox()
        self.filter_hidden_links_check.setToolTip(
            "Ignore invisible links to avoid bot-traps and honeypots"
        )
        parser_grid.addWidget(self.filter_hidden_links_check, 12, 1)

        parser_layout.addWidget(parser_group)

        # Filters tab
        filters_tab = QWidget()
        filters_layout = QVBoxLayout(filters_tab)

        # Image filters group
        image_group = QGroupBox("Image Filters")
        image_grid = QGridLayout(image_group)

        # Minimum image width
        image_grid.addWidget(QLabel("Min. Width (px):"), 0, 0)
        self.min_image_width_spin = QSpinBox()
        self.min_image_width_spin.setRange(0, 9999)
        self.min_image_width_spin.setToolTip("Minimum image width (px)")
        image_grid.addWidget(self.min_image_width_spin, 0, 1)

        # Minimum image height
        image_grid.addWidget(QLabel("Min. Height (px):"), 1, 0)
        self.min_image_height_spin = QSpinBox()
        self.min_image_height_spin.setRange(0, 9999)
        self.min_image_height_spin.setToolTip("Minimum image height (px)")
        image_grid.addWidget(self.min_image_height_spin, 1, 1)

        # Minimum image size
        image_grid.addWidget(QLabel("Min. Size (KB):"), 2, 0)
        self.min_image_size_spin = QSpinBox()
        self.min_image_size_spin.setRange(0, 9999)
        self.min_image_size_spin.setToolTip("Minimum image file size (KB)")
        image_grid.addWidget(self.min_image_size_spin, 2, 1)

        filters_layout.addWidget(image_group)

        # Video filters group
        video_group = QGroupBox("Video Filters")
        video_grid = QGridLayout(video_group)

        # Minimum video size
        video_grid.addWidget(QLabel("Min. Size (KB):"), 0, 0)
        self.min_video_size_spin = QSpinBox()
        self.min_video_size_spin.setRange(0, 99999)
        self.min_video_size_spin.setToolTip("Minimum video file size (KB)")
        video_grid.addWidget(self.min_video_size_spin, 0, 1)

        filters_layout.addWidget(video_group)

        # Stop words group
        stop_words_group = QGroupBox("Stop Words")
        stop_words_layout = QVBoxLayout(stop_words_group)

        self.stop_words_edit = QPlainTextEdit()
        self.stop_words_edit.setPlaceholderText("Enter stop words, one per line...")
        self.stop_words_edit.setToolTip(
            "Stop words (one per line, links containing these will be skipped)"
        )
        stop_words_layout.addWidget(self.stop_words_edit)

        filters_layout.addWidget(stop_words_group)

        # Performance tab
        performance_tab = QWidget()
        performance_layout = QVBoxLayout(performance_tab)

        # Threads group
        threads_group = QGroupBox("Threads")
        threads_grid = QGridLayout(threads_group)

        # Parser threads
        threads_grid.addWidget(QLabel("Parser Threads:"), 0, 0)
        self.parser_threads_spin = QSpinBox()
        self.parser_threads_spin.setRange(1, 16)
        self.parser_threads_spin.setToolTip("Number of parser threads")
        threads_grid.addWidget(self.parser_threads_spin, 0, 1)

        # Downloader threads
        threads_grid.addWidget(QLabel("Downloader Threads:"), 1, 0)
        self.downloader_threads_spin = QSpinBox()
        self.downloader_threads_spin.setRange(1, 32)
        self.downloader_threads_spin.setToolTip("Number of downloader threads")
        threads_grid.addWidget(self.downloader_threads_spin, 1, 1)

        # Threads per file
        threads_grid.addWidget(QLabel("Threads per File:"), 2, 0)
        self.threads_per_file_spin = QSpinBox()
        self.threads_per_file_spin.setRange(1, 8)
        self.threads_per_file_spin.setToolTip("Number of threads per file download")
        threads_grid.addWidget(self.threads_per_file_spin, 2, 1)

        performance_layout.addWidget(threads_group)

        # Download speed group
        speed_group = QGroupBox("Download Speed")
        speed_layout = QVBoxLayout(speed_group)

        speed_label_layout = QHBoxLayout()
        speed_label_layout.addWidget(QLabel("Max. Speed (KB/s):"))
        self.speed_value_label = QLabel("0 (unlimited)")
        speed_label_layout.addWidget(self.speed_value_label)
        speed_layout.addLayout(speed_label_layout)

        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(0, 10000)
        self.speed_slider.setTickInterval(1000)
        self.speed_slider.setTickPosition(QSlider.TicksBelow)
        self.speed_slider.valueChanged.connect(self.update_speed_label)
        self.speed_slider.setToolTip("Maximum download speed (0 = unlimited)")
        speed_layout.addWidget(self.speed_slider)

        performance_layout.addWidget(speed_group)

        # HTTP tab
        http_tab = QWidget()
        http_layout = QVBoxLayout(http_tab)

        # HTTP settings group
        http_group = QGroupBox("HTTP Settings")
        http_grid = QGridLayout(http_group)

        # User agent
        http_grid.addWidget(QLabel("User-Agent:"), 0, 0)
        self.user_agent_edit = QLineEdit()
        self.user_agent_edit.setToolTip("Custom User-Agent header")
        http_grid.addWidget(self.user_agent_edit, 0, 1)

        # Referer policy
        http_grid.addWidget(QLabel("Referer:"), 1, 0)
        self.referer_combo = QComboBox()
        self.referer_combo.addItems(["auto", "origin", "none"])
        self.referer_combo.setToolTip("Referer policy")
        http_grid.addWidget(self.referer_combo, 1, 1)

        # Accept language
        http_grid.addWidget(QLabel("Accept-Language:"), 2, 0)
        self.accept_language_edit = QLineEdit()
        self.accept_language_edit.setToolTip("Accept-Language header")
        http_grid.addWidget(self.accept_language_edit, 2, 1)

        # Timeout
        http_grid.addWidget(QLabel("Timeout (sec):"), 3, 0)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 120)
        self.timeout_spin.setToolTip("Request timeout (seconds)")
        http_grid.addWidget(self.timeout_spin, 3, 1)

        # Retry count
        http_grid.addWidget(QLabel("Retry Count:"), 4, 0)
        self.retry_count_spin = QSpinBox()
        self.retry_count_spin.setRange(0, 10)
        self.retry_count_spin.setToolTip("Number of retries for failed requests")
        http_grid.addWidget(self.retry_count_spin, 4, 1)

        # Proxy server
        http_grid.addWidget(QLabel("Proxy Server:"), 5, 0)
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("e.g. 127.0.0.1:8080")
        self.proxy_edit.setToolTip("Proxy server (host:port). Prepend http:// if needed.")
        http_grid.addWidget(self.proxy_edit, 5, 1)

        http_layout.addWidget(http_group)

        # Logging tab
        logging_tab = QWidget()
        logging_layout = QVBoxLayout(logging_tab)

        logging_group = QGroupBox("Log to File")
        logging_grid = QGridLayout(logging_group)

        self.log_to_file_check = QCheckBox("Enable logging to file")
        self.log_to_file_check.setToolTip("Save application log to a text file")
        logging_grid.addWidget(self.log_to_file_check, 0, 0, 1, 2)

        logging_grid.addWidget(QLabel("Log file:"), 1, 0)
        self.log_file_edit = QLineEdit()
        self.log_file_edit.setPlaceholderText("web_media_parser.log")
        self.log_file_edit.setToolTip("Path to the log file (relative to app directory or absolute)")
        logging_grid.addWidget(self.log_file_edit, 1, 1)

        self.log_file_browse = QPushButton("Browse")
        self.log_file_browse.clicked.connect(self.browse_log_file)
        logging_grid.addWidget(self.log_file_browse, 1, 2)

        logging_layout.addWidget(logging_group)
        logging_layout.addStretch()

        # Add all tabs
        self.tab_widget.addTab(parser_tab, "Parsing")
        self.tab_widget.addTab(filters_tab, "Filters")
        self.tab_widget.addTab(performance_tab, "Performance")
        self.tab_widget.addTab(http_tab, "HTTP")
        self.tab_widget.addTab(logging_tab, "Logging")

        main_layout.addWidget(self.tab_widget)

        # Buttons
        buttons_layout = QHBoxLayout()
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_settings)
        reset_button = QPushButton("Reset")
        reset_button.clicked.connect(self.reset_settings)

        buttons_layout.addWidget(save_button)
        buttons_layout.addWidget(reset_button)
        main_layout.addLayout(buttons_layout)
        self.setLayout(main_layout)

    def update_speed_label(self, value):
        """
        Update speed limit label based on slider value
        """
        if value == 0:
            self.speed_value_label.setText("0 (unlimited)")
        else:
            self.speed_value_label.setText(f"{value}")

    def browse_log_file(self):
        """Open file dialog to select log file path."""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Select Log File",
            self.log_file_edit.text() or "web_media_parser.log",
            "Text Files (*.log *.txt);;All Files (*)",
        )
        if file_path:
            self.log_file_edit.setText(file_path)

    def browse_pattern_file(self):
        """
        Open file dialog to select custom pattern file
        """
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Custom Pattern File",
            os.path.expanduser("~"),
            "JSON Files (*.json)",
        )
        
        if file_path:
            self.custom_pattern_edit.setText(file_path)
            self.update_pattern_info(file_path)
    
    def browse_imagus_file(self):
        """
        Open file dialog to select Imagus sieve file
        """
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Imagus Sieve File",
            os.path.expanduser("~"),
            "JSON Files (*.json)",
        )
        
        if file_path:
            self.imagus_sieve_edit.setText(file_path)

    def update_pattern_info(self, pattern_path=None):
        """
        Update pattern info label with count of available patterns
        """
        try:
            # Try to load the pattern file to get count
            if pattern_path and os.path.exists(pattern_path):
                with open(pattern_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                # Count patterns
                pattern_count = 0
                if 'patterns' in data:
                    pattern_count = len(data['patterns'])
                else:
                    # Count site entries in old format
                    for key, value in data.items():
                        if key != 'global_settings' and isinstance(value, dict):
                            if 'site' in value or 'domains' in value or 'url_patterns' in value:
                                pattern_count += 1
                
                self.pattern_info_label.setText(f"Custom file: {pattern_count} patterns loaded")
            else:
                # Show built-in pattern count
                built_in_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "resources",
                    "patterns",
                    "site_patterns.json"
                )
                
                if os.path.exists(built_in_path):
                    with open(built_in_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    
                    # Count patterns
                    pattern_count = 0
                    if 'patterns' in data:
                        pattern_count = len(data['patterns'])
                    else:
                        # Count site entries
                        for key, value in data.items():
                            if key != 'global_settings' and isinstance(value, dict):
                                if 'site' in value or 'domains' in value or 'url_patterns' in value:
                                    pattern_count += 1
                    
                    self.pattern_info_label.setText(f"Built-in: {pattern_count} patterns available")
                else:
                    self.pattern_info_label.setText("No pattern file found")
        except Exception as e:
            self.pattern_info_label.setText(f"Error loading pattern file: {str(e)[:50]}...")
    
    def apply_settings_to_ui(self):
        """
        Apply loaded settings to UI elements (all tooltips and labels in English)
        """
        # Parser settings
        self.search_depth_spin.setValue(self.settings.get("search_depth", 3))
        self.page_limit_spin.setValue(self.settings.get("page_limit", 1000))
        self.page_timeout_spin.setValue(self.settings.get("page_timeout", 30))
        self.stay_in_domain_check.setChecked(self.settings.get("stay_in_domain", True))
        self.process_js_check.setChecked(self.settings.get("process_js", True))
        self.bypass_cookie_consent_check.setChecked(
            self.settings.get(
                K.SETTING_BYPASS_COOKIE_CONSENT, K.DEFAULT_BYPASS_COOKIE_CONSENT
            )
        )
        self.bypass_js_redirects_check.setChecked(
            self.settings.get(
                K.SETTING_BYPASS_JS_REDIRECTS, K.DEFAULT_BYPASS_JS_REDIRECTS
            )
        )
        self.use_patterns_check.setChecked(
            self.settings.get(K.SETTING_USE_PATTERNS, K.DEFAULT_USE_PATTERNS)
        )
        self.filter_hidden_links_check.setChecked(
            self.settings.get(K.SETTING_FILTER_HIDDEN_LINKS, K.DEFAULT_FILTER_HIDDEN_LINKS)
        )
        
        # Pattern settings
        custom_pattern_path = self.settings.get(K.SETTING_CUSTOM_PATTERN_PATH, "")
        if custom_pattern_path:
            self.custom_pattern_edit.setText(custom_pattern_path)
            
        imagus_sieve_path = self.settings.get(K.SETTING_IMAGUS_SIEVE_PATH, "")
        if imagus_sieve_path:
            self.imagus_sieve_edit.setText(imagus_sieve_path)
            
        # Update pattern info
        self.update_pattern_info(custom_pattern_path)

        # Filters
        self.min_image_width_spin.setValue(self.settings.get("min_image_width", 100))
        self.min_image_height_spin.setValue(self.settings.get("min_image_height", 100))
        self.min_image_size_spin.setValue(self.settings.get("min_image_size", 40))
        self.min_video_size_spin.setValue(self.settings.get("min_video_size", 1000))

        # Stop words
        stop_words = self.settings.get("stop_words", [])
        self.stop_words_edit.setPlainText("\n".join(stop_words))

        # Performance
        self.parser_threads_spin.setValue(self.settings.get("parser_threads", K.DEFAULT_PARSER_THREADS))
        self.downloader_threads_spin.setValue(
            self.settings.get("downloader_threads", K.DEFAULT_DOWNLOADER_THREADS)
        )
        self.threads_per_file_spin.setValue(self.settings.get("threads_per_file", 1))
        self.speed_slider.setValue(self.settings.get("max_download_speed", 0))
        self.update_speed_label(self.speed_slider.value())

        # HTTP
        self.user_agent_edit.setText(self.settings.get("user_agent", ""))
        self.referer_combo.setCurrentText(self.settings.get("referrer", "auto"))
        self.accept_language_edit.setText(self.settings.get("accept_language", ""))
        self.timeout_spin.setValue(self.settings.get("timeout", 30))
        self.retry_count_spin.setValue(self.settings.get("retry_count", 3))
        self.proxy_edit.setText(self.settings.get("proxy", ""))

        # Logging
        self.log_to_file_check.setChecked(self.settings.get("log_to_file", False))
        self.log_file_edit.setText(self.settings.get("log_file_path", "web_media_parser.log"))

    def get_settings_from_ui(self):
        """
        Get settings from UI elements
        """
        settings = {}
        
        # Parser settings
        settings["search_depth"] = self.search_depth_spin.value()
        settings["page_limit"] = self.page_limit_spin.value()
        settings["page_timeout"] = self.page_timeout_spin.value()
        settings["stay_in_domain"] = self.stay_in_domain_check.isChecked()
        settings[K.SETTING_PROCESS_JS] = self.process_js_check.isChecked()
        settings[K.SETTING_BYPASS_COOKIE_CONSENT] = self.bypass_cookie_consent_check.isChecked()
        settings[K.SETTING_BYPASS_JS_REDIRECTS] = self.bypass_js_redirects_check.isChecked()
        settings[K.SETTING_USE_PATTERNS] = self.use_patterns_check.isChecked()
        settings[K.SETTING_FILTER_HIDDEN_LINKS] = self.filter_hidden_links_check.isChecked()
        settings[K.SETTING_CUSTOM_PATTERN_PATH] = self.custom_pattern_edit.text() if self.custom_pattern_edit.text() else ""
        settings[K.SETTING_IMAGUS_SIEVE_PATH] = self.imagus_sieve_edit.text() if self.imagus_sieve_edit.text() else ""

        # Filters
        settings["min_image_width"] = self.min_image_width_spin.value()
        settings["min_image_height"] = self.min_image_height_spin.value()
        settings["min_image_size"] = self.min_image_size_spin.value()
        settings["min_video_size"] = self.min_video_size_spin.value()

        # Stop words
        stop_words_text = self.stop_words_edit.toPlainText().strip()
        if stop_words_text:
            settings["stop_words"] = [
                word.strip() for word in stop_words_text.split("\n") if word.strip()
            ]
        else:
            settings["stop_words"] = []

        # Performance
        settings["parser_threads"] = self.parser_threads_spin.value()
        settings["downloader_threads"] = self.downloader_threads_spin.value()
        settings["threads_per_file"] = self.threads_per_file_spin.value()
        settings["max_download_speed"] = self.speed_slider.value()

        # HTTP
        settings["user_agent"] = self.user_agent_edit.text()
        settings["referrer"] = self.referer_combo.currentText()
        settings["accept_language"] = self.accept_language_edit.text()
        settings["timeout"] = self.timeout_spin.value()
        settings["retry_count"] = self.retry_count_spin.value()
        settings["proxy"] = self.proxy_edit.text().strip()

        # Logging
        settings["log_to_file"] = self.log_to_file_check.isChecked()
        settings["log_file_path"] = self.log_file_edit.text().strip() or "web_media_parser.log"

        return settings

    def _get_settings_path(self):
        """Get the persistent path for settings.json (always next to the exe)."""
        return _settings_path()

    def save_settings(self):
        """
        Save settings to file and remember last used download directory
        """
        self.settings = self.get_settings_from_ui()
        # Save last used download directory if available from parent/main window
        if hasattr(self.parent(), "get_download_directory"):
            self.settings["last_download_dir"] = self.parent().get_download_directory()
        
        settings_path = self._get_settings_path()
        try:
            # Ensure directory exists (though for EXE dir it always should)
            os.makedirs(os.path.dirname(settings_path), exist_ok=True)
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=4)
            logger.info(f"Saved settings to {settings_path}")
        except Exception as e:
            logger.error(f"Error saving settings to {settings_path}: {e}")
            
        self.accept()

    def reset_settings(self):
        """
        Reset settings to defaults
        """
        self.settings = self.default_settings.copy()
        self.apply_settings_to_ui()

    @staticmethod
    def sanitize_settings(settings: dict) -> dict:
        """Clamp all numeric settings to safe ranges and strip dangerous chars from strings."""
        clamps = {
            "search_depth": (0, 10),
            "page_limit": (1, 10000),
            "page_timeout": (5, 600),
            "timeout": (1, 600),
            "retry_count": (0, 20),
            "parser_threads": (1, 64),
            "downloader_threads": (1, 128),
            "threads_per_file": (1, 8),
            "min_image_width": (0, 99999),
            "min_image_height": (0, 99999),
            "min_image_size": (0, 99999),
            "min_video_size": (0, 99999),
            "max_download_speed": (0, 100000),
        }
        for key, (lo, hi) in clamps.items():
            val = settings.get(key)
            if val is not None:
                try:
                    settings[key] = max(lo, min(hi, int(val)))
                except (TypeError, ValueError):
                    settings[key] = K.DEFAULT_SETTINGS_VALUES.get(key, lo)
        # Strip CRLF from string headers (prevent header injection)
        for key in ("user_agent", "accept_language"):
            val = settings.get(key, "")
            if isinstance(val, str):
                settings[key] = val.replace("\r", "").replace("\n", "")
        return settings

    def load_settings(self):
        """
        Load settings from file or use defaults, including last used download directory.
        Unifies path logic for consistency.
        """
        settings_path = self._get_settings_path()
        
        # Try to load from main location
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                logger.info(f"Loaded settings from {settings_path}")
                return self.sanitize_settings(settings)
            except Exception as e:
                logger.error(f"Error loading settings from {settings_path}: {str(e)}")
        
        # If no settings file exists, return defaults
        logger.info(f"Settings file not found at {settings_path}, using defaults.")
        default_settings = self.default_settings.copy()
        
        # Ensure some critical defaults are set
        if "min_video_size" not in default_settings:
            default_settings["min_video_size"] = 1000
            
        return default_settings

    def get_last_download_dir(self):
        """Get the last used download directory"""
        return self.settings.get("last_download_dir", os.path.expanduser("~"))

    def get_settings(self):
        """
        Get current settings
        """
        return self.settings