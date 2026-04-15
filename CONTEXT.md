# Web Media Parser — Project Context & Development Guide

> **Last updated:** 2026-04-12  
> **Current work:** Очередь задач — ВСЕ 14 ЗАДАЧ ЗАВЕРШЕНЫ ✅  
> **Completed:** Этапы 1a, 1b, 2a, 2b, 3, 4

---

## 1. Проект: что это

**Web Media Parser** — desktop-приложение (PySide6) для парсинга и загрузки медиафайлов с веб-сайтов.

- **Один URL** → обход страниц по ссылкам → обнаружение изображений/видео → загрузка
- Умная приоритезация URL (медиа > навигация)
- Преобразование thumbnail → fullsize через систему паттернов (встроенные + Imagus Sieve)
- Обход защит: cookie-consent, age-gate, JS-редиректы
- Python 3.8+, Windows (win32)

**Root:** `d:\Arx\Software Downloads\_Images_EDIT-pack\web-media-parser\`

---

## 2. Структура файлов

```
web-media-parser/
├── main.py                           # Точка входа. PySide6 QApplication
├── requirements.txt                  # Зависимости
├── build_exe.py                      # PyInstaller сборка
├── setup.py                          # setuptools
├── LICENSE, README.md
│
├── src/
│   ├── __init__.py
│   ├── constants.py                  # ВСЕ константы и дефолты (K.*) — КРИТИЧНО
│   ├── fix_lxml.py                   # Патч lxml.html.clean
│   ├── fix_brotli.py                 # Патч Brotli для aiohttp
│   │
│   ├── core/                         # ✅ СОЗДАНО
│   │   ├── __init__.py
│   │   ├── task_item.py              # ✅ TaskItem dataclass + TaskStatus enum
│   │   └── task_queue_manager.py     # ✅ TaskQueueManager
│   │
│   ├── parser/
│   │   ├── __init__.py
│   │   ├── parser_manager.py         # Координатор парсинга (796 строк). ОДИН экземпляр = ОДНА задача
│   │   ├── priority_url_queue.py     # Приоритетная очередь URL (heap-based, 559 строк)
│   │   ├── webpage_parser.py         # Парсинг HTML, lazy-load, JS-редиректы (887 строк)
│   │   ├── json_parser.py            # Парсинг JSON API
│   │   ├── site_pattern_manager.py   # Паттерны сайтов + Imagus Sieve
│   │   ├── pattern_manager.py        # DEPRECATED → SitePatternManager
│   │   ├── shared_session.py         # aiohttp.ClientSession wrapper
│   │   ├── utils.py                  # is_media_url, normalize_url, format_proxy_url и др.
│   │   └── domain_blocklist.txt      # Чёрный список доменов
│   │
│   ├── downloader/
│   │   ├── __init__.py
│   │   └── media_downloader.py       # Загрузка файлов (single + multi-thread)
│   │
│   └── gui/
│       ├── __init__.py
│       ├── main_window.py            # Главное окно PySide6 (466 строк)
│       ├── settings_dialog.py        # Диалог настроек (4 вкладки, 719 строк)
│       └── log_handler.py            # GUILogHandler → QTextEdit
│
├── tests/                            # Тесты (6 файлов)
├── resources/
│   ├── dark_theme.qss                # Stylesheet тёмной темы
│   ├── icon.ico                      # Иконка приложения
│   ├── domain_blocklist.txt          # Блоклист доменов
│   └── patterns/
│       └── site_patterns.json        # Встроенные паттерны сайтов
```

---

## 3. Текущая архитектура (ДО очереди задач)

### 3.1. Жизненный цикл одной задачи

```
User вводит URL → нажимает Start
    │
    ├─ MainWindow.start_parsing()
    │   ├─ Создаёт папку: {download_dir}/{domain}_{YYYYMMDD_HHMMSS}/
    │   ├─ Создаёт ParserManager(url, download_path, settings, log_handler)
    │   ├─ Проверяет last_session.pkl → загружает если есть
    │   ├─ Подключает Qt-сигналы
    │   └─ Создаёт QThread, moveToThread, start()
    │
    ├─ ParserManager.start_parsing()  (в QThread)
    │   ├─ Создаёт asyncio.new_event_loop()
    │   ├─ Создаёт asyncio.Event: _stop_event, _pause_event
    │   ├─ Создаёт asyncio.Queue: download_queue, quarantine_queue
    │   ├─ Создаёт новый PriorityURLQueue
    │   ├─ Запускает 2 потока:
    │   │   ├─ AsyncEventLoopThread → _run_event_loop() → loop.run_until_complete(_main_task())
    │   │   └─ ProgressMonitorThread → _monitor_progress()
    │
    ├─ _main_task()  (в asyncio event loop)
    │   ├─ Создаёт shared_dl_session (requests.Session)
    │   ├─ async with AsyncClientManager → aiohttp.ClientSession
    │   ├─ Создаёт N parser_worker + N downloader_worker + completion_monitor
    │   ├─ Ждёт _stop_event.wait()
    │   └─ finally: закрывает shared_dl_session, эмитит parsing_finished (если natural)
    │
    └─ Завершение
        ├─ Natural: parsing_finished → MainWindow.on_parsing_finished() → thread.quit().wait()
        └─ Stop: thread.quit().wait() (parsing_finished НЕ эмитится!)
```

### 3.2. Ключевые файлы и строки

| Файл | Что важно | Строки |
|---|---|---|
| `src/parser/parser_manager.py` | `__init__` — инициализация всех полей | 45-107 |
| | `reset()` — что сбрасывает, а что НЕТ | 108-130 |
| | `start_parsing()` — создаёт потоки и примитивы | 171-220 |
| | `_main_task()` — ядро: воркеры, сессии, finally | 235-304 |
| | `_completion_monitor()` — idle detection → natural completion | 306-343 |
| | `_parser_worker()` — обработка URL | 424-470 |
| | `stop_parsing()` — set _stop_event, drain queues | 644-660 |
| | `save_state()` / `load_state()` — pickle сериализация | 744-797 |
| `src/gui/main_window.py` | `start_parsing()` — создание PM, QThread, сигналы | 242-298 |
| | `stop_parsing()` — thread.quit().wait() | 307-324 |
| | `closeEvent()` — save_session + stop | 426-443 |
| | `on_parsing_finished()` — cleanup | 367-382 |
| `src/downloader/media_downloader.py` | `_do_download()` — проверка stop_event между чанками | 277 |
| | `_download_with_threads()` — .part* файлы, cleanup в finally | 296-340 |
| | `create_shared_downloader_session()` — requests.Session с 0 retry | 16-60 |
| `src/parser/priority_url_queue.py` | `reset_async_primitives()` — пересоздание Lock/Event | 40-45 |
| | `_calculate_url_priority()` — скоринг URL | 191-369 |
| `src/constants.py` | Все K.* константы | ВЕСЬ ФАЙЛ |

### 3.3. Что НЕЛЬЗЯ менять

| Элемент | Почему |
|---|---|
| ParserManager = один экземпляр на задачу | lifecycle assumptions baked in, reset() неполный |
| QThread = новый на каждую задачу | QThread нельзя restart после quit()+wait() |
| `create_shared_downloader_session` с `total=0` retry | Критично для мгновенной остановки (Stop button) |
| Asyncio примитивы создаются ВНУТРИ event loop thread | Cross-loop errors если создать в GUI потоке |
| `_shared_downloader_session` создаётся в `_main_task` | Lifecycle привязан к async with |

---

## 4. Известные проблемы и ограничения

### 4.1. Критичные для очереди задач

| Проблема | Файл | Описание | Решение |
|---|---|---|---|
| `parsing_finished` только при natural | `parser_manager.py:303` | При Stop сигнал НЕ эмитится. Очередь не узнает о завершении | Добавить `task_ended(reason)` signal |
| Один `last_session.pkl` на всех | `parser_manager.py:770` | Все задачи пишут в один файл, перезаписывают друг друга | `{task_id}/state.pkl` |
| Partial-файлы не чистятся при Stop | `media_downloader.py` | `.part*` файлы остаются после Stop | Рекурсивное удаление при Stop |
| Настройки берутся в момент Start | `main_window.py:276` | Если изменить настройки пока задача в очереди — она получит новые | Фиксировать при добавлении в очередь |
| Log message_history неограничен | `log_handler.py` | Растёт безгранично при долгих сессиях | Лимит 5000 записей |

### 4.2. Общего характера

| Проблема | Описание |
|---|---|
| `asyncio.Event.is_set()` из другого потока | Формально race condition, но на практике работает (простой flag read) |
| Duplicate URL в очереди | Нет дедупликации на уровне очереди (внутри PM — есть через processed_urls) |
| Cookie-сессия парсера не сохраняется | При restore state cookies теряются |
| Нет очистки старых state файлов | `sessions/` папка может разрастаться |

---

## 5. Архитектура очереди задач (ПЛАН)

### 5.1. Модель данных

```python
# src/core/task_item.py

class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"

@dataclass
class TaskItem:
    id: str                          # UUID4
    url: str
    settings: dict                   # Snapshot настроек НА МОМЕНТ ДОБАВЛЕНИЯ
    download_path: str               # {base_dir}/{domain}_{timestamp}/
    status: TaskStatus = TaskStatus.QUEUED
    stats: dict = field(default_factory=dict)
    error_message: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Runtime — НЕ сериализуется
    parser_manager: ParserManager | None = None
    thread: QThread | None = None
```

### 5.2. TaskQueueManager

```python
# src/core/task_queue_manager.py

class TaskQueueManager(QObject):
    # Сигналы
    task_added = Signal(str)           # task_id
    task_removed = Signal(str)
    task_status_changed = Signal(str, str)  # task_id, new_status
    active_task_changed = Signal(str)  # task_id (или "")
    queue_saved = Signal()
    
    def __init__(self, base_download_dir: str, log_handler): ...
    
    def add_task(self, url: str, settings: dict) -> TaskItem: ...
    def remove_task(self, task_id: str): ...
    def move_task_up(self, task_id: str): ...
    def move_task_down(self, task_id: str): ...
    
    def start_task(self, task_id: str): ...
        # Если есть активная → Pause её
        # Создаёт новый ParserManager, новый QThread
        # Подключает сигналы
        # Запускает
    
    def pause_active_task(self): ...
        # Вызывает parser_manager.pause_parsing()
        # save_state()
        # Статус → PAUSED, возвращает в очередь
    
    def stop_active_task(self): ...
        # Вызывает parser_manager.stop_parsing()
        # Удаляет state файл
        # Удаляет *.part* файлы
        # Статус → STOPPED
        # НЕ трогает другие задачи в очереди
    
    def stop_and_clear_queue(self): ...
        # Полная очистка — для будущего "Stop All"
    
    def get_next_queued(self) -> TaskItem | None: ...
    
    def save(self, filepath: str): ...
    def load(self, filepath: str): ...
```

### 5.3. UI изменения

**Текущий UI:**
```
[ URL: ________________________ ]
[ Directory: ___ ] [ Browse ]
[ Start ] [ Pause ] [ Stop ] [ Settings ]
```

**Новый UI:**
```
[ URL: ________________________ ] [+]
[ Directory: ___ ] [ Browse ]

┌─ Очередь задач ───────────────────────────────────┐
│ # │ URL          │ Статус   │ Прогресс │ Найдено │
│───┼──────────────┼──────────┼──────────┼─────────│
│ ▶ │ example.com  │ Running  │ ████ 67% │ 156     │
│ 2 │ picsite.com  │ Queued   │ —        │ —       │
│ 3 │ gallery.org  │ Paused   │ ██ 33%   │ 42      │
│ 4 │ oldsite.net  │ Completed│ ███████ 100%│ 890  │
│                                                       │
│ [▲] [▼] [↑] [↓] [Edit] [Remove]                   │
└───────────────────────────────────────────────────┘

[ Start ] [ Pause ] [ Stop ] [ Settings ]
```

**Изменения в поведении:**
- `+` — добавляет URL в очередь (фиксирует текущие настройки из SettingsDialog)
- `Start` — запускает ВЫБРАННУЮ задачу. Если другая активна → Pause
- `Pause` — мягкая остановка (state + partial-файлы сохранены)
- `Stop` — жёсткая: только текущая задача, очереди PM обнулены, partial-файлы удалены, state удалён
- Автозапуск следующей при завершении текущей

### 5.4. Семантика Pause vs Stop

| Действие | Активная задача | Остальные в очереди | Partial-файлы | State |
|---|---|---|---|---|
| **Pause** | Пауза, state сохранён | Без изменений | Остаются | Сохранён → resume докачает |
| **Stop** | Остановлена, PM очищен | Без изменений | **Удалены** (*.part* + incomplete) | **Удалён** |
| **Close окна** | Pause + save | Сохранены | Остаются | Сохранены |

### 5.5. Формат task_queue.json

```json
{
  "version": 1,
  "active_task_id": "uuid-of-currently-running",
  "tasks": [
    {
      "id": "uuid-1",
      "url": "https://example.com",
      "download_path": "/downloads/example_com_20260412_1630",
      "settings": { ... },
      "status": "running",
      "stats": { "pages_processed": 42, "files_downloaded": 156 },
      "state_file": "sessions/uuid-1/state.pkl",
      "created_at": "2026-04-12T16:30:00"
    }
  ]
}
```

---

## 6. План задач (14 штук, 4 этапа)

### Этап 1a: Модель данных
- [x] **#1** `src/core/task_item.py` — TaskItem dataclass + TaskStatus enum ✅
- [x] **#2** `src/core/task_queue_manager.py` — менеджер очереди ✅

### Этап 1b: Инфраструктура ParserManager
- [x] **#12** Добавить сигнал `task_ended(reason: str)` в ParserManager ✅
- [x] **#13** Сессии в `{task_id}/state.pkl` вместо общего `last_session.pkl` ✅

### Этап 2a: UI основы
- [ ] **#3** Кнопка `+` рядом с URL — добавляет задачу с фиксацией настроек
- [ ] **#4** TaskQueueView (QTableView) — таблица задач

### Этап 2b: UI управление
- [ ] **#5** Start — запускает выбранную; если другая активна → Pause
- [ ] **#6** Кнопки ▲ ▼ Edit Remove

### Этап 3: Жизненный цикл
- [ ] **#7** Автозапуск следующей при завершении (task_ended → start_next)
- [ ] **#8** Pause — сохранить state + partial-файлы, статус → paused
- [ ] **#9** Stop — очистить очереди PM, удалить *.part*, удалить state, статус → stopped

### Этап 4: Сохранение
- [ ] **#10** Сохранение очереди в task_queue.json
- [ ] **#11** Загрузка очереди при старте, восстановление paused
- [ ] **#15** Лимит message_history (5000) + префикс `[Task #N]`

---

## 7. Технические детали

### 7.1. Как ParserManager сигнализирует завершение

```python
# parser_manager.py:303 — СЕЙЧАС
if self._completed_naturally:
    self.parsing_finished.emit()

# НУЖНО ДОБАВИТЬ:
self.task_ended.emit("completed" if self._completed_naturally else "stopped")
```

### 7.2. Как создаётся ParserManager сейчас

```python
# main_window.py:273-278
self.parser_manager = ParserManager(
    url=url,
    download_path=download_path,
    settings=settings,
    log_handler=self.log_handler,
)
```

### 7.3. Сигналы ParserManager → MainWindow

```python
# main_window.py:281-285
self.parser_manager.total_progress_updated.connect(self.update_total_progress)
self.parser_manager.current_progress_updated.connect(self.update_current_progress)
self.parser_manager.parsing_finished.connect(self.on_parsing_finished)
self.parser_manager.status_updated.connect(self.update_status)
```

### 7.4. QThread lifecycle

```python
# Start
self.parser_thread = QThread()
self.parser_manager.moveToThread(self.parser_thread)
self.parser_thread.started.connect(self.parser_manager.start_parsing)
self.parser_thread.start()

# Stop / Finish
self.parser_thread.quit()
self.parser_thread.wait()
# QThread НЕЛЬЗЯ переиспользовать — только новый экземпляр
```

### 7.5. Сохранение/загрузка сессии сейчас

```python
# Save: parser_manager.py:744
# Путь: {download_dir}/sessions/last_session.pkl
async def save_state(self, task_download_path: str) -> None:
    state = {
        "url_queue_items": ...,
        "download_queue_items": ...,
        "processed_urls": ...,
        "downloaded_files": ...,
        "stats": ...,
        "settings": ...,
        "start_url": ...,
        "download_path": ...,
        "domain_health": ...,
        "quarantined_domains": ...,
    }
    session_dir = os.path.join(task_download_path, K.SESSION_STATE_SUBDIR)
    # ... pickle.dumps → file

# Load: main_window.py:239
async def _load_previous_state(self, state_path: str):
    await self.parser_manager.load_state(state_path)
```

### 7.6. Partial-файлы — где и как

```python
# media_downloader.py
# Multi-threaded download создаёт: {filepath}.part0, {filepath}.part1, ...
# _download_with_threads() line 296-340:
#   - В finally: пытается удалить все .part* файлы
#   - При success: удаляет после объединения
#   - При failure: удаляет
# НО: если поток убит (thread.quit().wait()) — finally может не успеть

# ОДНОПОТОЧНАЯ загрузка (single-threaded):
#   - Пишет напрямую в {filepath}
#   - При Stop: файл остаётся неполным, НЕ удаляется
```

### 7.7. Константы (src/constants.py)

Ключевые для разработки очереди:

```python
SESSION_STATE_FILENAME = "last_session.pkl"       # → будет заменён на {task_id}/state.pkl
SESSION_STATE_SUBDIR = "sessions"
IDLE_COMPLETION_TIMEOUT_SECONDS = 5
QUARANTINE_BATCH_PROCESS_SIZE = 10
QUARANTINE_MAX_ITEM_RETRIES = 1
MAX_THREADS_PER_FILE_CAP = 8
WRITE_BUFFER_SIZE = 1024 * 1024  # 1MB
```

### 7.8. GUILogHandler

```python
# src/gui/log_handler.py
class GUILogHandler(logging.Handler):
    def __init__(self, text_edit):
        self.text_edit = text_edit
        self.message_history = []  # ← НЕОГРАНИЧЕННЫЙ СПИСОК! Нужно добавить лимит
    
    def emit(self, record):
        self.message_history.append(record)  # ← растёт бесконечно
        # ... обновляет QTextEdit
```

---

## 8. Что нужно знать при перезапуске

1. **ParserManager нельзя переиспользовать** — всегда новый экземпляр. `reset()` неполный.
2. **QThread нельзя перезапустить** — всегда новый экземпляр после `quit().wait()`.
3. **Сигнал `parsing_finished` НЕ эмитится при Stop** — нужен `task_ended(reason)`.
4. **Session state файл один на всех** — `last_session.pkl` перезаписывается. Нужно `{task_id}/state.pkl`.
5. **Partial-файлы не чистятся автоматически** — `.part*` остаются после аварийной остановки.
6. **Настройки фиксируются в момент Start** — сейчас. Нужно в момент добавления в очередь.
7. **Log message_history неограничен** — при долгой работе может занять много памяти.
8. **Все asyncio примитивы создаются ВНУТРИ event loop thread** — иначе cross-loop errors.
9. **`stop_parsing()` НЕ ждёт завершения потоков** — это делает `MainWindow.stop_parsing()` через `thread.quit().wait()`.
10. **`closeEvent` сохраняет сессию только если `is_running`** — queued задачи не сохраняются.

---

## 9. Следующий шаг

**Этап 2a**: UI — добавить кнопку `+` рядом с полем URL и QTableView для отображения очереди задач.

### Ключевые изменения в main_window.py:
1. Рядом с `url_input` добавить кнопку `+` → `add_task_to_queue()`
2. Под URL/Dir секцией добавить `QTableView` с моделью `QStandardItemModel`
3. Колонки: URL, Статус, Прогресс, Найдено, Скачано
4. `TaskQueueManager` создаётся в `__init__` MainWindow
5. `add_task_to_queue()` фиксирует текущие настройки из `settings_dialog`

---

## 10. Git статус

- Рабочая директория: `d:\Arx\Software Downloads\_Images_EDIT-pack\web-media-parser\`
- Текущая ветка: (узнать через `git status`)
- README.md обновлён (актуальное описание проекта)
- CONTEXT.md создаётся сейчас (этот файл)

---

## 11. История изменений и текущие проблемы (обновлено 2026-04-14)

### Что уже реализовано:
1. **TaskItem + TaskQueueManager** — модель данных, очередь, CRUD, перемещение, сохранение/загрузка в JSON
2. **UI очереди** — таблица задач, кнопки ▲▼ Remove, кнопка +
3. **Pause/Resume логика** — Pause = Save State + Hard Stop (не `_pause_event`), Resume = Load State + Новый ParserManager
4. **Кнопка Resume** — меняет текст на "Resume" если выбрана paused задача
5. **Исправление `_active_id`** — `clear_active()` перенесён в `finally` блока `stop_parsing()`
6. **`task_ended` signal** — ParserManager эмитит `task_ended(reason)` для завершения задачи
7. **Изолированные сессии** — `{task_id}/state.pkl` вместо общего `last_session.pkl`
8. **`pickle.dumps` в executor** — не блокирует event loop

### НЕ решённая проблема №1: Resume загружает файлы заново
**Симптом:** После Resume парсер начинает парсить всё с начала, заново находит и скачивает уже скачанные файлы (с суффиксами `_1`, `_2`).

**Что пробовали:**
- `load_state()` загружает `processed_urls` и `downloaded_files` из pickle
- В `_launch_parser_for_task` проверяем наличие `state.pkl` и вызываем `load_state`
- `on_task_ended` не перезаписывает статус `paused`

**Почему не работает (анализ кода):**
- `load_state` загружает `processed_urls` и `downloaded_files` в `ParserManager`
- **НО:** `load_state` НЕ загружает обратно URL в `url_queue`! Очередь URL пуста.
- ParserManager при старте seed'ит только `start_url` в `url_queue` (строка 232 `_main_task`)
- Парсер заново парсит страницы, находит те же media URLs, но `downloaded_files` должен был их отфильтровать...
- **НО:** `downloaded_files` содержит URL, а не файлы. При Resume URL может отличаться (например, параметры запроса), или `normalize_url` по-другому обрабатывает
- **ГЛАВНАЯ ПРИЧИНА:** `downloaded_files` проверяется в `_process_media_batch` (строка 538), но `download_queue` **ПУСТАЯ** после `load_state` (мы не восстанавливаем queue items обратно в queue). Парсер заново находит медиа и добавляет в download_queue. Проверка `if abs_url in self.downloaded_files` должна сработать, но:
  - `normalize_url` может давать разный результат
  - Или `downloaded_files` вообще не загружается корректно

**Что нужно:**
1. Убедиться что `load_state` корректно восстанавливает `downloaded_files`
2. Проверить что `normalize_url` даёт одинаковый результат при Resume
3. Возможно, добавить проверку по хэшу файла или имени файла, а не только URL

### НЕ решённая проблема №2: Загрузки продолжаются после Pause
**Симптом:** В логе видно `Starting single-threaded download` после сообщения `Parsing paused`.

**Причина:** Downloader воркер уже запустил `MediaDownloader.download()` до Pause. Это синхронная операция в `run_in_executor`, её нельзя прервать. После возврата из `download()` воркер проверяет `_pause_event` и засыпает.

**Решение (частичное):** При Pause мы теперь делаем Hard Stop (`stop_parsing()`), который убивает воркеров. Но текущий download может доканчиться до того, как `stop_parsing()` сработает. Это приемлемо.

### НЕ решённая проблема №3: Per-row статус в таблице
**Симптом:** Paused задача может отображаться как `stopped` в таблице.

**Причина:** `on_task_ended` вызывается с `reason="stopped"` когда мы делаем Stop внутри `toggle_pause`. Теперь добавлена проверка `if active.status != TaskStatus.PAUSED`, но нужно убедиться что `mark_paused()` вызывается ДО `stop_parsing()`.

**Текущий порядок в `toggle_pause`:**
1. `save_state` → 2. `stop_parsing` → 3. `mark_paused`
Это НЕПРАВИЛЬНО — `on_task_ended` может сработать раньше `mark_paused`.

**Нужно:** `mark_paused()` ДО `stop_parsing()`, или игнорировать `task_ended` если статус уже `paused`.

---

## 12. План следующих исправлений

1. **Исправить порядок в `toggle_pause`:** `mark_paused()` → `stop_parsing()` (чтобы `on_task_ended` видел `paused`)
2. **Исправить Resume:** убедиться что `downloaded_files` корректно фильтрует повторы. Добавить логирование при сравнении URL
3. **Проверить `normalize_url`**: убедиться что URL до и после Resume нормализуются одинаково

---

## 13. Аудит от сторонних моделей (2026-04-14)

### Gemini Audit — Оценка: 7/10 (полезно)
**Верно выявлено:**
- Потоконебезопасность `_stop_event.set()` из GUI потока ✅ → **ИСПРАВЛЕНО** (`call_soon_threadsafe`)
- Утечка in-flight задач при `save_state` ✅ → **ИСПРАВЛЕНО** (wait с таймаутом 5 сек)
- Quarantine Queue не сериализуется ✅ → **ИСПРАВЛЕНО** (добавлено в save/load_state)
- Regex баг `$10` → `\g<1>0` ✅ → **ИСПРАВЛЕНО** (`\d` → `\d+`)
- `_expand_variants` не раскрывает вложенные `#...#` ✅ → **ИСПРАВЛЕНО** (рекурсия)

**Упустил:** Главную проблему Resume — `load_state` не восстанавливает `url_queue` обратно.

### Qwen Audit — Оценка: 5/10 (устаревшие данные)
**Содержит:** Рекомендации по устаревшему коду (например, "ввести `async def pause_parsing`" — уже есть), overengineering ("SafeSnapshotQueue" — не нужно).

---

## 14. Применённые исправления из аудита (2026-04-14)

| # | Исправление | Файл | Статус |
|---|---|---|---|
| 1 | Thread-safe `stop_parsing` (`call_soon_threadsafe`) | parser_manager.py | ✅ |
| 2 | Wait for in-flight tasks (5s timeout) | parser_manager.py:save_state | ✅ |
| 3 | Quarantine Queue в save/load_state | parser_manager.py | ✅ |
| 4 | Логирование в save_state (кол-во items) | parser_manager.py | ✅ |
| 5 | Regex `\d+` для `$10`+ групп | site_pattern_manager.py | ✅ |
| 6 | Рекурсивный `_expand_variants` | site_pattern_manager.py | ✅ |

---

## 15. ГЛАВНАЯ НЕ РЕШЁННАЯ ПРОБЛЕМА: Resume дубликаты (обновлено 2026-04-14)

**Симптом:** После Resume парсер начинает ВСЁ С НАЧАЛА. Заново парсит страницы, заново находит те же медиа URL, заново скачивает файлы (с суффиксами `_1`, `_2`).

**Что УЖЕ работает:**
- `save_state` сохраняет: `url_queue_items`, `download_queue_items`, `processed_urls`, `downloaded_files`, `quarantine_queue_items`
- `load_state` восстанавливает всё это обратно (включая очереди)
- `downloaded_files` содержит URL скачанных файлов
- В `_process_media_batch` есть проверка `if abs_url in self.downloaded_files: continue`
- `url_queue` восстанавливается из `url_queue_items`
- Логирование добавлено: `save_state: N URL items, N download items, N downloaded files`

**Что НЕ работает (гипотезы):**
1. **`url_queue_items` сохраняется пустым** — возможно, PriorityURLQueue `_queue` содержит объекты `PrioritizedURL`, а не tuple. Код пытается извлечь `(url, depth, source_url, context)` но `PrioritizedURL` — это dataclass с другими атрибутами.
2. **`load_state` вызывается слишком поздно** — после того как парсер уже начал seed'ить `start_url` и парсить. Race condition: воркеры стартуют раньше чем `load_state` успевает заполнить `processed_urls`.
3. **`downloaded_files` URL не совпадает** — `normalize_url` даёт разный результат при повторном парсинге (например, порядок query параметров, trailing slash, регистр).
4. **`_process_media_batch` не проверяет `downloaded_files`** — возможно проверка `if abs_url in self.downloaded_files` находится в другом месте или условия не совпадают.

**Нужно для диагностики:**
- Проверить лог: сколько URL items сохраняется? Если 0 — проблема в сериализации `PrioritizedURL`.
- Проверить лог: сколько downloaded_files сохраняется?
- Проверить лог: сколько downloaded_files загружается?
- Сравнить URL до и после `normalize_url` — совпадают ли.
- Проверить timing: `load_state` вызывается ДО или ПОСЛЕ старта воркеров.

**Ключевой код для проверки:**
- `parser_manager.py:776` — сохранение `url_queue_items` (PrioritizedURL vs tuple)
- `parser_manager.py:232` — seed start_url (происходит ДО load_state?)
- `parser_manager.py:538` — проверка `downloaded_files` в `_process_media_batch`
- `main_window.py:563-578` — `_launch_parser_for_task` вызывает `load_state`
