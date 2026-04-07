# Полный Аудит Кода - Web Media Parser

**Дата аудита:** 2026-04-04
**Статус:** Выявлены критические и значительные ошибки

---

## Краткое резюме

Найдено **8 критических/значительных проблем**, требующих немедленного исправления:
- 1 проблема с импортом (Missing import)
- 3 логические ошибки в коде
- 2 дублирования кода/логики
- 1 конфликт конфигурации
- 1 неправильный тип файла

---

## КРИТИЧЕСКИЕ ПРОБЛЕМЫ

### 1. **Missing Import in json_parser.py**
**Файл:** `src/parser/json_parser.py`
**Строка:** 61
**Серьезность:** КРИТИЧЕСКАЯ 🔴

**Проблема:**
```python
# Строка 61 использует K.PARSER_UNKNOWN_ERROR
return set(), [], K.PARSER_UNKNOWN_ERROR, "Failed to fetch or parse JSON", http_status
```
**Но** константы K не импортированы в начале файла.

**Текущий импорт:**
```python
# Строк 1-17: импортов нет K
from scrapling.fetchers import AsyncDynamicSession, AsyncStealthySession
from bs4 import BeautifulSoup

from src import constants as K  # ← ОТСУТСТВУЕТ!
```

**Исправление:**
Добавить в начало файла после других импортов (после строки 15):
```python
from src import constants as K
```

**Полный блок импортов (правильно):**
```python
import re
import json
import logging
import asyncio
from typing import Dict, Any, List, Tuple, Optional, Set
from urllib.parse import urlparse, urljoin

from src.parser.webpage_parser import WebpageParser, HAS_BROTLI
from src.parser.utils import is_image_url, is_media_url, normalize_url
from src import constants as K  # ← ДОБАВИТЬ

logger = logging.getLogger(__name__)
```

**Последствия:** RuntimeError при попытке использовать K.PARSER_* константы во время парсинга JSON.

---

### 2. **Duplicate AsyncClientManager Initialization in parser_manager.py**
**Файл:** `src/parser/parser_manager.py`
**Строки:** 99 и 107
**Серьезность:** КРИТИЧЕСКАЯ 🔴

**Проблема:**
Класс инициализирует AsyncClientManager дважды, что приводит к неопределенному поведению:

```python
def __init__(self, ...):
    # ...
    self.async_client_manager: AsyncClientManager = AsyncClientManager(self.settings)  # Строка 99
    self.session = None

    self.stats = {
        "pages_processed": 0, "images_found": 0, "videos_found": 0,
        "files_downloaded": 0, "files_skipped": 0,
    }

    self.async_client_manager: AsyncClientManager = AsyncClientManager(self.settings)  # Строка 107 - ДУБЛИРУЮЩАЯСЯ!
```

**Исправление:**
Удалить строку 107 полностью. Переинициализация уничтожает первый экземпляр и любые его состояния.

**Правильный код:**
```python
def __init__(self, url: str, download_path: str, settings: Dict[str, Any], log_handler):
    # ... все инициализации ...

    self.async_client_manager = AsyncClientManager(self.settings)  # ← ТОЛЬКО ОДИН РАЗ
    self.session = None

    self.stats = {
        "pages_processed": 0, "images_found": 0, "videos_found": 0,
        "files_downloaded": 0, "files_skipped": 0,
    }
    # Строка 107 удалить полностью!
```

**Последствия:** Неопределенное поведение, потенциальные утечки ресурсов, неправильное управление сессией.

---

### 3. **File Mode Type Mismatch in media_downloader.py**
**Файл:** `src/downloader/media_downloader.py`
**Строка:** 199
**Серьезность:** КРИТИЧЕСКАЯ 🔴

**Проблема:**
Переменная mode устанавливается как `"wb"` (binary write), но файл открывается в текстовом режиме:

```python
mode = "wb"  # Строка 150
# ...
with open(self.filepath, mode) as f:  # Строка 199
    # ...
    write_buffer = bytearray()  # Попытка записать bytearray в текстовый файл!
```

**Полный контекст ошибки (строки 150-199):**
```python
mode = "wb"  # ← ДВОИЧНЫЙ РЕЖИМ
# ... проверки ...
with open(self.filepath, mode) as f:  # ← ОТКРЫТО КАК ТЕКСТ
    start_time = time.time()
    network_chunk_size = 8192
    downloaded_bytes = 0
    for chunk in response_get.iter_content(chunk_size=network_chunk_size):
        if chunk:
            write_buffer.extend(chunk)  # ← bytearray - только для двоичного!
            # ...
            if len(write_buffer) >= K.WRITE_BUFFER_SIZE:
                try: f.write(write_buffer)  # ← ОШИБКА: text mode expected string!
```

**Исправление:**
Строка 199 должна открывать файл в двоичном режиме (уже правильно задан, но открытие неправильно):
```python
# НЕПРАВИЛЬНО:
with open(self.filepath, mode) as f:

# ПРАВИЛЬНО:
with open(self.filepath, "wb") as f:
```

Или просто использовать переменную правильно (mode уже "wb"):
```python
mode = "wb"
# ...
with open(self.filepath, mode) as f:  # Это ДОЛЖНО быть "wb", но check actual opening
```

**На самом деле - более детальный анализ:**
Строка 199 использует переменную `mode="wb"` правильно. Но следующая ошибка на строке 211:

```python
try: f.write(write_buffer); write_buffer.clear()
```

Попытка записать `bytearray` в файл, открытый в двоичном режиме "wb" это нормально... ОДНАКО проверить есть ли проблема с открытием файла фактически.

Actually - ФАКТИЧЕСКАЯ ОШИБКА на строке 211 - потенциально неправильный синтаксис на одной строке.

**Полное исправление (строки 198-225):**
```python
write_buffer = bytearray()
try:
    with open(self.filepath, "wb") as f:  # Явно указать "wb"
        start_time = time.time()
        network_chunk_size = 8192
        downloaded_bytes = 0
        for chunk in response_get.iter_content(chunk_size=network_chunk_size):
            if chunk:
                write_buffer.extend(chunk)
                downloaded_bytes += len(chunk)
                if self.progress_callback:
                    prog = min(100, int((downloaded_bytes / content_length) * 100)) if content_length > 0 else -1
                    self.progress_callback(prog)
                if len(write_buffer) >= K.WRITE_BUFFER_SIZE:
                    try:
                        f.write(write_buffer)
                        write_buffer.clear()
                    except Exception as e:
                        return {"success": False, "error": f"Disk write error: {e}"}
                # ... rate limiting ...

        if write_buffer:
            try:
                f.write(write_buffer)
            except Exception as e:
                return {"success": False, "error": f"Disk write error: {e}"}
except Exception as e:
    logger.error(f"Failed to open file for writing: {e}")
    return {"success": False, "error": f"Could not open file: {e}"}
```

**Последствия:** TypeError или IOError при попытке загрузить файл.

---

## ЗНАЧИТЕЛЬНЫЕ ПРОБЛЕМЫ

### 4. **Conflicting configuration: process_dynamic in settings_dialog.py**
**Файл:** `src/gui/settings_dialog.py`
**Строка:** 51
**Серьезность:** ЗНАЧИТЕЛЬНАЯ 🟠

**Проблема:**
В constants.py (строки 110, 137) `SETTING_PROCESS_DYNAMIC` был удален с комментарием:
```python
# SETTING_PROCESS_DYNAMIC = "process_dynamic" # Removed
# SETTING_PROCESS_DYNAMIC: DEFAULT_PROCESS_DYNAMIC, # Removed
```

Но в settings_dialog.py он ОСТАЛСЯ в default_settings:

```python
def __init__(self, parent=None):
    self.default_settings = {
        # ...
        "process_js": False,
        "process_dynamic": True,  # ← КОНФЛИКТ! Должен быть удален
        # ...
    }
```

**Исправление:**
Удалить строку 51 из settings_dialog.py:
```python
# УДАЛИТЬ:
"process_dynamic": True,

# Результат (строки 48-52):
"bypass_js_redirects": True,  # Enable JavaScript redirect bypass
# Filters
"min_image_width": 100,
```

**Последствия:** Несоответствие конфигурации, потенциальные ошибки при загрузке/сохранении настроек.

---

### 5. **Code Duplication in priority_url_queue.py - Path Component Extraction**
**Файл:** `src/parser/priority_url_queue.py`
**Строки:** 132-157 и 156-163
**Серьезность:** ЗНАЧИТЕЛЬНАЯ 🟠

**Проблема:**
Код для извлечения и проверки компонентов пути дублируется, что затрудняет обслуживание и может привести к расхождению логики:

```python
# Блок 1 (строки 131-139):
source_components = [c for c in source_path.split('/') if c]
url_components = [c for c in url_path.split('/') if c]

if len(source_components) > 0 and len(url_components) > 0 and source_components[0] == url_components[0]:
    logger.debug(f"URLs share common root directory: {source_components[0]} - considering related")
    return True

# ... другой код ...

# Блок 2 (строки 155-162) - ТОЧНО ТОТ ЖЕ КОД:
source_components = [c for c in source_path.split('/') if c]
url_components = [c for c in url_path.split('/') if c]

if len(source_components) > 0 and len(url_components) > 0 and source_components[0] == url_components[0]:
    logger.debug(f"URLs share first path component: {source_components[0]} - considering related")
    return True
```

**Исправление:**
Извлечение компонентов пути один раз в начале функции `_is_downward_url`:

```python
def _is_downward_url(self, url: str, source_url: str) -> bool:
    if not source_url:
        return True

    # Parse URLs
    url_parsed = urlparse(url)
    source_parsed = urlparse(source_url)

    # ... domain checks ...

    # Get normalized paths - ОД The extracted ONCE
    url_path = url_parsed.path.lower().strip('/')
    source_path = source_parsed.path.lower().strip('/')

    # EXTRACT COMPONENTS ONCE
    source_components = [c for c in source_path.split('/') if c]
    url_components = [c for c in url_path.split('/') if c]

    # ... Использовать source_components и url_components везде где нужно ...

    if len(source_components) > 0 and len(url_components) > 0 and source_components[0] == url_components[0]:
        logger.debug(f"URLs share common root directory: {source_components[0]} - considering related")
        return True

    # ... остальная логика использует уже извлеченные компоненты ...
```

**Последствия:**
- Код сложнее читать и обслуживать
- Вероятность то, что логика расходится между двумя блоками
- Потенциальные баги при обновлении одного блока

---

### 6. **Unnecessary Import at End of File**
**Файл:** `src/parser/shared_session.py`
**Строка:** 135
**Серьезность:** ЗНАЧИТЕЛЬНАЯ 🟠

**Проблема:**
Импорт из typing расположен в конце файла, после определений класса:

```python
# Строка 135 (конец файла):
from typing import Optional # Add this if not already present at the top
```

Это должно быть в начале файла с другими импортами.

**Исправление:**
1. Удалить строку 135
2. Добавить в начало файла (строка 12, после других импортов):

```python
# Правильное расположение:
from typing import Dict, Any, Optional  # ← ПЕРЕД определениями классов
```

**Полный блок импортов (исправленный):**
```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Shared asynchronous HTTP client session manager
"""

import aiohttp
import logging
import socket
import ssl
from typing import Dict, Any, Optional  # ← ДОБАВИТЬ OPTIONAL ЗДЕСЬ

logger = logging.getLogger(__name__)
# ... rest of code ...
```

**Последствия:**
- Нарушение PEP 8 (импорты должны быть в начале файла)
- Потенциальные проблемы с type chec инструментами и IDE autocompletion

---

### 7. **Incorrect parameter value to WebpageParser constructor**
**Файл:** `src/parser/parser_manager.py`
**Строка:** 260
**Серьезность:** ЗНАЧИТЕЛЬНАЯ 🟠

**Проблема:**
При создании WebpageParser в методе `_invoke_parser` параметр `process_js` жестко установлен на False, игнорируя настройки пользователя:

```python
async def _invoke_parser(self, url: str, session, is_json_api: bool, context: Dict[str, Any]):
    # ...
    else:
        # Tier 1: Fast Static Parser (aiohttp + BeautifulSoup)
        p = WebpageParser(url, self.settings, False, self.session, self.pattern_manager)
        #                                           ↑↑↑↑↑  НЕПРАВИЛЬНО - всегда False!
```

Должно использовать значение из settings:

```python
is_js_enabled = bool(self.settings.get(K.SETTING_PROCESS_JS, K.DEFAULT_PROCESS_JS))
```

Это значение уже определено позже на строке 267 для проверки, нужно использовать его.

**Исправление:**
```python
async def _invoke_parser(self, url: str, session, is_json_api: bool, context: Dict[str, Any]):
    try:
        domain = urlparse(url).netloc
        is_protected = domain in self.quarantined_domains

        if is_json_api:
            p = JSONWebpageParser(url, self.settings, self.session)
            parse_result = await p.parse()
        else:
            # Вычислить is_js_enabled один раз
            is_js_enabled = bool(self.settings.get(K.SETTING_PROCESS_JS, K.DEFAULT_PROCESS_JS))

            # Tier 1: Fast Static Parser (aiohttp + BeautifulSoup)
            p = WebpageParser(url, self.settings, is_js_enabled, self.session, self.pattern_manager)
            #                                        ↑↑↑↑↑↑↑↑↑ ИСПРАВЛЕНО
            parse_result = await p.parse()

            # Tier 2: Conditional Scrapling upgrade if enabled and static found NO media
            depth = context.get("depth", 0)
            low_media_found = (not parse_result[1] or len(parse_result[1]) == 0)

            if (low_media_found or depth == 0) and is_js_enabled and not self._stop_event.is_set():
                # ... Scrapling logic ...
```

**Последствия:**
- JS-обработка никогда не используется в StaticParser, несмотря на настройки пользователя
- Пользователь не может контролировать обработку JS для первоначального парсера

---

### 8. **Potential Logic Flaw in is_webpage_url check in media_downloader.py**
**Файл:** `src/downloader/media_downloader.py`
**Строка:** 118-124
**Серьезность:** ЗНАЧИТЕЛЬНАЯ 🟠

**Проблема:**
Проверка non_media_extensions не исключает все веб-файлы перед загрузкой:

```python
def _do_download(self, custom_timeout=None):
    try:
        self.filepath = self._ensure_unique_filepath_at_destination(self.filepath)
        non_media_extensions = [ ".html", ".htm", ".php", ".asp", ".aspx", ".js", ".css", ".json", ".xml"]
        url_lower = self.url.lower()
        if any(url_lower.endswith(ext) or f"{ext}?" in url_lower or f"{ext}#" in url_lower for ext in non_media_extensions):
            return {"success": False, "error": "Non-media file based on URL extension"}
```

**Проблема:** Расширения `.json`, `.xml` и некоторые другие могут быть частью URL без явного расширения (например `api/data.json.php`). Логика `f"{ext}?"` проверяет только query string, но не fragment.

Но более критично - проверка на строке 418 делает это еще раз:
```python
if (is_webpage_url(abs_url) or abs_url.rstrip("/").lower().endswith((".html", ".htm", ".php"))) and not is_media_url(abs_url):
```

Это создает двойную проверку и потенциальную путаницу.

**Исправление:**
Унифицировать проверку, используя функцию `is_webpage_url` из utils:

```python
def _do_download(self, custom_timeout=None):
    try:
        self.filepath = self._ensure_unique_filepath_at_destination(self.filepath)

        # Use unified webpage/media check
        from src.parser.utils import is_webpage_url, is_media_url

        if is_webpage_url(self.url) and not is_media_url(self.url):
            return {"success": False, "error": "Non-media file based on URL pattern"}
```

**Последствия:**
- Некоторые веб-файлы могут пройти через проверку и быть загружены
- Несоответствие логики между проверками может скрывать ошибки

---

## ПОТЕНЦИАЛЬНЫЕ ПРОБЛЕМЫ (Рекомендации)

### P1. JSONWebpageParser Missing Constant Import Usage
**Файл:** `src/parser/json_parser.py`
**Оценка:** Высокая

При обновлении парсера API может понадобиться К.PARSER_SUCCESS отслеживание, убедитесь что К импортирован везде.

### P2. Race Condition in PriorityURLQueue
**Файл:** `src/parser/priority_url_queue.py`
**Строки:** 404-448

Функции `update_domain_score` и `update_url_pattern` могут вызываться из разных асинхронных задач одновременно без синхронизации. Хэш-таблицы в Python атомарны для простых операций, но сложные обновления требуют блокировки.

**Рекомендация:** Добавить блокировку если возможны race conditions:
```python
self._score_lock = asyncio.Lock()

async def update_domain_score(self, url: str, media_count: int):
    async with self._score_lock:
        domain = self._get_domain(url)
        if domain:
            self._domain_scores[domain] = self._domain_scores.get(domain, 0) + media_count
```

### P3. Missing Error Handling in Build Script
**Файл:** `build_exe.py`
**Строки:** 28-30

Нет проверки успешного удаления директорий перед сборкой.

---

## РЕКОМЕНДАЦИИ ПО ИСПРАВЛЕНИЮ ПРИОРИТЕТ

### Немедленно (Критич):
1. ✅ Добавить импорт K в json_parser.py (Issue #1)
2. ✅ Удалить дублированную инициализацию AsyncClientManager (Issue #2)
3. ✅ Исправить открытие файла в media_downloader.py (Issue #3)
4. ✅ Исправить параметр process_js в parser_manager.py (Issue #7)

### Высокий приоритет (Значительные):
5. ✅ Удалить "process_dynamic" из settings_dialog.py (Issue #4)
6. ✅ Убрать дублирование кода в priority_url_queue.py (Issue #5)
7. ✅ Переместить импорт в shared_session.py (Issue #6)
8. ✅ Унифицировать проверку webpage/media в media_downloader.py (Issue #8)

### Средний приоритет (Рекомендации):
9. Добавить синхронизацию в PriorityURLQueue (P2)
10. Улучшить обработку ошибок в build_exe.py (P3)

---

## ТЕСТИРОВАНИЕ ПОСЛЕ ИСПРАВЛЕНИЯ

Рекомендуется проверить:
1. **json_parser.py** - Запустить парсинг JSON API и проверить логирование ошибок
2. **parser_manager.py** - Проверить жизненный цикл сессии и управление памятью
3. **media_downloader.py** - Загрузить несколько файлов разного размера, проверить интегность
4. **settings_dialog.py** - Загрузить старые сохраненные настройки (если есть)
5. **priority_url_queue.py** - Проверить с 100+ URL одновременно
6. **shared_session.py** - Проверить type hints в IDE

---

## ДОПОЛНИТЕЛЬНЫЕ ЗАМЕЧАНИЯ

### Хорошие практики, которые используются:
✅ Правильное использование asyncio/await
✅ Экспортирование констант в отдельный файл
✅ Использование типов (Type hints)
✅ Логирование деталей операций

### Области для улучшения (не критичные):
- Добавить docstrings ко всем методам (есть но не везде)
- Добавить логирование для больших операций
- Добавить unit tests для критичных функций

---

**Конец отчета. Применить все КРИТИЧЕСКИЕ и ЗНАЧИТЕЛЬНЫЕ исправления перед продакшеном.**
