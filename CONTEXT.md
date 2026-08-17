# Web Media Parser — Контекст проекта и архитектура (as-built)

> **Обновлено:** 2026-08-16 — приведён в соответствие с кодом после полного аудита (см. [`Audit.md`](Audit.md)).
> **Статус:** очередь задач и все фичи P0–P4 (sieve-JS/Deno, junk-фильтр, curl_cffi, гейтвеи) реализованы.
> Этот документ описывает **как код устроен сейчас**. Исторические планы/аудиты сжаты в §9.

---

## 1. Проект: что это

**Web Media Parser** — desktop-приложение (PySide6) для парсинга и загрузки медиафайлов с веб-сайтов.

- URL (из GUI или Chrome-расширения) → обход страниц по ссылкам → обнаружение изображений/видео → загрузка
- Приоритизация URL (медиа > навигация), преобразование thumbnail → fullsize (паттерны + Imagus Sieve)
- Обход защит: cookie-consent, age-gate (статика + Deno DOM-клик), JS-редиректы
- Python 3.8+ (dev 3.12), Windows — основная платформа
- Опционально: Deno JS-движок, curl_cffi TLS-имперсонация, Chrome-расширение (localhost API)

---

## 2. Структура файлов (текущая)

```
web-media-parser/
├── main.py                  # Точка входа: fix_lxml/fix_brotli → QApplication → MainWindow
├── build_exe.py             # PyInstaller onedir → dist/WebMediaParser/ (+ bin/deno, sieve, allowlist)
├── setup.py                 # setuptools (известно: отстаёт от requirements.txt — Audit PKG-2)
├── requirements.txt         # Канонический список зависимостей
├── WebMediaParser.spec      # Генерируемый артефакт build_exe.py (не источник истины)
│
├── src/
│   ├── app_paths.py         # Портативные пути (dev vs frozen): settings/queue/sessions/resource
│   ├── constants.py         # ВСЕ дефолты и ключи настроек (K.*) — единый источник
│   ├── fix_lxml.py          # Патч lxml.html.clean
│   ├── fix_brotli.py        # Патч brotli для aiohttp (фолбэк вредоносен — Audit DL-6)
│   │
│   ├── core/                # Модель очереди задач
│   │   ├── task_item.py     # TaskItem dataclass + TaskStatus enum (queued/running/paused/...)
│   │   └── task_queue_manager.py  # CRUD, lifecycle, persistence (task_queue.json)
│   │
│   ├── parser/
│   │   ├── parser_manager.py       # Координатор: воркеры, карантин, page limit, domain concurrency
│   │   ├── priority_url_queue.py    # Приоритетная очередь URL (heap + lock/event)
│   │   ├── webpage_parser.py        # HTML, lazy-load, JS-редиректы, гейтвеи, фолбэк на sync-сессию
│   │   ├── json_parser.py           # JSON API парсер (сессия извне; см. Audit CORE-1)
│   │   ├── site_pattern_manager.py  # Нативные паттерны + Imagus Sieve (static/deno)
│   │   ├── pattern_manager.py       # DEPRECATED — алиас на SitePatternManager
│   │   ├── js_engine/               # Deno-движок: engine.py + worker.js (P0) + dom_worker.js (P1)
│   │   │                            #   + gateway_worker.js (P4) + deno_cache/
│   │   ├── junk_filter.py           # Классификатор ad/tracker-мусора + allowlist (P2-lite)
│   │   ├── http_engine.py           # Выбор движка sync-путей: requests vs curl_cffi (P3) + эскалация
│   │   ├── shared_session.py        # AsyncClientManager (aiohttp) + extension_cookies
│   │   ├── utils.py                 # is_media_url, normalize_url, should_skip_crawl_url и др.
│   │   └── domain_blocklist.txt     # УСТАРЕВШАЯ копия блоклиста (канон — resources/; Audit PAT-7)
│   │
│   ├── downloader/
│   │   └── media_downloader.py      # Single/multi-thread загрузка, rate limit, эскалация
│   │
│   ├── gui/
│   │   ├── main_window.py           # Очередь, старт/пауза/стоп, extension-хуки, CSV
│   │   ├── settings_dialog.py       # 5 вкладок: Parsing, Filters, Performance, HTTP, Logging
│   │   └── log_handler.py           # GUILogHandler → QTextEdit (deque 5000)
│   │
│   └── server/
│       └── http_server.py           # ExtensionServer (aiohttp, 127.0.0.1:19876)
│
├── extension/              # Chrome MV3: content_script, background (SW), popup, sieve.js
├── resources/
│   ├── dark_theme.qss, icon.ico
│   ├── domain_blocklist.txt          # Канонический блоклист (вкл. соцсети)
│   ├── junk_allowlist.txt            # Allowlist для junk-фильтра
│   └── patterns/site_patterns.json   # Встроенные паттерны (32)
│
├── tests/                  # 15 модулей pytest (~3200 строк)
├── docs/
│   ├── DENO_JS_ENGINE_DESIGN.md           # Дизайн+статус P0–P4 (двигок Deno)
│   └── DEV_GUIDE_MEDIA_CRAWL_IMPROVEMENTS.md  # Рабочий план улучшений краула
├── Audit.md                # Полный аудит 2026-08-16 (приложение + расширение)
└── Imagus_sieve_*.json     # Sieve-файлы (корень; в dev НЕ подхватываются автоматом — Audit PAT-1)
```

---

## 3. Жизненный цикл задачи (as-built)

### 3.1. Запуск

```
Пользователь (или расширение) добавляет задачу
    │
    ├─ MainWindow.add_task_to_queue() / add_tasks_from_extension()
    │    ├─ снимок настроек: settings = settings_dialog.get_settings()  (МОМЕНТ добавления)
    │    ├─ папка: {download_dir}/{domain}_{YYYYMMDD_HHMMSS}/
    │    └─ TaskQueueManager.add_task(url, settings, download_path, one_shot)
    │         → TaskItem (settings глубококопируются)
    │
    ├─ start_parsing() → _launch_task_from_queue(task_id)
    │    ├─ при активной другой задаче → _pause_current_for_switch()
    │    └─ _launch_parser_for_task(task):
    │         ├─ disconnect старого task_ended (см. §7 GUI-2 — известная дыра)
    │         ├─ ParserManager(url, download_path, settings, log_handler, task_id,
    │         │               one_shot, pending_downloads=...)   # НОВЫЙ инстанс на задачу
    │         └─ QThread + moveToThread + started→start_parsing  # НОВЫЙ поток на задачу
    │
    └─ ParserManager.start_parsing()  (в QThread)
         ├─ asyncio.new_event_loop() + set_event_loop — ЗАТЕМ Event/Queue/Lock
         ├─ loop_thread (AsyncEventLoopThread) → _run_event_loop → _main_task
         └─ monitor_thread (ProgressMonitorThread) → _monitor_progress
```

### 3.2. `_main_task()` (в event loop)

```
1. create_shared_downloader_session()   — requests.Session, Retry(total=0)
2. async with AsyncClientManager        — aiohttp.ClientSession (shared_session)
3. load_state(download_path)            — ВОССТАНОВЛЕНИЕ ДО посева start_url
4. seed start_url в PriorityURLQueue (если state не восстановил очередь)
5. N _parser_worker + N _downloader_worker + _completion_monitor
6. await _stop_event.wait()
7. finally: shutdown DenoJsEngine, закрыть сессии, task_ended.emit(reason)
```

`task_ended(reason)` эмитится ВСЕГДА: `completed | stopped | failed` — по нему живёт автозапуск очереди.

Ключевой порядок для Resume: **load_state вызывается внутри `_main_task` (parser_manager.py:302), до посева start_url** — старый race «воркеры стартуют раньше восстановления state» устранён архитектурно. Очередь URL, download_queue, processed_urls, downloaded_files, quarantine восстанавливаются; докачанные URL фильтруются через `downloaded_files`.

### 3.3. Завершение

| Путь | Что происходит |
|---|---|
| Natural | `_completion_monitor`: очереди пусты > 5 сек (`IDLE_COMPLETION_TIMEOUT_SECONDS`) → `_stop_event.set()` → `task_ended("completed")` → `on_task_ended` → thread.quit/wait → `start_next()` автозапуск |
| Pause | GUI: `mark_paused()` → `save_state()` (run_coroutine_threadsafe, timeout 15) → `stop_parsing()` → `clear_active()`. Partial-файлы сохраняются |
| Stop | GUI: `pm.stop_parsing()` (async, события) → `cleanup_partial_files()` (⚠ до фактической остановки потоков — Audit GUI-3) → удаление state.pkl → `mark_stopped()` |
| Close окна | `save(queue_path)` → save_state активной → stop → accept. Extension-сервер НЕ останавливается явно (daemon-поток; Audit GUI-5) |

Сигнатуры остановки: `threading.Event` (`_thread_stop_event`) для потоков загрузки/монитора + `asyncio.Event` для loop-воркеров; обе выставляются в `stop_parsing()` (через `call_soon_threadsafe` для loop-примитивов).

### 3.4. Persistence

| Что | Где | Как |
|---|---|---|
| Очередь задач | `task_queue.json` рядом с exe (`app_paths.queue_path`) | `TaskQueueManager.save/load`; ⚠ запись не атомарна (Audit GUI-4) |
| State задачи | `{task_download_path}/sessions/{task_id}/last_session.pkl` | `app_paths.task_state_path()` — единая формула для save/load/delete; pickle через executor, запись tmp+fsync+os.replace (атомарно) |
| Настройки | `settings.json` рядом с exe | `SettingsDialog.load/save`; sanitize при загрузке (clamps, CRLF-strip, allowlist-нормализация) |

---

## 4. Потоковая модель (кто где живёт)

| Поток | Создаёт | Что делает |
|---|---|---|
| GUI (main) | QApplication | все Qt-виджеты, таблица очереди, диалоги |
| parser QThread | MainWindow | «владелец» ParserManager; внутри — loop_thread + monitor_thread (plain threading.Thread) |
| AsyncEventLoopThread | ParserManager | весь asyncio: воркеры парсера/загрузчика, completion monitor |
| ExtensionServer | MainWindow (daemon) | aiohttp на 127.0.0.1:19876; колбэки дергаются из этого потока (⚠ см. §7 GUI-1) |
| Downloader executor | loop | `run_in_executor` на `MediaDownloader.download()` — синхронные requests/curl |
| Deno subprocess'ы | js_engine | long-lived воркеры (worker/dom/gateway), JSON-over-stdio, без `--allow-*` |

**Инварианты (нарушение = регрессии):**
1. Один `ParserManager` = одна задача; `QThread` новый на задачу (нельзя restart).
2. asyncio-примитивы создаются только в loop-потоке (после `set_event_loop`).
3. `create_shared_downloader_session` с `Retry(total=0)` — отзывчивость Stop.
4. `DOMAIN_CONCURRENCY_LIMIT = 2` (парсер+загрузчик вместе) на домен.
5. Кросс-потоковая остановка: только через события (`threading.Event` / `asyncio.Event`).
6. Пути — через `src/app_paths.py`; константы/ключи настроек — через `K.*`.
7. `one_shot=True` — не следовать ссылками; media-список может прийти предсобранным из расширения (`pending_downloads`).

---

## 5. Ключевые механизмы парсера

- **Приоритизация** (`priority_url_queue`): прямые медиа ×25, image-linked ×20, совпадение пути ×3+/компонент, домашние ×0.005; гварды `MAX_URL_PATH_SEGMENTS=50`, `MAX_URL_LENGTH=2000`, `DEFAULT_MAX_LINKS_PER_PAGE=200`.
- **Карантин**: 3 failure → домен в карантине; `QUARANTINE_MAX_ITEM_RETRIES=1`; probation timeout 5 c.
- **Fullsize-дискавери** (`_discover_linked_fullsize`): sieve-цепочка link→url→res для thumbnail-переходов; `FULLSIZE_DISCOVER_CONCURRENCY=5`, per-probe timeout 8 c, бюджет 45 c/страницу.
- **Junk-фильтр** (`junk_filter.py` + `SETTING_FILTER_JUNK`): суффиксы хостов, path-токены, слабый сигнал «NNNxNNN + third-party»; allowlist `resources/junk_allowlist.txt`; финальный гейт перед постановкой в download_queue.
- **Форматы**: allowlist'ы `enabled_image/video/audio_formats`; выключенные форматы не скачиваются (GIF/SVG/ICO/CUR по умолчанию off).
- **Гейтвеи (P4)**: статическое извлечение consent-кук из `onclick` (всегда) + DOM-клик через `gateway_worker.js` (при `js_engine=deno`); consent-кэш по доменам в ParserManager; лимит 3 попытки, `_js_gateway_tried` one-shot.
- **HTTP (P3)**: async-фетч всегда aiohttp; sync-пути (фолбэк, гейтвеи, скачивание) — `requests` или `curl_cffi` (имперсонация chrome); авто-эскалация: одна curl-попытка при 403/5xx (`SETTING_HTTP_ESCALATE`, дефолт on; 429 никогда).
- **JS-движок (P0/P1)**: `js_engine: static|deno`; static — JS→Python конвертер; deno — воркеры в песочнице; любое падение → fail-open на статику.

---

## 6. Интеграция с расширением

- Протокол: `GET /api/status`, `GET /api/queue` (= status, дубликат), `POST /api/tasks` `{urls:[{url,source,type,...}], one_shot, user_agent, cookies}`; origin-гейт «любой chrome-extension://» (см. §7 EXT-11).
- One-shot путь: элементы собираются в `_pending_downloads` (оригинальный URL, transformed-флаг), задача стартует как crawl по странице-источнику.
- Куки+UA вкладки передаются в settings задачи (`extension_cookies`, `user_agent`) → `shared_session` (⚠ сессионный заголовок на все хосты — Audit DL-7).
- Popup шлёт `{url, source: pageUrl (referer), type, original_url, transformed}`; валидации элементов на сервере нет (Audit EXT-4).

---

## 7. Известные дефекты (сводно; детали и фиксы — Audit.md)

**Не полагаться на «работает», пока не починено (топ):**

| ID | Суть |
|---|---|
| CORE-1 | JSON-парсер мёртв (`async with` без контекст-менеджера) — critical |
| CORE-10 | Ошибка единственной страницы → задача не завершается (hang) |
| GUI-1 | `QTimer.singleShot` из HTTP-потока — автостарт/refresh из расширения не срабатывают |
| GUI-2 | Гонка смены задач: утечка QThread / порча статуса чужим `task_ended` (сигнал без task_id) |
| GUI-4 | `task_queue.json` не атомарен; один битый элемент стирает всю очередь |
| DL-3 | gzip-ответы ложно бракуются по размеру → карантин домена |
| PAT-1 | Dev-запуск: 0 sieve-правил (файлы вне путей поиска) |
| PAT-7/8 | Блоклист и allowlist в dev грузятся не оттуда (src-копия / неверный путь) |

Полный список (~90 находок с фиксами и кодом): **`Audit.md`**.

---

## 8. Тесты

```bash
python -m pytest tests -q    # 180 passed, 40 skipped (2026-08-16)
```

Скипы: 30 — нет Deno в окружении (весь P0/P1/P4 JS-стек), 9 — curl_cffi не установлен в venv (при том что он в requirements.txt — Audit PKG-1), 1 — сетевой плейсхолдер. Слабые/вакуумные тесты и пробелы — Audit TST-1…TST-6.

---

## 9. История (сжато)

- **2026-04-12/14 — очередь задач.** TaskItem/TaskQueueManager/UI/пауза-резюм/`task_ended`/`{task_id}/state.pkl`. Известные тогда проблемы (Resume-дубликаты, порядок mark_paused, неограниченный log history) — решены: `load_state` перенесён внутрь `_main_task` (до посева URL), `mark_paused()` теперь ДО `stop_parsing()`, history — deque(5000). Внешние аудиты того периода (Gemini 7/10, Qwen 5/10) учтены; применённые фиксы — thread-safe stop, wait in-flight, quarantine в state, regex `$10`, рекурсивный `_expand_variants`.
- **2026-07-16 — план улучшений краула** (`docs/DEV_GUIDE_MEDIA_CRAWL_IMPROVEMENTS.md`): WP по junk/links/fullsize — легли в основу P2-lite и сегментного stop-words.
- **2026-08-07..10 — P0–P4** (`docs/DENO_JS_ENGINE_DESIGN.md`): Deno-движок (sieve-JS, DOM-правила, this.node-шимы), junk_filter, curl_cffi + эскалация, gateway-bypass с consent-кэшем. Боевая проверка: fullsize-дискавери 1940 media (было 0), 0 DOM-ошибок.
- **2026-08-16 — полный аудит** (`Audit.md`): два блока (приложение/расширение), ~90 находок с фиксами; CONTEXT переписан как as-built (этот документ).
