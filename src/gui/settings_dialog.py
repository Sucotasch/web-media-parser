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


class SettingsDialog(QDialog):
    """
    Dialog for application settings
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(600, 700)

        # Default settings
        self.default_settings = {
            # Parser settings
            "search_depth": 3,
            "page_limit": 1000,
            "stay_in_domain": True,
            "process_js": True,  # Enable JavaScript processing by default
            "process_dynamic": True,
            "page_timeout": 30,
            # Pattern settings
            "use_patterns": True,  # Enable image patterns by default
            "custom_pattern_path": None,  # Path to custom pattern file
            # Bypass settings
            "bypass_cookie_consent": True,  # Enable cookie consent bypass
            "bypass_js_redirects": True,  # Enable JavaScript redirect bypass
            # Filters
            "min_image_width": 100,
            "min_image_height": 100,
            "min_image_size": 40,
            "min_video_size": 1000,  # Ensure video filtering works by default
            # Performance
            "parser_threads": 4,
            "downloader_threads": 8,
            "max_download_speed": 0,
            "threads_per_file": 1,
            # Remember last used directory
            "last_download_dir": os.path.expanduser("~"),  # HTTP settings
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "referrer": "auto",
            "accept_language": "en-US,en;q=0.9",
            "timeout": 30,
            "retry_count": 3,
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

        # Page limit
        parser_grid.addWidget(QLabel("Page Limit:"), 1, 0)
        self.page_limit_spin = QSpinBox()
        self.page_limit_spin.setRange(1, 1000)
        self.page_limit_spin.setToolTip("Maximum number of pages to process")
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

        # Process dynamic content
        parser_grid.addWidget(QLabel("Process Dynamic Content:"), 5, 0)
        self.process_dynamic_check = QCheckBox()
        self.process_dynamic_check.setToolTip(
            "Process dynamic content (lazy loading, infinite scroll)"
        )
        parser_grid.addWidget(self.process_dynamic_check, 5, 1)
        
        # Use image patterns
        parser_grid.addWidget(QLabel("Use Image Patterns:"), 6, 0)
        self.use_patterns_check = QCheckBox()
        self.use_patterns_check.setToolTip(
            "Use site patterns for extracting fullsize images from thumbnails (improves image quality)"
        )
        parser_grid.addWidget(self.use_patterns_check, 6, 1)
        
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
        
        # Bypass options
        parser_grid.addWidget(QLabel("Bypass Cookie Consent:"), 9, 0)
        self.bypass_cookie_consent_check = QCheckBox()
        self.bypass_cookie_consent_check.setToolTip(
            "Automatically bypass cookie consent prompts by setting common cookie values"
        )
        parser_grid.addWidget(self.bypass_cookie_consent_check, 9, 1)
        
        parser_grid.addWidget(QLabel("Bypass JS Redirects:"), 10, 0)
        self.bypass_js_redirects_check = QCheckBox()
        self.bypass_js_redirects_check.setToolTip(
            "Automatically follow JavaScript redirects by analyzing page content"
        )
        parser_grid.addWidget(self.bypass_js_redirects_check, 10, 1)

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

        http_layout.addWidget(http_group)

        # Add all tabs
        self.tab_widget.addTab(parser_tab, "Parsing")
        self.tab_widget.addTab(filters_tab, "Filters")
        self.tab_widget.addTab(performance_tab, "Performance")
        self.tab_widget.addTab(http_tab, "HTTP")

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
        self.process_dynamic_check.setChecked(
            self.settings.get("process_dynamic", True)
        )
        
        # Pattern settings
        self.use_patterns_check.setChecked(self.settings.get("use_patterns", True))
        custom_pattern_path = self.settings.get("custom_pattern_path", "")
        if custom_pattern_path:
            self.custom_pattern_edit.setText(custom_pattern_path)
            
        # Bypass settings
        self.bypass_cookie_consent_check.setChecked(self.settings.get("bypass_cookie_consent", True))
        self.bypass_js_redirects_check.setChecked(self.settings.get("bypass_js_redirects", True))
            
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
        self.parser_threads_spin.setValue(self.settings.get("parser_threads", 2))
        self.downloader_threads_spin.setValue(
            self.settings.get("downloader_threads", 4)
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
        settings["process_js"] = self.process_js_check.isChecked()
        settings["process_dynamic"] = self.process_dynamic_check.isChecked()
        
        # Pattern settings
        settings["use_patterns"] = self.use_patterns_check.isChecked()
        custom_pattern_path = self.custom_pattern_edit.text().strip()
        if custom_pattern_path:
            settings["custom_pattern_path"] = custom_pattern_path
        else:
            settings["custom_pattern_path"] = None
            
        # Bypass settings
        settings["bypass_cookie_consent"] = self.bypass_cookie_consent_check.isChecked()
        settings["bypass_js_redirects"] = self.bypass_js_redirects_check.isChecked()

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

        return settings

    def save_settings(self):
        """
        Save settings to file and remember last used download directory
        """
        self.settings = self.get_settings_from_ui()
        # Save last used download directory if available from parent/main window
        if hasattr(self.parent(), "get_download_directory"):
            self.settings["last_download_dir"] = self.parent().get_download_directory()
        
        # Save settings in the same directory as the executable
        settings_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "settings.json")
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(self.settings, f, indent=4)
            
        print(f"[SettingsDialog] Saved settings to {settings_path}")
        self.accept()

    def reset_settings(self):
        """
        Reset settings to defaults
        """
        self.settings = self.default_settings.copy()
        self.apply_settings_to_ui()

    def load_settings(self):
        """
        Load settings from file or use defaults, including last used download directory
        """
        # Load settings from the same directory as the executable
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        settings_path = os.path.join(base_dir, "settings.json")
        
        # For PyInstaller bundle
        if hasattr(sys, "_MEIPASS"):
            # First try to find settings in the same directory as the exe
            exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else base_dir
            exe_settings_path = os.path.join(exe_dir, "settings.json")
            if os.path.exists(exe_settings_path):
                settings_path = exe_settings_path
                print(f"[SettingsDialog] Using settings from executable directory: {settings_path}")
        
        # Also check the old location for backward compatibility
        old_settings_path = os.path.join(os.path.expanduser("~"), ".web_media_parser", "settings.json")
        
        # First try to load from main location
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                print(f"[SettingsDialog] Loaded settings from {settings_path}")
                return settings
            except Exception as e:
                print(f"[SettingsDialog] Error loading settings from {settings_path}: {str(e)}")
        
        # Then try old location for backward compatibility
        if os.path.exists(old_settings_path):
            try:
                with open(old_settings_path, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                print(f"[SettingsDialog] Loaded settings from old location {old_settings_path}")
                # Save to new location for future use
                try:
                    with open(settings_path, "w", encoding="utf-8") as f:
                        json.dump(settings, f, indent=4)
                    print(f"[SettingsDialog] Migrated settings to new location {settings_path}")
                except Exception as e:
                    print(f"[SettingsDialog] Error migrating settings: {str(e)}")
                return settings
            except Exception as e:
                print(f"[SettingsDialog] Error loading settings from old location: {str(e)}")
        
        # If no settings file exists anywhere, create default one
        default_settings = self.default_settings.copy()
        
        # Set default min_video_size to 1000 to ensure video filtering works
        default_settings["min_video_size"] = 1000
        
        # Save default settings
        try:
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(default_settings, f, indent=4)
            print(f"[SettingsDialog] Created new settings file with defaults at {settings_path}")
        except Exception as e:
            print(f"[SettingsDialog] Error creating default settings: {str(e)}")
        
        return default_settings

    def get_last_download_dir(self):
        """Get the last used download directory"""
        return self.settings.get("last_download_dir", os.path.expanduser("~"))

    def get_settings(self):
        """
        Get current settings
        """
        return self.settings