#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Fix for lxml.html.clean issue in requests-html
"""

import sys
import importlib.util

# Предотвращаем ошибку lxml.html.clean
class LXMLHTMLCleanFix:
    @staticmethod
    def patch():
        # Проверяем, доступен ли модуль lxml_html_clean
        if importlib.util.find_spec("lxml_html_clean") is not None:
            # Создаем прокси для lxml.html.clean
            import types
            import lxml.html
            import lxml_html_clean
            
            # Создаем модуль-прокси
            if not hasattr(lxml.html, "clean"):
                lxml.html.clean = types.ModuleType("lxml.html.clean")
                # Копируем атрибуты из lxml_html_clean в lxml.html.clean
                for attr in dir(lxml_html_clean):
                    if not attr.startswith("__"):
                        setattr(lxml.html.clean, attr, getattr(lxml_html_clean, attr))
            
            print("lxml.html.clean proxy created successfully")
            return True
        else:
            print("lxml_html_clean module not found")
            return False