# DEV GUIDE — Web Media Parser

Канонический гид разработчика (актуализирован 2026-10-29, ревизия v1.4.1). Живой документ:
если расходится с кодом — правится этот файл, а не код задним числом.
Исторические материалы: `Audit/archive/`, `docs/archive/`. Открытый бэклог: `Audit/2026-10-29_FULL_AUDIT.md`.

---

## 1. Что это

Desktop-приложение (PySide6): обход веб-страниц → обнаружение медиа (изображения/видео/аудио/потоки) →
преобразование превью в полноразмерные URL (нативные паттерны + Imagus Sieve) → загрузка файлов.
Chrome/Firefox-расширение (MV3, `extension/`) шлёт страницы/URL в приложение по localhost-HTTP.

- **Язык:** Python (dev-окружение 3.12; **минимальный пол 3.10** — в `src/` используются PEP 604 union-типы).
- **Основная ОС:** Windows. **Документы/UI — русский, код и идентификаторы — английский.**
- Версия — `src/constants.py: APP_VERSION`; синхронизирована с `extension/manifest.json`.

## 2. Быстрый старт

```bash
.\venv\Scripts\activate            # Windows (venv уже в репо-корне)
pip install -r requirements.txt
pip install pytest                 # pytest НЕ входит в requirements.txt
python main.py                     # GUI
python -m pytest tests -q          # полный сьют
```

Расширение: `chrome://extensions` → Developer mode → Load unpacked → `extension/`.
Локальный API: `http://127.0.0.1:19876` — `GET /api/status`, `POST /api/tasks` (роут `/api/queue` удалён: он возвращал payload статуса, см. X-01).

## 3. Гейты качества (единственный автоматический заслон)

| Гейт | Команда | Ожидание (база 2026-10-29) |
|---|---|---|
| pytest | `python -m pytest tests -q` | **581 passed / 30 skipped / 8 subtests** (37 модулей); все 30 скипов — Deno-зависимые, это штатный зелёный статус (не «чинить»!) |
| pytest c Deno | положить deno на PATH | Deno-сьюты (`test_js_engine.py`, `test_gateway_bypass.py`) оживают |
| Extension JS | `node --check extension/background.js extension/sieve_updater.js extension/content_script.js` | exit 0 |
| Extension тесты | `for f in extension/tests/*.test.mjs; do node "$f"; done` | **16/16 ALL PASS** |
| Линт | `pip install -e .[lint]` затем `python -m ruff check src/ tests/ --select E722,E402,E741,E731,E401,F401` | ровно 3 намеренных E402 в `webpage_parser.py:37-39` |

- CI/pyproject/linter-конфигов **нет** — не добавлять без запроса. pytest работает из любого cwd
  (`tests/conftest.py` кладёт корень и `tests/` в `sys.path`; импорты `src.*` и `helpers`).
- GUI-флоу автоматикой не покрыты — ручной смоук `python main.py` обязателен после правок GUI.
- E2E расширения — gitignored-харнесс `.ext-dev-loop/` (реальный Chrome-профиль + probe-скрипт;
  сначала `doctor.mjs --ext extension`; CfT-версия закреплена в `~/.ext-dev-loop/LOCAL.md`).
- Detализация гейтов: `GATES.md`. Свежие базовые числа: `Audit/2026-10-29_FULL_AUDIT.md` §1.

### Золотое правило паритета

Медиа-логика существует **дважды**: JS (`extension/`) и Python (`src/parser/`). Пары-близнецы:

| JS | Python |
|---|---|
| `applyUrlTransform`, `processLink` (background.js) | `site_pattern_manager.apply_link_url_transform` |
| `discoverFullsize` / `upgradeMedia` / `applySieveRulesAll` (sieve.js) | `SitePatternManager.transform_image_url` |
| `normalizeVariant` (sieve.js) | `normalize_variant` |
| `getSubstitution` (sieve.js) | `get_substitution` (общие векторы: `extension/tests/vectors/substitution.json`) |
| junk-фильтр (content_script.js) | `junk_filter.py` + `utils.py` |
| sieve merge (`sieve_updater.js`) | merge в `site_pattern_manager` («новейший файл выигрывает») |

**Изменение fullsize-upgrade/sieve/junk-поведения обязано попасть на оба борта и быть покрыто
в обоих сьютах (`tests/` и `extension/tests/`).** Пропуск паритета — самый частый итог прошлых аудитов.

## 4. Архитектура

### 4.1 Карта репозитория

```
main.py                 # вход: lxml/brotli-патчи → QApplication → MainWindow
src/
  app_paths.py          # портативные пути (dev vs frozen); task_state_path() — каноническая формула
  constants.py          # ВСЕ дефолты/ключи настроек/константы поведения (K.*) — единственный источник
  core/                 # TaskItem, TaskQueueManager (CRUD + persistence, снимок настроек в add_task)
  parser/
    parser_manager.py   # координатор: 1 инстанс = 1 задача; asyncio-воркеры в QThread
    webpage_parser.py   # HTML, lazy-load, гейтвеи, JS-редиректы
    priority_url_queue.py  # скоринг URL; кап DEFAULT_MAX_LINKS_PER_PAGE
    site_pattern_manager.py# паттерны + Imagus Sieve (static/deno)
    http_engine.py      # requests ↔ curl_cffi (TLS-имперсонация); авто-эскалация
    junk_filter.py, json_parser.py, shared_session.py, utils.py
    js_engine/          # опциональные Deno-воркеры: sieve-песочница, happy-dom, consent/age-гейт
  downloader/media_downloader.py  # single/multi-thread, rate limit, верификация медиа
  gui/                  # main_window (очередь, старт/пауза/стоп, extension-хуки), settings_dialog, log_handler
  server/http_server.py # ExtensionServer (aiohttp, 127.0.0.1:19876)
extension/              # MV3: background (service worker), content_script, sieve.js, sieve_updater.js, popup/, tests/
resources/              # dark_theme.qss, domain_blocklist.txt, junk_allowlist.txt, patterns/site_patterns.json
tests/                  # 37 pytest-модулей (+ helpers.py с пином sieve-файла, conftest.py)
docs/                   # DEV_GUIDE.md (этот файл), CHROME_WEB_STORE.md, archive/
Audit/                  # живой аудит + archive/ (всё датированное)
```

- `pattern_manager.py` — **DEPRECATED** алиас на SitePatternManager; не использовать и не «оживлять».
- Не коммитится как продукт: `venv/ build/ dist/ release/ downloads/ __pycache__/ settings.json
  task_queue.json sessions/ *.pkl *.log *.zip` и всё агентское (`AGENTS.md, Audit/, GATES*.md,
  knowledge.md, .ext-dev-loop/, .agents/` — локальные, gitignored).

### 4.2 Жизненный цикл задачи

1. Задача (GUI или расширение) → `TaskQueueManager.add_task` **снимает настройки** и `download_path`.
2. `MainWindow` на каждый запуск создаёт **новый** `ParserManager` + **новый** `QThread`.
3. `ParserManager` владеет asyncio-loop'ом, парсер/загрузочными воркерами, очередями загрузки/карантина
   и приоритетной URL-очередью.
4. Завершение всегда эмитит `task_ended(reason)` = `completed | stopped | failed`; очередь автостартует
   следующую задачу **ниже** завершённой (поставленные выше задачи сохраняют состояние).
5. Очередь персистится в `task_queue.json` рядом с exe (`app_paths.queue_path`), атомарно (tmp+fsync+replace).
   Состояние задачи: `{task_download_path}/sessions/{task_id}/last_session.pkl` — только через
   `app_paths.task_state_path()` (`clear_task_sessions` удаляет эти каталоги).

| Действие | Активная задача | *.part* | State-pickle |
|---|---|---|---|
| **Pause** | мягкий стоп, можно resume | сохранить | сохранить |
| **Stop** | жёсткий стоп, очереди очищаются | удалить | удалить |
| **Закрытие окна** | pause + сохранить очередь | сохранить | сохранить |

Внимание: `last_session.pkl` содержит куки, выученные на gateway/age-gate страницах, в открытом виде —
осознанное решение (см. CONTEXT.md §8); не выкладывать каталог загрузок публично.

### 4.3 Потоковая модель — железные правила

1. **Один `ParserManager` = одна задача.** Не переиспользовать после стопа — строить новый.
2. **Новый `QThread` на задачу.** Не перезапускать поток, переживший `quit()/wait()`.
3. **asyncio-примитивы** (`Event`, `Lock`, очереди) создаются **внутри** loop-потока, не в GUI-потоке.
4. **Кросс-поточный стоп:** `threading.Event` (`_thread_stop_event`) — download-потокам и монитору;
   `asyncio.Event` — loop-воркерам; оба выставляются в `stop_parsing()`.
5. Shared downloader-сессия — `Retry(total=0)` (`create_shared_downloader_session`), чтобы Stop срабатывал
   мгновенно; число повторов на закачку — из настроек.
6. `DOMAIN_CONCURRENCY_LIMIT = 2` (парсер+загрузчик **суммарно**, общий dict семафоров);
   `DEFAULT_MAX_LINKS_PER_PAGE = 200` (приоритизированные, топ-N).
7. Блокирующая работа — **только** в своих пулах: `_js_executor` (2 воркера) — JS-правила, `_dl_executor` —
   закачки, `_html_executor` — BeautifulSoup/sync-фетч; никогда инлайн в корутине (замер: стайл 5.02 c → 0.03 c).
   Пулы создаются в loop-потоке, закрываются в `finally` `_main_task`.
8. **Deno-воркеры одноразовые:** рецикл каждые `K.JS_WORKER_MAX_REQUESTS` (200) вызовов и/или по RSS
   (`K.JS_WORKER_MAX_RSS_MB` = 256) — `happyDOM.close()` память фактически не возвращает.
9. Windows: все `Popen` — с `CREATE_NO_WINDOW`, иначе консоль deno на всю задачу.
10. Пути — только через `src/app_paths.py`; константы/ключи настроек — только `K.*` из `src/constants.py`.

### 4.4 Конвейер медиа-логики

```
start_url → PriorityURLQueue (скоринг: медиа ×25, image-linked ×20, path-match ×3+, домашние ×0.005;
            капы MAX_URL_PATH_SEGMENTS/MAX_URL_LENGTH/MAX_LINKS_PER_PAGE)
→ parser workers (aiohttp; sync-фолбэк requests/curl_cffi; авто-эскалация 403/5xx, кроме 429;
   гейтвеи consent/age; JS-редиректы ≤5; JSON-API парсер)
→ webpage_parser._extract_* (изображения/видео/JSON-LD/srcset/picture)
→ transform_image_url: нативные паттерны → Imagus Sieve (первое совпавшее правило, варианты,
   SIEVE_LOOP_MAX_HOPS=5 для цепочек link→url→res) → глобальные трансформы → [Resize] → size-suffix strip
→ fullsize-дискавери для thumbnail-переходов (5 проб/страницу, timeout 8 c, бюджет 45 c)
→ junk_filter (ad-hosts, path-токены, banner-размер+third-party; allowlist resources/junk_allowlist.txt)
→ download_queue → MediaDownloader (HEAD/GET гейты, content-type, сниффинг первого чанка,
   max_file_size, single- или MT-по Range с общей сборкой .mt.partial → os.replace,
   карантин домена: 3 фейла → probation 5 c/1 ретрай)
```

## 5. Расширение (MV3)

- **Service worker классический** (без `type: module`) — `background.js` подключает `shared.js`,
  `sieve.js`, `sieve_updater.js` через `importScripts`. MV3-lifetime: все слушатели регистрируются
  синхронно на верхнем уровне; keepalive refcount + `chrome.alarms`; состояние сканов/батчей —
  `chrome.storage.local`/`storage.session` (кэш discovery с TTL).
- JS-правила sieve в расширении **не исполняются** (MV3 CSP) — `executeSieveJS` возвращает null by design;
  десктоп исполняет их через Deno (опция `js_engine=deno`).
- Обновление sieve: бандл `sieve.json` + онлайн-апдейт (`sieve_updater.js`, 8 MiB cap, зеркала jsDelivr);
  merge хранит `_`-пользовательские правила и `off`-флаги.
- Паритет и тесты: 18 node-сьютов в `extension/tests/` (см. §3). Изменения паритетных пар — оба борта.
- **Firefox (E-02):** отдельный `extension/manifest.firefox.json` — Chrome-манифест остаётся
  `service_worker`-only (Chrome отвергает `background.scripts` в MV3). У FF background — обычные
  скрипты `shared.js`, `sieve.js`, `sieve_updater.js`, `background.js` (в этом порядке, один общий
  global); `importScripts` в `background.js` под guard, поэтому один и тот же код работает в обоих.
  Установка: копия каталога, где `manifest.firefox.json` → `manifest.json`; `about:debugging` →
  This Firefox → Load Temporary Add-on. HTTP-сервер принимает `moz-extension://` (C-04);
  **живой QA в Firefox — обязательный шаг перед релизом** (здесь FF нет).
- **Известные паритетные гэпы (осознанно, не регрессы):** `loop`-правила sieve (X-06) исполняет
  только десктоп (`SIEVE_LOOP_MAX_HOPS=5`); JS-`res`-правило e-hentai (X-08) в MV3 неисполнимо —
  десктоп закрывает его нативным паттерном; `dc`-правила (X-07) не поддерживает никто и оба борта
  их явно скипают; allowlist junk (`resources/junk_allowlist.txt`) и правило banner-size+third-party
  остаются десктоп-онли (у расширения нет ФС и `pageUrl` в вызовах фильтра); таблицы ad-хостов и
  токенов синхронизированы (`tests/test_junk_parity.py`).

## 6. Рецепты типовых изменений

| Цель | Начать с |
|---|---|
| Новый дефолт/настройка | `src/constants.py` → `settings_dialog.py` → потребители в `parser_manager`/downloader |
| Приоритет/порядок обхода | `src/parser/priority_url_queue.py` |
| Извлечение HTML/гейтвеи | `src/parser/webpage_parser.py` (`_extract_images`, `_extract_videos`, `_extract_jsonld_media`) |
| Превью → fullsize | `site_pattern_manager.py` + `resources/patterns/site_patterns.json` / корневой Imagus sieve JSON — **и JS-твин + `extension/tests/`** |
| Sieve-трансформы | `site_pattern_manager.py` — `transform_image_url`, `apply_link_url_transform` |
| Надёжность загрузки | `src/downloader/media_downloader.py` |
| UI очереди / автозапуск | `src/gui/main_window.py`, `src/core/task_queue_manager.py` |
| Протокол расширения | `src/server/http_server.py` + `extension/` |
| Портативные пути/сборка | `src/app_paths.py`, `build_exe.py` (бандлит `bin/deno.exe` и `deno_cache/npm`) |
| Фильтрация краула (stop-words, skip URL) | `src/parser/utils.py` — `should_skip_crawl_url` (сегментный) |
| HTTP-движок/TLS | `src/parser/http_engine.py` (requests ↔ curl_cffi эскалация) |
| JS-гейтвей (Deno, consent/age) | `src/parser/js_engine/engine.py` + `gateway_worker.js`/`dom_worker.js` |
| Junk/превью-фильтрация | `src/parser/junk_filter.py` + `resources/junk_allowlist.txt` |

Deno-резолюция (`find_deno_bin`): `WEB_MEDIA_PARSER_DENO`/`DENO_BIN` env → пакет `deno` (PyPI) →
`bin/deno.exe` рядом с frozen-exe → PATH. Сит-тесты Deno скипаются автоматически, если бинарника нет.

## 7. Сборка и релиз

```bash
python build_exe.py    # PyInstaller onedir → dist/WebMediaParser/ (+ bin/deno.exe, sieve, allowlist)
```

Релиз = два zip: `WebMediaParser_v<ver>.zip` (приложение) и `WebMediaParser_extension_v<ver>.zip`
(расширение, пакет для Chrome Web Store) — складывать в `release/`. Firefox-сборка — отдельный zip
из копии `extension/` с `manifest.firefox.json` вместо `manifest.json`; Chrome-манифест в неё не попадает. Артефакты сборки в git не попадают.
`setup.py` зеркалит `requirements.txt` (13 зависимостей) и служит для `pip install .`;
канон для приложения/сборки — `requirements.txt`. Dev-инструменты — extras: `pip install -e .[test,lint]`.
CWS-публикация: `docs/CHROME_WEB_STORE.md` (лимит summary — 132 символа).

## 8. Стиль и дисциплина

- Karpathy-дисциплина (`.agents/skills/karpathy/SKILL.md`): подумать до кода; простота прежде всего;
  хирургические правки в стиле окружения; цель — падающий тест или конкретный шаг верификации.
- Модули: shebang + `# -*- coding: utf-8 -*-` + module docstring, где заведено. Type hints на новых
  публичных API. Логирование `logging.getLogger(__name__)`; GUI-лог через `GUILogHandler`.
- Тяжёлая работа никогда не в GUI-потоке (PySide6 сигналы/слоты).
- **Не делать:** не оживлять `pattern_manager.py`; не «чинить» 30 Deno-скипов; не рефакторить файлы целиком
  при узком баге; не добавлять scrape-bypass сверх легитимного клиентского поведения; не коммитить
  секреты/личный `settings.json`/артефакты сборки.

## 9. Карта документации

| Документ | Роль |
|---|---|
| `docs/DEV_GUIDE.md` | этот гид — канон для разработки |
| `AGENTS.md` | компактная памятка агенту (дублирует §3–§6 сжато) |
| `README.md` / `README_EN.md` | пользовательские фичи, установка, расширение |
| `CONTEXT.md` | as-built архитектура (глубже гидa; при расхождении верен код) |
| `knowledge.md` | короткие заметки-готчи (Freebuff context) |
| `GATES.md` | исполнимый реестр гейтов (CHECK/EXPECT) |
| `Audit/2026-10-29_FULL_AUDIT.md` | **открытый бэклог** + актуальные базовые числа |
| `Audit/README.md`, `Audit/archive/` | индекс и история аудитов |
| `docs/CHROME_WEB_STORE.md` | листинг для Chrome Web Store |
| `docs/archive/` | исторические дизайн/план-документы (Deno-движок, улучшения краула) |

## 10. Состояние бэклога

Бэклог аудита 2026-10-29 **внедрён полностью**; по каждому ID статус и место — §7 в
`Audit/2026-10-29_FULL_AUDIT.md`. Что стоит помнить при следующих изменениях:

- **Пауза/session:** in-flight страницы и загрузки хранятся наборами (`_inflight_pages`,
  `_inflight_downloads`), восстановленная страница удаляется из `processed_urls` — иначе резюм молча
  теряет элемент. One-shot список расширения — одноразовый (`task._pending_downloads = None` в GUI +
  идемпотентный `_seed_pending_downloads`).
- **Эскалация HTTP:** страничный фетч всегда идёт через aiohttp, поэтому `should_escalate(...)` вызывается
  с `primary_is_browser_tls=False`; эскалационная сессия обязана нести `SETTING_PROXY`.
- **Паритет URL:** `normalize_variant`/`normalizeVariant` — единственные нормализаторы (host-like → https,
  `//` → схема страницы, путь → от страницы); `apply_link_url_transform` и `res`-матчи расширения идут
  через них. Менять — только парой и с общими векторами.
- **GUI/потоки:** при смене задачи отключаются все пять сигналов; Stop ждёт `loop_thread` (а не только
  QThread); конструктор `ParserManager` под try/except с откатом в FAILED; `app.lock` не даёт двум
  экземплярам перетирать `task_queue.json`.
- **Осознанные гэпы** перечислены в §5 (X-06/X-07/X-08, allowlist, banner-size).
