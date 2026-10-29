# Deno как лёгкий JS-движок — анализ и дизайн интеграции

> **As-built поправки (2026-08-16, сверка с кодом):**
> 1. Модуль реализован как пакет `src/parser/js_engine/` (`engine.py` + воркеры), а не одиночный `js_engine.py`, как в эскизе §3.1.
> 2. Публичный API: `run_js` (P0), `run_dom` (P1), `run_gateway_click` (P4), `available()`, `shutdown()`; `render_html()` из эскиза не реализан — вместо него DOM-eval url/res-правил (см. §4, P1-DOM).
> 3. Таймаут вызова: `DEFAULT_CALL_TIMEOUT = 2.0 c` (engine.py:51), а не 5.0 из эскиза; таймаут включает холодный старт Deno — известная проблема (Audit DL-8). Настройки `js_engine_timeout` / `js_render_wait_ms` из §3.3 НЕ реализаны.
> 4. Протокол gateway (§7.3): фактически `{"id":0,"html":...,"pageUrl":...,"textPatterns":[...],"overlaySelectors":[...]}` — поле `op`/`timeout` не используется, таймаут на Python-стороне; воркер отвечает `{id, ok|error, ...}` (gateway_worker.js).
> 5. Права воркеров: без ЕДИНОГО `--allow-*` (дизайн §3.1 допускал `--allow-net=<цель>`); внешние `<script src>` не загружаются — inline-only.
> 6. Бинарник Deno ищется и из venv-пакета `deno`, и рядом с exe (`bin/deno.exe`) — см. `_find_deno` в engine.py.
>
> **Дата:** 2026-08-07 (обновлено 2026-08-10; as-built сверка 2026-08-16)
> **Статус:** P0 РЕАЛИЗОВАН (2026-08-08): src/parser/js_engine/ (engine.py + worker.js), интеграция в SitePatternManager (to_js), настройка js_engine: static|deno (дефолт static), сборка бандлит bin/deno.exe + bin/worker.js. P1 РЕАЛИЗОВАН (2026-08-08): DOM-режим — dom_worker.js (happy-dom 15.11.7, enableJavaScriptEvaluation=false, без прав), DOM-eval JS url/res правил sieve (apply_link_url_transform + extract_res_urls), офлайн-кэш happy-dom бандлится в bin/deno_cache/npm. P2-lite РЕАЛИЗОВАН (2026-08-08): src/parser/junk_filter.py — точный классификатор ad/трекер/форумный хром (suffix-матч хостов, path-токены, слабый сигнал размера только в паре с кросс-доменом), allowlist junk_allowlist.txt, финальный гейт в _process_media_batch, отсечка apple-touch-icon на парсинге; настройка filter_junk (дефолт on). P1.5 РЕАЛИЗОВАН (2026-08-08): this.node/TRG в dom_worker.js — живой DOM-элемент для res-правил (a-якорь по href, img по группам), this.find({href|src}), guards на querySelector/closest/src, фолбэк на document (не на первый элемент) со стубами () => null — чистый fail-open без мусорных URL. P3 РЕАЛИЗОВАН (2026-08-10) + P3-esc (авто-эскалация на 403/5xx). P4 РЕАЛИЗОВАН (2026-08-10, см. §7): JS-гейты consent/age — статическое извлечение consent-кук из onclick/функций (всегда) + точечный DOM-клик через gateway_worker.js (при js_engine=deno; JS-эвалуация включается только в этой операции) + consent-кэш по доменам. Заодно исправлен латентный баг: sync-куки теперь уходят в aiohttp re-fetch явно (cookie_jar.update_cookies ненадёжен для IP/бес-доменных кук). 191 passed, 1 skipped. P2-full — отложен; P5 (CF-PoW) — отклонён (тупик).
>
> **Боевая проверка (2026-08-08, deno включён):** запуск без видимых ошибок; fullsize-дискавери дал 1940 media (было 0); DOM rule errors 0 (было 21 — фикс `$._`); окна deno.exe больше не появляются (CREATE_NO_WINDOW). Поздние фиксы: `$._` (сырой текст страницы, Imagus-конвенция) + рекурсивное расплющивание вложенных массивов в dom_worker.js; CREATE_NO_WINDOW/start_new_session в engine.py.
> **Контекст:** `Audit.md` в корне репо (полный аудит 2026-08-16; старые аудиты из `Audit/` удалены), `docs/DEV_GUIDE_MEDIA_CRAWL_IMPROVEMENTS.md` (рабочий план улучшений).
> **Жёсткие ограничения:** никаких headless-браузеров, Playwright/Puppeteer запрещены; лёгкий путь — статический HTML + эвристики + правила + HTTP-байпас.

---

## 1. Зачем это нужно

Главное ограничение парсера — **отсутствие JS-движка**:
1. JS-правила Imagus sieve (`to` со `:`-выражениями) сейчас просто **дропаются** (`site_pattern_manager._needs_dom` → `js_skipped`).
2. Lazy-load/JS-генерируемый контент достаётся только статическим сканером (`_handle_dynamic_content`) — без выполнения скриптов страницы.
3. JS-челленджи (YouTube `player.js`, устаревший Cloudflare «Just a moment») требуют выполнения JS.

Идея пользователя: использовать **Deno** как подпроцессный JS-движок через пакет `deno` на PyPI (официальный редистрибутив бинарника Deno, `pip install deno`). Проект-ориентир: `mhogomchungu/media-downloader`, который уже так делает.

---

## 2. Что доказано фактами (проверено, не предположения)

### 2.1 Пакет `deno` на PyPI — официальный
- Автор: «the Deno authors» (deno.com), repo `github.com/denoland/deno_pypi`.
- `deno==2.9.5` → бинарник Deno v2.9.5, **Windows x86_64 поддерживается**, wheel ~40 МБ, `requires_python >=3.10`.
- API: `deno.find_deno_bin()` → путь к бинарнику; запуск через `subprocess`. Всё.
- Для onedir-сборки: +40–60 МБ к `dist/WebMediaParser/`.

### 2.2 Как это делает media-downloader (ваш ориентир)
Доказательство — ваш же `Log.txt` из этого репозитория:
```
[media-downloader] "yt-dlp.exe" "--no-js-runtimes" "--js-runtimes" "deno:C:/Users/.../bin/deno.exe" ... "https://www.youtube.com/watch?v=..."
[youtube] [jsc:deno] Solving JS challenges using deno
[youtube] ajCvO9iCYMs: Downloading player dfc3d2b2-main
```
**Механика:** media-downloader (Qt GUI над yt-dlp) бандлит бинарник Deno в `bin/` и передаёт его yt-dlp флагом `--js-runtimes deno:<path>`. yt-dlp запускает Deno как подпроцесс для **выполнения JS-челленджей YouTube** (`player.js`: распутывание сигнатуры и параметра `n`). Это и есть «обход Cloudflare для доступа к видео YouTube» — на самом деле это **JS-челленджи самого YouTube**, которые решаются в Deno-подпроцессе. Строка `[jsc:deno]` в логе — прямое подтверждение.

### 2.3 Что Deno может и чего не может (честная оценка)

| Задача | Deno сам по себе | Комментарий |
|---|---|---|
| Imagus JS-правила (`to` со `:`) | ✅ **Да, хорошо** | Простое выполнение выражения с `$[n]` шимом — идеальный кейс |
| Lazy-load/JS-контент страницы | 🟡 Частично | Deno + `linkedom/jsdom`: выполнит скрипты, вернёт DOM. Нет layout/WebGL — часть сайтов не отрендерится |
| YouTube `player.js` челленджи | ✅ Да (как в yt-dlp) | Для нашего приложения неактуально (мы не качаем YouTube) |
| Cloudflare «Just a moment» (старый JS-PoW) | 🟡 Иногда | Выполнить челлендж-JS можно, но TLS-отпечаток (JA3/JA4) Deno **не браузерный** → CF блокирует ещё на handshake |
| Cloudflare Turnstile / Managed Challenge (2024+) | ❌ Нет | Требует реального браузера (Canvas/WebGL/навигатор-проверки) |
| Проход пассивных CF-проверок | ❌ Нет | Deno `fetch()` (rustls/токио) легко определяется — Deno issue #31299 |

**Вывод по CF:** «чистый» Deno CF-челленджи **не** проходит. Проверенные в 2025–2026 лёгкие пути:
1. **Cookies реального браузера** (`cf_clearance`) + совпадающий UA — самый надёжный и дешёвый. **У нас это уже есть**: расширение передаёт cookies в десктоп (`extension_cookies` → `shared_session`).
2. **TLS-impersonation** (`curl_cffi`, `impersonate="chrome"`) — проходит пассивные проверки, но не Turnstile. Можно добавить как опцию HTTP-движка (отдельное решение).
3. Deno — для CF не является решением, но **отлично закрывает задачи 1–2 таблицы** (sieve JS + отрисовка страниц).

---

## 3. Архитектура интеграции (проект)

### 3.1 Новый модуль `src/parser/js_engine.py`
```python
class DenoJsEngine:
    """Лёгкий JS-движок на базе Deno (subprocess, JSON-over-stdio)."""

    def __init__(self, settings):
        self._bin = None            # кэш deno.find_deno_bin()
        self._proc = None           # ленивый long-lived воркер
        self._lock = threading.Lock()

    def available(self) -> bool: ...            # find_deno_bin() + версия
    async def run_js(self, code, timeout=5.0):  # выполнить выражение → str | None
    async def render_html(self, html, url, wait_ms=1500): ...  # P1: jsdom/linkedom pass
    def shutdown(self): ...                     # kill при Stop
```

**Ключевые решения:**
- **Протокол:** JSON-строки по stdin/stdout, по одной на вызов (формат как в `background.js`-sieve, но для Python-сайда).
- **Запуск:** `deno run --allow-net=<цель> --allow-read=<tmp> worker.ts` — минимальные права (`--allow-all` запрещён). Воркер живёт долго (переиспользуем V8-контекст), убивается при Stop (`proc.terminate()`), таймаут на каждый вызов.
- **Не блокировать event loop:** все вызовы через `loop.run_in_executor(None, ...)` (как уже сделано для `MediaDownloader.download`).
- **Дефолт — выключено:** настройка `SETTING_JS_ENGINE = "static" | "deno"` (по умолчанию `"static"` — поведение не меняется). Даже при включённом — **fallback на статический парсинг** при любой ошибке движка (fail-open, по DEV_GUIDE).

### 3.2 Точки интеграции (по приоритету)

**P0 — Imagus sieve JS-правила (максимальный эффект, минимум кода)**
- Сейчас: `site_pattern_manager._try_parse_imagus_js` конвертирует подмножество JS→Python, остальное `js_skipped`.
- С Deno: `SitePatternManager` получает опциональный `js_runner`; правила со `:` отправляются в Deno: воркер выполняет `(function(m){ <rule> })(matchData)` с шимом `$[n]`, `#ext#`, возвращает строку/`\n`-варианты.
- `transform_image_url` возвращает варианты как сейчас — остальной пайплайн не трогаем.
- **Метрика:** замерить `js_skipped` на `Imagus_sieve_2026.04.01_849.json` до/после.

**P1 — render-pass для JS-контента страницы**
- В `WebpageParser.parse()`, когда `process_js` И движок включён: после статического `_extract_images/_extract_videos/_extract_links` отправить HTML в Deno-воркер (`linkedom` или `jsdom`), подождать `wait_ms` (настраиваемо, дефолт 1500), получить «отрендеренный» HTML, **повторно** прогнать `_extract_images` + `_handle_dynamic_content`. Дедупликация уже есть (`downloaded_files`, `seen`).
- Ограничение честно документировать: нет layout → lazy-триггеры типа IntersectionObserver не сработают; поможет только для скриптов, строящих DOM напрямую.

**P2 — (экспериментально, маркировать в UI) старый CF-JS-челлендж**
- Попытка выполнить PoW-скрипт «Just a moment» старого типа в Deno → `cf_clearance`-cookie. Заведомо ненадёжно против Turnstile. Если не взлетает — не чинить, оставить как эксперимент.

**P3 — (отдельное решение) curl_cffi как HTTP-движок**
- Не Deno. Опция `SETTING_HTTP_ENGINE = "aiohttp" | "curl_cffi"` с `impersonate="chrome"` для снижения блокировок на уровне TLS. Синергия: cookies расширения + браузерный TLS-отпечаток.

### 3.3 Настройки (новые ключи в `src/constants.py`)
```
SETTING_JS_ENGINE        = "js_engine"         # "static" | "deno", дефолт "static"
SETTING_JS_ENGINE_TIMEOUT= "js_engine_timeout" # сек, дефолт 5
SETTING_JS_RENDER_WAIT_MS= "js_render_wait_ms" # P1, дефолт 1500
SETTING_HTTP_ENGINE      = "http_engine"       # P3, дефолт "aiohttp"
```
UI: вкладка HTTP → «JS Engine: Static / Deno (experimental)» + чекбоксы. Все дефолты сохраняют текущее поведение.

### 3.4 Сборка и зависимости
- `pip install deno` → `requirements.txt` (опциональная зависимость, т.к. движок выключен по умолчанию — `deno` можно держать в extras или проверять `import deno` лениво).
- `build_exe.py`: скопировать бинарник `deno.find_deno_bin()` в `dist/WebMediaParser/bin/` (в onedir, рядом с exe), не бандлить через PyInstaller `--add-binary` (большой wheel).
- Ленивая проверка в `js_engine.py`: `importlib.util.find_spec("deno")` → если нет, движок недоступен, лог-предупреждение.

### 3.5 Безопасность
- Воркер запускается с `--allow-net=<host>` и без файловых прав; правило из sieve — непроверенный код → **не давать** доступ к диску/сети воркеру (только stdio). Аналогично `executeSieveJS` в расширении: JS-правила из пользовательских sieve-файлов выполнять только в Deno-песочнице.
- Производительность: P0 — ~30–80 мс на правило (subprocess переиспользуется), приемлемо; P1 — один лишний проход на страницу только при включённой опции.

---

## 4. Дорожная карта (статус на 2026-08-08)

```
[✓] P0      js_engine/engine.py + worker.js + Imagus to-правила через Deno (48 правил)
[✓] P1-DOM  dom_worker.js (happy-dom 15.11.7, без прав) + DOM-eval url/res правил
           sieve (323 res + 45 url правил); $._ (сырой текст страницы) + вложенные
           массивы; офлайн-кэш npm бандлится в bin/deno_cache/npm
[✓] P2-lite junk_filter.py — ad/трекер/форумный хром, allowlist, финальный гейт,
           отсечка иконок на парсинге (фильтр junk: дефолт on)
[✓] P1.5     this.node/TRG в dom_worker.js — живой DOM-элемент для res-правил
           (a-якорь по href, img по группам), this.find({href|src}), guards на
           querySelector/closest/src; фолбэк на document (не первый элемент),
           стубы () => null на Document — чистый fail-open. Fetch/XHR (110) и
           IMGS_ext_data (86) — асинхронные, вне синхронного пайплайна.
[✓] P0.5     this.node-шим в worker.js (P0): url-правила с this.node больше
           не падают с 'reading node' (20 правил) — lazy-ветки работают
           ($[2] ? ... : this.node...), DOM-зависимые ветки тихо fail-open.
           + expression-wrapper: IIFE `(()=>{...})()` возвращают значение
           (раньше undefined — голый body не возвращает trailing expression);
           wrapCache — решение кэшируется, двойная компиляция исключена.
[✓] P3       curl_cffi impersonation как HTTP-движок sync-путей (2026-08-10):
           http_engine: aiohttp|curl_cffi (дефолт aiohttp), профиль impersonate
           (дефолт chrome). Основной async-fetch остаётся aiohttp; curl_cffi
           применяется к фолбэк-загрузке/гейтвеям (webpage_parser sync-сессия)
           и скачиванию медиа (shared downloader session). Fail-open: нет
           curl_cffi/невалидный профиль → requests + одно предупреждение.
           UA не переопределяется на impersonate-сессии (рассинхрон JA3↔UA).
           Исключения: NETWORK/HTTP_ERROR_EXCEPTIONS покрывают оба движка.
           Сборка: --collect-all=curl_cffi (libcurl-impersonate статически в
           _wrapper.pyd). 165 passed, 1 skipped; фингерпринт подтверждён
           (JA4 t13d1516h2, HTTP/2, браузерный UA); скачивание в бандле OK.
[✓] P3-esc  Авто-эскалация на явную блокировку (2026-08-10): при HTTP 403/5xx
           (после исчерпания ретраев для 5xx) — ОДИН bounded запрос через
           curl_cffi browser-TLS. Прозрачно для карантина: успех → failures
           не растёт; провал → та же ошибка/статус, что и без эскалации.
           429 не эскалируется (rate-limit backoff уже есть); при глобальном
           http_engine=curl_cffi эскалация отключена (не дублируем curl).
           Настройка http_escalate (дефолт on, Settings → HTTP). E2E-тест с
           локальным блокирующим сервером (requests 403 / curl 200): без
           эскалации fail, с эскалацией файл скачан. 173 passed, 1 skipped.
[ ] P2-full  Ghostery adblocker (отложено — текущие эвристики покрывают нужды)
[x] P4       JS-обход интерстициальных прокладок — РЕАЛИЗОВАН (2026-08-10):
           consent/age-гейты. Уровень 1 (всегда): статическое извлечение
           consent-кук из onclick (document.cookie, setCookie/createCookie,
           inline-функции, location.reload-флаг), JS-кандидаты в
           _handle_gateways (вкл. div/span[onclick]), overlay-скоуп, блэклист
           GATEWAY_AVOID_KEYWORDS, скип password-форм. Уровень 2 (deno):
           gateway_worker.js — happy-dom с JS-эвалуацией только для клика,
           перехват location.reload/href до клика (без stack-overflow),
           diffCookies + html_after (мутированный DOM парсится без рефетча).
           Consent-кэш по доменам в ParserManager (гейт обходится 1 раз/домен).
           Фикс: sync-куки явно в aiohttp cookie= (cookie_jar ненадёжен).
           Тесты: tests/test_gateway_bypass.py (18) + e2e с локальным
           JS-гейтом. 191 passed, 1 skipped.
[x] P5       старый CF-JS-PoW (отклонён — тупик против Turnstile)
```

**Боевые фиксы после первого запуска с Deno (2026-08-08):**
- `CREATE_NO_WINDOW` (Windows) / `start_new_session` (Unix) в обоих `Popen` — иначе каждое `deno run` открывало видимое консольное окно на всю задачу.
- `$._ = htmlStr` в dom_worker.js — Imagus-конвенция: res-правила читают сырой текст загруженной страницы (`$._.match(...)`); без этого 504 правила давали `reading 'match'` и fullsize-дискавери = 0. Плюс рекурсивное расплющивание вложенных массивов (`[[['#url']]]`) со снятием `#`-маркера.
- P1.5: `this.node` — поиск элемента в DOM (a[href] по URL из контекста exact/relative, img[src] по группам regex); Proxy-шим: `src` от первого img внутри, `closest/querySelector` от якоря; фолбэк на `document` с стубами `() => null` — правила вроде Google_Images (`closest('a')`) и CNN-m-pp (`querySelector('img')`) работают, а при отсутствии матча — честный null без мусорных URL лого/навигации.
- P0.5: `this.node`-шим в worker.js — url/to-правила с `this.node` (20 в sieve) больше не кидают `reading 'node'`: lazy-ветки (`$[2] ? X : this.node...`) дают результат, DOM-ветки — тихий fail-open (src/closest/querySelector → null/[]). Плюс expression-wrapper: голый body не возвращает значение trailing-выражения, поэтому `(()=>{...})()` правила давали undefined (молчаливый fail-open) — теперь `return (expr)` для чистых выражений (probe-компиляция + wrapCache).

Тесты: `tests/test_js_engine.py` (30, включая `$._`, вложенные массивы, CREATE_NO_WINDOW, fail-open, P1.5 this.node/find/фолбэк, P0.5 lazy/guard/IIFE-обёртка), `tests/test_junk_filter.py` (31), иконки в `tests/test_js_processing.py`. Смоук: 151 passed, 1 skipped; боевой запуск — 1163 media через fullsize-дискавери, 0 DOM-ошибок (P1.5 подтверждён в бою).

---

## 7. P4 — JS-гейты: consent/age-подтверждения (реализовано 2026-08-10)

> **Цель:** обход «простых запросов подтверждений» — кнопки «I agree / Agree / Yes / Согласен / Принимаю» — которые закрывают контент. До P4 обработка покрывала только cookie-преинжект + кнопки с href/form (GET/POST). Чисто JS-кнопки (`onclick` без URL) полностью пропускались: кандидат в `_handle_gateways` попадал только `if href:`.

### 7.1 Что уже работает (по коду, до P4)
1. **Cookie-преинжект** (`webpage_parser._get_content`): ~16 хардкод-кук (`cookieconsent_status`, `gdpr_accepted`, `CookieConsent`, `age_verified`, `over18`…) на каждый aiohttp-запрос (`bypass_cookie_consent`, дефолт on).
2. **Gateway-детекция** (`_handle_gateways`): «подозрительность» = overlay-селектор (`.age-gate`, `#consent-modal`, `.overlay-consent`…), age-фраза, или generic-overlay + consent-слова, или `<GATEWAY_MIN_MEDIA_THRESHOLD` медиа + consent-слова. Затем сбор кандидатов из `a/button/input` по `GATEWAY_TEXT_PATTERNS` + блэклист legal/terms/privacy.
3. **Bypass** (`_execute_bypass`): GET/POST по найденному URL через sync-сессию (Referer + UA), при `status<400` — куки синхронизируются в aiohttp, media/links очищаются, страница перепарсивается (лимит 3 попытки).

### 7.2 Дыры (подтверждено по коду)
- **JS-only кнопки не обрабатываются**: `<button onclick="document.cookie='age=18';location.reload()">` — нет href → кандидат отбрасывается (`if href:`).
- `onclick`-regex ищет только URL-литерал; `document.cookie=...` и вызовы функций (`setCookie`, `acceptCookies`) не извлекаются.
- Кнопки, **мутирующие DOM без reload**, неразрешимы повторным fetch (заглушка вернётся снова).
- `div/span[onclick]` не сканируются вовсе (только `a/button/input`).
- Успешный bypass не помогает **другим страницам того же домена** (куки живут только в `_sync_session` парсера; для скачивания синкаются в shared-session с domain-scope, но для парсинга следующей страницы не применяются).

### 7.3 Реализация (два уровня + кэш)

**Уровень 1 — статическое извлечение consent-кук из JS (Python, всегда):**
- Расширить сканирование: `a/button/input` + элементы с `onclick`/`onmousedown`/`onkeypress` (в т.ч. `div/span`).
- Парсинг обработчика: `document.cookie\s*=\s*["']name=value...["']` → только первая `name=value` до `;`; вызовы `setCookie/createCookie/acceptCookies/...` → поиск определения в inline `<script>` и парсинг тела на `document.cookie`-присваивания; детект `location.reload()`/`location.href=` → флаг «нужен рефетч».
- Применение кук в `_sync_session` → существующий цикл перепарсинга (limit 3) срабатывает без изменений.
- **Защита от ложных срабатываний:** скоуп кандидатов по overlay-контейнеру, когда он найден (не сканировать всю страницу); расширенный блэклист `login/signin/register/logout/account/password/email/checkout/cart`; скип форм с `input[type=password]`; только сильное совпадение текста для JS-клика.

**Уровень 2 — точечный DOM-клик через `gateway_worker.js` (js_engine=deno):**
- **Отдельный воркер-процесс** (не делит `_dom_lock` с sieve — клик на 5с не блокирует res-правила других страниц).
- Протокол: `{"op":"gateway_click", "html":..., "pageUrl":..., "textPatterns":[...], "overlaySelectors":[...], "timeout":5000}`.
- Воркер: Window с `enableJavaScriptEvaluation:true` → `document.write(html)` → **перехват ДО клика** (`location.reload` stub + флаг, `location.href` setter-перехват) → поиск кнопки по паттернам (внутри overlay) → `.click()` → `waitUntilComplete()` с бюджетом → чтение `document.cookie` (до/после) + `document.documentElement.outerHTML` → `{cookies, html_after, redirect, reload_requested}`.
- Python: `cookies`/`reload_requested` → инжект + рефетч; `html_after` существенно изменился (overlay исчез) → **парсим локально, без повторного fetch**.

**Consent-кэш по доменам (ParserManager):** `_consent_cookies[domain] = {name: value}`; инжект в парсер через settings/контекст при `_invoke_parser`; дополнение после успешного bypass. Превращает «разовую удачу» в «обход один раз на домен» (все треды форума).

### 7.4 Трудности и меры

| Трудность | Мера |
|---|---|
| Функция в **внешнем** `<script src>` | Не находится статически → fallback на уровень 2 или fail-open |
| Куки со спецсимволами/`this` в обработчике | Только первая `name=value`; `this`-зависимые → уровень 2 или пропуск |
| `location.reload()`/`location.href=` в happy-dom инициирует навигацию | Перехват ДО клика (stub + флаг) |
| Внешние `<script src>` не загрузятся (нет `--allow-net`) | Фича: согласия обычно inline; undefined-функция → молчаливый fail-open |
| Тяжёлая страница → eval > 2с | Отдельный таймаут клика (5с), отдельный воркер-процесс (sieve не страдает) |
| Произвольный JS в воркере (беск. цикл, `Deno.exit`) | Без `--allow-*`; таймаут убивает процесс; Python пересоздаёт |
| Асинхронные обработчики (`setTimeout`) | `waitUntilComplete()` с бюджетом |
| Shadow DOM / iframe-виджеты (OneTrust/Cookiebot) | Вне скоупа — known limitation |
| Циклы | `_js_gateway_tried` (1 клик/страницу) + общий лимит 3 → карантин не затронут |
| Ложные срабатывания (login/sign-in) | Overlay-скоуп + блэклист + скип password-форм + сильное совпадение текста |

### 7.5 Тесты
- Unit: статический парсер кук (inline-функция, document.cookie, reload-флаг, блэклист, password-форм скип).
- Unit: gateway_worker click на синтетической HTML с кнопкой (куки после клика, html_after, reload_requested).
- Integration: локальный сервер с JS-гейтом (по образцу `_tmp_esc_e2e.py`) — клик → контент доступен.

### 7.6 Сборка
- `build_exe.py`: копировать `src/parser/js_engine/gateway_worker.js` → `bin/gateway_worker.js` рядом с `dom_worker.js` (happy-dom кэш общий).



| Нельзя | Почему |
|---|---|
| Playwright/Puppeteer/headless Chromium | Решение пользователя; тяжело, хрупко, пакуется плохо |
| Deno как «решение Cloudflare» без оговорок | Не проходит Turnstile/TLS-проверки (см. §2.3) |
| `--allow-all` для Deno-воркера | Правила sieve — непроверенный код; нужна песочница |
| Включённый по умолчанию движок | Меняет поведение и добавляет 40–60 МБ без спроса |
| JS→Python конвертер как основной путь | Хрупкий; Deno снимает необходимость |

---

## 6. Ссылки
- Пакет: https://pypi.org/project/deno/ (JSON API подтверждает автора и платформы), repo `denoland/deno_pypi`.
- Ориентир: `mhogomchungu/media-downloader` — бандлит `bin/deno.exe`, передаёт в yt-dlp `--js-runtimes deno:<path>` (доказательство — `Log.txt` в этом репозитории: `[youtube] [jsc:deno] Solving JS challenges using deno`).
- yt-dlp `--js-runtimes`: выполняет JS-челленджи (`player.js` и подобные) через внешний рантайм (deno/node/quickjs).
- Ограничения Deno-стека: Deno issue #31299 (TLS/HTTP-отпечатки не браузерные); лёгкий путь к CF — cookies браузера + (опц.) curl_cffi.
