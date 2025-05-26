#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Backup script for Web Media Parser project
Creates a timestamped zip archive of the entire project
"""

import os
import sys
import time
import shutil
import zipfile
from datetime import datetime


def create_backup():
    """
    Create a timestamped backup of the entire project
    """
    # Get current timestamp for unique backup name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"web_media_parser_backup_{timestamp}.zip"
    
    # Get the project root directory (where this script is located)
    project_root = os.path.dirname(os.path.abspath(__file__))
    
    # Create backup directory if it doesn't exist
    backup_dir = os.path.join(project_root, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    
    backup_path = os.path.join(backup_dir, backup_filename)
    
    # Files and directories to exclude from backup
    exclude = [
        "__pycache__",
        ".git",
        ".pytest_cache",
        "backups",
        "build",
        "dist",
        ".idea",
        ".vscode",
        "venv",
        "env",
        ".env",
    ]
    
    print(f"Creating backup: {backup_path}")
    
    # Create zip file
    with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # Walk through directory
        for root, dirs, files in os.walk(project_root):
            # Skip excluded directories
            dirs[:] = [d for d in dirs if d not in exclude]
            
            # Get relative path
            rel_path = os.path.relpath(root, project_root)
            if rel_path == ".":
                rel_path = ""
            
            # Add files
            for file in files:
                # Skip the backup file itself and any other backups
                if file.endswith(".zip") and "backup" in file:
                    continue
                    
                file_path = os.path.join(root, file)
                zipf_path = os.path.join(rel_path, file)
                
                # Add file to zip
                try:
                    zipf.write(file_path, zipf_path)
                except Exception as e:
                    print(f"Error adding {file_path}: {e}")
    
    print(f"Backup created successfully: {backup_path}")
    print(f"Size: {os.path.getsize(backup_path) / 1024 / 1024:.2f} MB")
    return backup_path


if __name__ == "__main__":
    print("Starting backup of Web Media Parser project...")
    backup_file = create_backup()
    print(f"Backup completed: {backup_file}")