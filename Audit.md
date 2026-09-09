# Audit.md — полный инженерный аудит репозитория (2026-09-09)

**Дата:** 2026-09-09 · **Baseline:** коммит `7bac98b` (после `chore: extend review prompt scope…`).
**Метод:** сплошное чтение кода всех модулей приложения и расширения, прогон тестов,
линта и статического анализа, точечные runtime-проверки подозрений. Предыдущий аудит
(2026-08-16) заменён этим документом — история сохранена в git.

**Прогон валидации:**
- `python -m pytest tests -q` → **265 passed, 30 skipped** (12.3 c). Скипы: Deno-зависимые
  тесты (нет deno на PATH в этом окружении), curl_cffi-тесты (P3), сетевой плейсхолдер.
- `python -m pyflakes src/ tests/` → 1 замечание (намеренный side-effect import `brotli`
  в `shared_session.py`, помечен `noqa`, но pyflakes его не понимает).
- `python -m ruff check src/ tests/` → 120 ошибок, из них 116 — E701/E702 (компактный
  стиль с `;` — намеренный, соответствует Karpathy-дисциплине проекта, чинить не нужно);
  реальные: 1×E722 (bare `except`), 1×E402 (import после кода), 1×E741, 1×E731.
- Type-checker: mypy отсутствует в проекте (не настроен).

**Сводка по severity:**

| Уровень | Кол-во | Ключевое |
|---|---|---|
| Critical | 1 | MT-download пишет в закрытый файл — фича «Threads per File > 1» не работает |
| High | 3 | Загрязнение глобальных настроек из HTTP-потока; мёртвый Referer (`_source_url`); неверный путь очистки session-файлов |
| Medium | 7 | Гонка очереди из HTTP-потока; `verify=False`; unbounded `wait()`; sieve: `off`/`loop`/`data:,` игнорируются; рассинхрон sieve-версий расширения |
| Low | 8 | Bare except, мёртвый код, стиль, мелкие edge-case'ы |

---

# БЛОК A — Приложение (Python)

## A-1 · CRITICAL — `_download_chunk` пишет остаток буфера в уже закрытый файл; «Threads per File > 1» не работает никогда

`src/downloader/media_downloader.py:642-672`

```python
with open(filename, "wb") as f:
    for chunk_data in response.iter_content(chunk_size=network_chunk_size_thread):
        ...
        if len(write_buffer_chunk) >= K.WRITE_BUFFER_SIZE:
            f.write(write_buffer_chunk); write_buffer_chunk.clear()
if write_buffer_chunk: f.write(write_buffer_chunk)   # ← ВНЕ with-блока!
```

`f.write()` вызывается **после** выхода из `with open(...)`, т.е. на закрытом файле.
**Воспроизведено:** `ValueError: write to closed file`. Последний недописанный буфер
(< 1 MB) всегда непуст → каждый чанк падает → `progress_dict["success"] = False` →
`_download_with_threads` возвращает ошибку → `_do_download` молча фолбэчится на
single-threaded (лог «Multi-threaded download failed … falling back»). Так как размер
файла практически никогда не кратен 1 MB, **multi-threaded-ветка не отрабатывает ни разу**:
настройка «Threads per File > 1» и rate-limiter для MT (DL-12) — мёртвый код.

Тест не ловит баг: `test_mt_chunk_closes_response_on_error` покрывает только ошибку на
стриме; успешного MT-пути в тестах нет вообще.

**Фикс** (перенос flush внутрь `with`):
```python
with open(filename, "wb") as f:
    for chunk_data in response.iter_content(chunk_size=network_chunk_size_thread):
        if not progress_dict["success"]:
            return
        if self.stop_event and self.stop_event.is_set():
            with progress_lock:
                progress_dict["success"] = False
                progress_dict["errors"].append("Download manually aborted")
            return
        if chunk_data:
            write_buffer_chunk.extend(chunk_data)
            downloaded_this_chunk += len(chunk_data)
            with progress_lock:
                progress_dict["total"] += len(chunk_data)
                if self.progress_callback:
                    prog = min(99, int((progress_dict["total"] / total_size) * 100))
                    self.progress_callback(prog)
                if self.rate_limit > 0 and "_rate" in progress_dict:
                    rate = progress_dict["_rate"]
                    rate["bytes"] += len(chunk_data)
                    elapsed = time.time() - rate["start"]
                    expected_time = rate["bytes"] / (self.rate_limit * 1024)
                    sleep_needed = max(0.0, expected_time - elapsed)
                else:
                    sleep_needed = 0.0
            if sleep_needed > 0:
                time.sleep(sleep_needed)
            if len(write_buffer_chunk) >= K.WRITE_BUFFER_SIZE:
                f.write(write_buffer_chunk); write_buffer_chunk.clear()
    # Остаток — ВНУТРИ with, файл ещё открыт
    if write_buffer_chunk:
        f.write(write_buffer_chunk)
```

**Тест (заблокировать регрессию):** добавить успешный MT-кейс в `test_media_downloader.py`:
мок `session.get` с `iter_content`, возвращающим последовательность чанков (напр. 3 ×
600 KB — остаток в буфере обязателен), `threads_per_file=2`, `Accept-Ranges: bytes`,
`Content-Length` = сумма; после `_download_chunk` проверить `progress_dict["success"] is True`
и что файл `.part0` существует и равен ожидаемому размеру.

---

## A-2 · HIGH — Загрязнение глобальных настроек приложения из HTTP-потока расширения

`src/gui/main_window.py:1106-1113` (`add_tasks_from_extension`, выполняется в потоке aiohttp-сервера):

```python
settings = self.settings_dialog.get_settings()          # ← ЖИВОЙ dict диалога!
if user_agent:
    settings["user_agent"] = user_agent                 # ← мутация глобального состояния
if cookies:
    settings["extension_cookies"] = cookies             # ← мутация глобального состояния
```

`SettingsDialog.get_settings()` возвращает сам `self.settings` (`settings_dialog.py:970-974`),
а не копию. Строки выше **перезаписывают настройки всего приложения**: после первого
one-shot из расширения каждый последующий GUI-запуск (URL из поля, CSV-импорт) унаследует
UA и cookies последней вкладки расширения. Cookies скоупятся на start-домен (DL-7), но
UA остаётся навсегда, а при следующем сохранении настроек `extension_cookies` могут уйти
в `settings.json` на диск.

**Фикс:**
```python
settings = dict(self.settings_dialog.get_settings())    # копия — мутируем только её
if user_agent:
    settings["user_agent"] = user_agent
if cookies:
    settings["extension_cookies"] = cookies
```
Один символ в начале функции, и загрязнение исчезает: `add_task` всё равно делает
`deepcopy` для снапшота задачи.

**Тест:** в `tests/test_parser_lifecycle.py` или новом модуле — вызвать
`add_tasks_from_extension` с `cookies="session=abc"`, затем проверить, что
`settings_dialog.get_settings()` не содержит `extension_cookies`.

---

## A-3 · HIGH — Referer-заголовок никогда не отправляется: `_source_url` нигде не устанавливается

`src/parser/webpage_parser.py:129` и `:360`:

```python
source_url = self.settings.get("_source_url")
if source_url and source_url != self.url:
    request_specific_headers["Referer"] = source_url
```

`grep -rn "_source_url" src/` показывает **только чтения** — ключ нигде не пишется.
Ветка Referer для политики `auto` (по умолчанию) — мёртвый код: страницы фетчатся без
Referer, а многие фото-хостинги (vBulletin, phpBB, CDN) требуют его для отдачи медиа или
хотя бы не отдают его в лог. Собираемый контекст (`context["source_url"]`) существует —
WebpageParser получает его в `context`, но читает код из `settings`.

**Фикс:**
```python
source_url = self.settings.get("_source_url") or (self.context or {}).get("source_url")
```
в обоих местах (строки 129 и 360; второе — `_try_escalate_fetch`). Дополнительно можно
продублировать `settings["_source_url"]` при создании WebpageParser в
`ParserManager._invoke_parser` — это уберёт зависимость от контекста:
```python
settings_copy = dict(self.settings)
if context and context.get("source_url"):
    settings_copy["_source_url"] = context["source_url"]
```

**Тест:** unit-тест на `WebpageParser._get_content` с мок-сессией: проверить, что
`Referer` в заголовках запроса равен `context["source_url"]`, когда он задан, и политика `auto`.

---

## A-4 · HIGH — «Clear History» не удаляет session-файлы: неверный путь

`src/gui/main_window.py` (`_clear_download_history`):

```python
sessions_dir = os.path.join(self.download_dir, "sessions")   # ← неверный путь
if os.path.isdir(sessions_dir):
    import shutil
    shutil.rmtree(sessions_dir)
```

Session-файлы лежат **внутри папки каждой задачи**: `{task_download_path}/sessions/{task_id}/last_session.pkl`
(формула `app_paths.task_state_path`, `app_paths.py:66-77`), т.е. физически
`{download_dir}/{task_folder}/sessions/{task_id}/last_session.pkl`. Каталог
`{download_dir}/sessions` не существует в нормальной структуре → rmtree молча ничего не
удаляет, а `state.pkl` с URL/очередями остаются на диске после «Clear History».
Дополнительно `src/app_paths.py:sessions_dir()` — **мёртвый код**, его никто не вызывает
(это и породило путаницу с путём).

**Фикс:**
```python
# вместо os.path.join(self.download_dir, "sessions")
import shutil
deleted = 0
for entry in os.listdir(self.download_dir):
    entry_path = os.path.join(self.download_dir, entry)
    sess = os.path.join(entry_path, "sessions")
    if os.path.isdir(sess):
        try:
            shutil.rmtree(sess)
            deleted += 1
        except OSError as e:
            self.log_handler.error(f"Error deleting sessions in {entry_path}: {e}")
```
Либо вынести в `app_paths.py` функцию `clear_task_sessions(download_dir)` и покрыть её тестом.

**Тест:** создать фиктивную структуру `{tmp}/site_x/sessions/{id}/last_session.pkl`,
вызвать логику очистки (вынести в отдельную функцию для тестируемости) и проверить удаление.

---

## A-5 · MEDIUM — `TaskQueueManager` мутируется из HTTP-потока, несмотря на контракт «GUI thread only»

`src/gui/main_window.py:1128` (`self.task_queue.add_task(...)` внутри `add_tasks_from_extension`)
и `task_queue_manager.py:26-27` («All methods are synchronous and must be called from the
GUI thread»). Python-операции над списком атомарны под GIL, поэтому краха нет, но:
1. `queue`-проперти и `save()` из GUI-потока могут увидеть «полувставленную» задачу
   (insert + сигнал между ними);
2. статус «active» и `start_next`-решения принимаются по несогласованному снимку;
3. `save()` из таймера/closeEvent может записать JSON с задачей, ещё не вставленной
   полностью (на практике — атомарный insert, риск низкий, но контракт нарушен).

**Фикс (минимальный, безопасный):** собрать список задач в HTTP-потоке, а вставку
выполнить на GUI-потоке через уже существующий сигнал `extension_tasks_added`:
```python
# в add_tasks_from_extension: собрать payload и emit
self._pending_extension_payload = payload   # простое поле, читается в GUI
self.extension_tasks_added.emit(auto_start_id)
```
а в `_updateUiFromExtension` (GUI-поток) выполнить `add_task`. Альтернатива — threading.Lock
внутри TaskQueueManager вокруг всех публичных методов (дороже, но проще для аудита).

---

## A-6 · MEDIUM — `verify=False` на всех sync-фолбэках: TLS-верификация отключена

`webpage_parser.py` — `_sync_fetch` (fallback requests), `_execute_bypass`, `_try_escalate_fetch`;
`media_downloader.py` — `_try_escalate_get`. Все `session.get(..., verify=False)`.

Это MITM-риск: прокси/провайдер может подменить TLS-сертификат и подсунуть поддельный
медиафайл или HTML. Для download-утилиты это особенно важно (скачиваются бинарники).

**Фикс (мягкий, сохраняющий поведение):** вынести флаг в настройки:
```python
K.SETTING_VERIFY_TLS = "verify_tls"          # constants.py
K.DEFAULT_VERIFY_TLS = False                  # сохранить текущее поведение по умолчанию
# в settings_dialog — чекбокс (HTTP-вкладка), sanitize: isinstance bool
# в точках вызова:
verify = self.settings.get(K.SETTING_VERIFY_TLS, K.DEFAULT_VERIFY_TLS)
resp = session.get(url, headers=..., timeout=..., verify=verify, ...)
```
Либо как минимум однократный `logger.warning` при первом `verify=False` (паттерн
`_missing_warned` в `http_engine.py`). Не менять молча: некоторые пользователи намеренно
используют самоподписанные прокси.

---

## A-7 · MEDIUM — `on_task_ended` и `on_parsing_finished`: неограниченный `thread.wait()`

`src/gui/main_window.py` — `on_task_ended`: `self.parser_thread.wait()` без таймаута;
`on_parsing_finished`: `self.parser_thread.wait()` тоже. В `stop_parsing` уже есть bounded
`wait(10000)`, в `_launch_parser_for_task` — `wait(5000)`. Неограниченный wait на GUI-потоке
рискует заморозить окно, если QThread завис (например, Deno-воркер в состоянии гонки,
сеть в блокирующем вызове).

**Фикс:**
```python
if self.parser_thread and self.parser_thread.isRunning():
    self.parser_thread.quit()
    if not self.parser_thread.wait(10000):
        self.log_handler.warning("Parser thread did not stop within 10s")
```
(в обоих местах).

---

## A-8 · LOW — Bare `except` и прочий мусор от ruff

- `src/parser/priority_url_queue.py:75` — `except:` без типа (в `_get_domain`). Глотает
  всё, включая `KeyboardInterrupt/SystemExit`. Фикс: `except (ValueError, TypeError):`.
- `src/parser/site_pattern_manager.py:577` — переменная `l` (E741). Переименовать в `line`.
- `src/parser/http_engine.py:151` — `import requests as _requests` после кода (E402):
  намеренный lazy-импорт, оставить, добавить `# noqa: E402`.
- `tests/test_parser_lifecycle.py:139` — `cb = lambda: None` (E731): заменить на `def cb(): ...`.
- `tests/test_media_downloader.py:127` — `t1.start(); t2.start(); ...` (E702): стиль проекта,
  оставить.

---

## A-9 · LOW — Прочее

1. `src/parser/json_parser.py` — `_get_json` ловит все исключения внутри `parse()` и
   **повторно поднимает** (`raise`), а `ParserManager._invoke_parser` для JSON-ветки не
   оборачивает вызов: исключение уходит в `_parser_worker`, где страница считается
   ошибочной. Поведение корректное, но уровень логов шумный (`exc_info=True` на каждой
   JSON-ошибке). Опустить до `logger.debug` в `_invoke_parser` для JSON-пути — см. A-3 фикс.
2. `priority_url_queue.py` — `task_done()` — no-op (`pass`). Это совместимость с
   asyncio.Queue, но в `_parser_worker` вызывается `task_done()` дважды (внутри
   `_processed_lock` при дубликате и в `finally`) — безвредно только потому что no-op.
   Прокомментировать или убрать дубль (A-9 низкий приоритет).
3. `webpage_parser.py` — `_get_content`: при `403` с включённой эскалацией, если
   `curl_cffi` недоступен, `escalation_tried=True` ставится безусловно → **requests-fallback
   для 403 пропускается** (раньше 4xx тоже сразу возвращался — поведение не изменилось,
   но комментарий «same error as today» вводит в заблуждение). См. блок про эскалацию.
4. `media_downloader.py` — `_do_download`: HEAD-запрос на каждый файл — двойной round-trip
   для каждого медиа. Сознательное решение (проверка размера/типа), оставить.
5. `parser_manager.py:78` — `self.page_limit = self.settings.get("page_limit", 1000)`:
   строковый ключ вместо `K.SETTING_PAGE_LIMIT`. Мелочь консистентности.

---

# БЛОК B — Расширение Chrome (MV3)

## B-1 · MEDIUM — Рассинхрон версий sieve: расширение тащит 849 старых правил

`extension/sieve.json` — **849 правил от 2026.04.01**, `background.js:9` — жёстко
зашитый `SIEVE_VERSION = "2026.04.01"`. В корне репозитория лежит более свежий
`Imagus_sieve_2026.07.15_823.json` (823 правила), который desktop-сборка копирует в
`dist/` (`build_exe.py`), а расширение — нет. Итог: расширение и приложение работают с
разными наборами правил, обновление sieve в расширении только ручное (popup → Load file).
См. блок C — там полное решение (online update).

**Минимальный фикс:** синхронизировать `extension/sieve.json` с `Imagus_sieve_2026.07.15_823.json`
и поднять `SIEVE_VERSION` до `"2026.07.15"`. И ввести проверку: при загрузке попытаться
найти `sieve.json` рядом с `Imagus_sieve_*` — нет, это уже C-блок.

## B-2 · MEDIUM — `applyUrlTransform` заменяет `$n` в порядке возрастания: `$10` ломается

`extension/background.js` (`applyUrlTransform`):
```js
for (let i = 1; i < matchGroups.length; i++) {
  const placeholder = `$${i}`;
  if (result.includes(placeholder) && matchGroups[i] !== undefined) {
    result = result.split(placeholder).join(matchGroups[i]);
  }
}
```
`$10` содержит подстроку `$1` → после итерации `i=1` в `$10` останется `0` (или мусор).
Python-аналог в `site_pattern_manager.apply_link_url_transform` уже исправлен нисходящим
порядком («Substitute $n in DESCENDING order so $10 isn't corrupted»), а в JS — нет.

**Фикс:**
```js
for (let i = matchGroups.length - 1; i >= 1; i--) {
  const placeholder = `$${i}`;
  if (result.includes(placeholder) && matchGroups[i] !== undefined) {
    result = result.split(placeholder).join(matchGroups[i]);
  }
}
```

## B-3 · LOW — `chromeDownload`: TDZ-зависимость от `watchdog` в listener

`background.js` — `const watchdog` объявлен **после** `addListener(listener)`, а listener
вызывает `clearTimeout(watchdog)`. В MV3-окружении событие не может прийти между
синхронными строками (events — асинхронные), поэтому TDZ на практике не срабатывает, но
это хрупкая конструкция: любой рефакторинг, сделавший listener синхронным вызовом,
уронит background-воркер с `ReferenceError`.

**Фикс:** объявить `let watchdog = null;` до `addListener`, присваивать в callback'е
`chrome.downloads.download`, и в listener проверять `if (watchdog) clearTimeout(watchdog)`.

## B-4 · LOW — Прочее (расширение)

1. `popup.js` — после скана `mediaItems.push(...)` без дедупликации против
   `discoverFullsize`-результатов: один и тот же URL из `img` и из `res`-правила попадёт
   дважды (дедуп только внутри `scanPageMedia` через `seen`). Мелкая косметика.
2. `content_script.js` — `JUNK_PATTERNS` содержит `/\/(prev|next|close|...)\b/i`:
   паттерн с `\b` после `|` может отсечь легитимные `/photos/` при совпадении токена
   (на практике редко, но проверить на target-сайтах).
3. `background.js` — `discoverFullsize` не имеет per-page time budget (в отличие от
   desktop `FULLSIZE_DISCOVER_TIME_BUDGET`): 50 ссылок × 8s = до 400s на страницу.
   Добавить общий дедлайн 45s, как в приложении.

---

# БЛОК C — Сравнение sieve/fullsize-пайплайна с Imagus-Mass-Download-Mod

Референс: `d:\Arx\Software Downloads\_Images_EDIT-pack\Imagus-Mass-Download-Mod\src-mv3-overlay\`
(далее — «Mod»). Mod предлагает существенно более продвинутые механизмы; ниже — что именно
он делает и как перенести в наш проект. Ключевые механики Mod, отсутствующие у нас:
онлайн-обновление sieve, `loop`-цепочки, `off`/`dc`, `data:,`-шаблоны, in-page выполнение
JS-правил, merge пользовательских правил.

## C-1 · Online sieve update — добавить в расширение и приложение

**Что есть в Mod** (`background/service.js:74-200`, `855-895`):
- настройка `sieveRepository` (URL raw.githubusercontent, по умолчанию
  `kuzn123/Imagus-Sieve-RuBoard/master/update.txt`) → периодический fetch;
- conditional GET: `If-Modified-Since` по `sieveUpdateLast` → `304` = up to date;
- **jsDelivr-зеркало** (`jsDelivrMirror()` — конвертация raw.githubusercontent → cdn.jsdelivr.net)
  как автоматический fallback при ошибке/rate-limit GitHub;
- retry с экспоненциальным backoff (3 попытки) + fallback на локальный `/data/sieve.json`;
- **merge с сохранением пользовательских правил**: ключи, начинающиеся с `_`, переносятся
  из старого sieve в новый; `off`-флаги старых правил сохраняются на новых;
- авто-обновление раз в неделю через `chrome.alarms` (`alarm-sieve-update`,
  `autoUpdateSieve`), проверка при старте.

**Что у нас:** расширение — bundled `sieve.json` + ручная загрузка файла из popup,
`SIEVE_VERSION` зашит. Приложение — только ручной путь в Settings (или файл рядом с exe).

**Решение для нас:**
1. **Расширение:** добавить в popup поле «Sieve repository URL» (storage key
   `sieveRepository`), кнопку «Update» и функцию `updateSieve()` по образцу Mod:
   fetch URL → валидация объекта → `validRuleCount` (правила с `link`/`img`) > 0 →
   merge (`_`-правила + `off`) → запись в storage. Conditional GET + jsDelivr mirror +
   backoff + weekly alarm (MV3 `chrome.alarms`) — перенести как есть. `SIEVE_VERSION`
   заменить на `sieveUpdateLast` (timestamp), а не ручной номер.
2. **Приложение:** в `SettingsDialog` (HTTP-вкладка) — поле «Sieve update URL» +
   кнопка «Download latest». В `SitePatternManager` добавить метод
   `download_imagus_from_url(url, timeout=30)` (aiohttp-сессия не нужна — можно requests),
   сохранить в `resources/patterns/` и перезагрузить правила. **Контракт безопасности:**
   sieve — исполняемый код (JS через Deno, Python-колбэки через `exec`), поэтому скачивание
   с произвольного URL должно быть явным действием пользователя, а не автообновлением
   (в отличие от расширения, где sandbox MV3 ограничивает). Подписать рекомендацию:
   «только доверенные источники (официальный репозиторий Imagus)».

## C-2 · Поле `loop` (95 правил в `extension/sieve.json`, 96 в файле 2026.07.15) — не поддерживается

> **Исправлено 2026-09-09:** первоначальная редакция этого пункта описывала `loop` как
> «N итераций url-шаблона» — это неверно. Ниже — семантика, verified по исходникам Mod
> (`src/includes/content.js:1481` и `:4478`; `background/service.js:547`).

**В Mod (фактическая семантика):** `loop` — не счётчик итераций, а **битовая маска для
рекурсивного повторного разрешения результата**. После того как правило выдало строковый
результат `ret` (из `res` или url-трансформа), при `rule.loop & (use_img ? 2 : 1)`
(бит 1 — совпадение через `link`, бит 2 — через `img`) строка `ret` **скармливается обратно
в движок правил** (`PVI.find({href: ret})`) — т.е. ищется следующее правило, чей `link`/
`img`-регэксп соответствует уже полученному URL viewer'а, и разрешение продолжается.
Защита: счётчик `IMGS_loop_count` с жёстким лимитом 5 переходов и abort при self-reference
(`ret === trg.href`). Поле `loop_param` (в связке с `dc`, service.js:547) выбирает, какой
биты маски применяются для сторон link/img.

**У нас:** ни `site_pattern_manager._discover_linked_fullsize`, ни расширенческий
`discoverFullsize` не читают `loop` — одноразовая цепочка link→url→res обрывается на
первом шаге, для multi-hop правил (thumbnail → viewer → CDN) полный размер не находится.

**Решение (см. PLAN.md P3-3):** в `_discover_linked_fullsize` (desktop) и `discoverFullsize`
(extension) — ограниченный цикл повторного разрешения (≤ 5 переходов, константа
`K.SIEVE_LOOP_MAX_HOPS`): если `res` выдал ровно один URL, сам соответствующий другому
sieve-правилу, и `rule.get('loop') & 1` — повторить get_link_rule → apply_link_url_transform
→ fetch → extract с новым URL; накапливать только финальные media-URL. Стоп-условия: нет
нового совпадения, лимит переходов, self-reference (seen-set для transform-URL) и общий
страничный `FULLSIZE_DISCOVER_TIME_BUDGET` (45s) — новый бюджет НЕ вводится, поэтому
производительность не деградирует.

## C-3 · Поле `off` (8 правил) и `dc` (16 правил) игнорируются

**В Mod:** `"off": 1` — правило отключено пользователем (и это состояние сохраняется при
обновлении, см. C-1 merge); `dc` — domain-code: ссылка на другой домен/макрос поддомена.

**У нас:** `_load_imagus_file` не фильтрует `off` → 8 отключённых правил всё равно
применяются; `dc` не обрабатывается вовсе.

**Решение (desktop, `site_pattern_manager.py:_load_imagus_file`):**
```python
if rule_data.get("off"):
    continue   # не загружать отключённые правила
```
Для `dc` — на первом этапе просто логировать `logger.debug` (макросы редки и плохо
документированы в самих файлах), полную поддержку — отдельной задачей.

## C-4 · `data:,`-шаблоны (29 правил) дают мусорные URL

**В Mod:** `url: "data:,$&"` (напр. `[Google_Images]`) — **не fetch**, а маркер «использовать
совпадение как есть» (или встроенная data-заглушка). Mod обрабатывает `data:` отдельно от
http-фетчей.

**У нас:** `apply_link_url_transform` не знает про `data:`:
```python
if result.startswith('//'):
    result = 'https:' + result
elif not result.startswith(('http://', 'https://')):
    result = 'https://' + result     # → https://data:,... мусор
```
**Проверено:** `"data:,$&"` превращается в `https://data:,abc123` — запрос уходит в никуда,
правило молча не работает.

**Решение:** в `apply_link_url_transform` (и в extension `background.js`):
```python
if result.startswith("data:"):
    # data:-шаблоны — не URL для fetch; оставить ссылку на исходную страницу
    return None   # caller: fetch link_url сам (fallback на <img> scan)
```
Или, если правило подразумевает data-заглушку: вернуть специальный маркер, чтобы
`_discover_linked_fullsize` не делал fetch, а сразу применил `res` к загруженной странице.

## C-5 · JS-правила в расширении: in-page выполнение вместо `new Function`

**В Mod:** JS-правила (`to:`/`res:`/`url:` с `:`) выполняются **в контексте страницы**
(content script), где есть реальный `this.node`, `document`, hover-элемент; правила с
`IMGS_ext_data`/асинхронными запросами работают.

**У нас:** popup использует `new Function` → **заблокировано MV3 CSP** (EXT-7 уже
зафиксирован, UI честно пишет «JS — skipped in popup»); desktop использует Deno-воркер
(happy-dom, без `--allow-*`), который не может дать hovered-элемент и асинхронные правила.

**Решение:** перенести применение sieve-JS из popup в **content script** (у него есть
доступ к DOM страницы и к `document`, но не к hover-элементу; для `this.node` потребуется
shim по URL). Это как минимум вернёт правила, требующие простого DOM (closest/querySelector
по статической структуре). Deno-путь в desktop остаётся как есть — он уже покрывает
статические DOM-правила. Асинхронные правила (`IMGS_*`, fetch) — осознанно не поддержаны
(задокументировано в CONTEXT.md).

## C-6 · Прочие улучшения из Mod, которые стоит скопировать

1. **Валидация нового sieve перед заменой** (Mod): `validRuleCount === 0` → отказ;
   невалидный формат → ошибка, старый sieve сохраняется. У нас `_load_imagus_file` уже
   защищён try/except, но при скачивании нового файла нужно то же: не перезаписывать
   текущий, пока новый не провалидирован.
2. **`grantUrls`/grants** (Mod): per-site активация правил. Для desktop-краулера не нужно —
   у нас есть domain blocklist и `stay_in_domain`.
3. **Обратная совместимость merge** (Mod): при обновлении старые правила, отсутствующие в
   новом sieve, получают `off: 1` и сохраняются — пользовательские модификации не теряются.
   Для нас это означает: при скачивании нового sieve сохранять пользовательские правки из
   `resources/patterns/site_patterns.json` (native patterns) — они у нас и так отдельные файлы.

---

# БЛОК D — Тесты

**Покрытие в целом хорошее** (265 passed / 30 skipped, модули: url-detection, js-processing,
js-engine, pattern-manager, parser-manager-filtering, media-downloader, shared-session,
http-engine, gateway-bypass, crawler-frontier, fullsize-discovery, format-filter, junk-filter,
bugfixes, sec3-fixes, parser-lifecycle, parser-phase2/5, task-queue-persistence, http-server,
json-parser).

**Дыры (по убыванию важности):**

1. **Нет успешного MT-download кейса** — баг A-1 прошёл незамеченным. Обязателен тест из A-1.
2. **Нет теста на загрязнение настроек** из HTTP-потока (A-2) — добавить.
3. **Нет теста Referer/`_source_url`** (A-3).
4. **Нет теста очистки session-файлов** (A-4).
5. **Нет тестов `loop`/`off`/`data:,`** в sieve (C-2..C-4) — покрыть `apply_link_url_transform`
   на fixture-правилах с `off: 1` и `url: "data:,$&"`; для `loop` (бинарная маска
   повторного разрешения, бит 1 = link-side) — цепочка из двух правил (см. PLAN.md P3-3):
   правило с `loop: 1` резолвит thumbnail-link в viewer-URL, совпадающий со вторым правилом,
   финал — CDN-изображение; циклическая цепочка обязана оборваться на лимите переходов.
6. **`test_js_engine.py` / Deno-стек** скипается без deno на PATH — CI-окружение должно
   устанавливать deno, иначе P0/P1/P4-код остаётся недопроверенным (в этом прогоне 30
   скипов, из них большая часть — Deno/curl).
7. **GUI-логика (main_window) не тестируется** (Qt-зависимая): минимум — вынести чистые
   функции (`_make_task_folder_name`, очистку сессий, `_build_extension_items`) в модули без
   Qt и покрыть их.

---

# Остаточные риски (не исправляются безопасно автоматически)

1. **Deno JS-движок — выполнение произвольного JS из sieve-файлов.** `DenoJsEngine` запускает
   воркеры **без** `--allow-net/read/write/env` (корректно), но правила — непроверенный код
   из пользовательских sieve. Пока engine недоступен — fail-open на статику; это осознанный
   компромисс. **Не менять без согласования**: любые «усиления» в сторону
   `--allow-*`/network снимут sandbox, а запрет пользовательских sieve сломает фичу.
2. **Pickle `state.pkl` — потенциальный десериализационный риск** при Resume из
   подменённого файла. Папка `sessions/` — внутри download-папки пользователя; файл пишет
   только наше приложение. Полный отказ от pickle — большая переделка; оставить, но
   задокументировать «не давайте чужим файлам записываться в папки задач».
3. **`http_engine` импорты в `try/except` на уровне модуля** — curl_cffi отсутствует в
   venv (PKG-1 из прошлого аудита): `requirements.txt` содержит `curl_cffi>=0.14.0`, но
   venv его не имеет (проверено: `import curl_cffi` → ModuleNotFoundError). Все места
   fail-open, но P3-функциональность (эскалация, impersonation) в dev-окружении мертва.
   Решение — переустановить venv: `pip install -r requirements.txt`.
4. **Расширение отстаёт от приложения по sieve** — B-1; не фиксить «в лоб» без C-1,
   иначе опять разойдётся при следующем обновлении.
5. **GIF/SVG/ICO выключены по умолчанию** — часть галерей (эмодзи-паки, стикеры) будет
   пропущена; это намеренная настройка по умолчанию (Settings → Filters), не баг.

---

# Допущения

1. Сравнение sieve-механик проведено по исходникам Mod-расширения
   (`background/service.js`, `data/sieve.json`, `content/content.js`); runtime-прогон Mod не
   выполнялся (это сторонний проект вне репозитория).
2. Баг A-1 воспроизведён изолированно (запись в закрытый файл) и подтверждён чтением
   кода; полный сквозной MT-прогон с сетью не выполнялся (нет живого Accept-Ranges-сервера
   в окружении) — тест из A-1 закроет это.
3. Ключи настроек считались каноническими из `src/constants.py` (`K.*`); несоответствия
   (`page_limit` строкой) отмечены как minor.
4. «Clear History» не удаляет скачанные файлы — по заявленному поведению UI; аудит не
   меняет этого, только чинит session-путь (A-4).
5. Стиль `t1.start(); t2.start(); ...` и компактные однострочники — намеренная
   Karpathy-дисциплина проекта; ruff E701/E702 не чинились (кроме E722 — реальный баг).
6. Предыдущий `Audit.md` (2026-08-16) содержал ~90 находок; большинство из них уже
   исправлены в коде (маркеры `CORE-*`, `DL-*`, `EXT-*`, `GUI-*`, `PAT-*` в комментариях).
   В этот аудит вошли только **нерешённые** проблемы + новые, найденные при повторном
   проходе. История — в git.