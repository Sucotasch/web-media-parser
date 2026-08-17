# Audit.md — полный инженерный аудит репозитория

**Дата:** 2026-08-16 · **Baseline-коммит:** `8aec57d` (перед аудитом закоммичены: удаление старого Audit/Analysis, `.agents/types/`, обновление AGENTS.md; релизный zip 117 МБ сознательно НЕ закоммичен — правило гигиены репо).
**Метод:** сплошное чтение кода (не сканером), прогон тестов, точечные runtime-воспроизведения. Ключевые критические находки воспроизведены лично (см. «Метод проверки»).
**Тестовый прогон:** `python -m pytest tests -q` → **180 passed, 40 skipped, 0 failed** (18 c). Причины скипов перечислены в TST-2.

**Структура:** два взаимосвязанных блока — **A. Расширение Chrome (extension/)** и **B. Приложение (Python)**, затем раздел «Взаимосвязанные находки» (контракты расширение↔приложение), «Остаточные риски», «Допущения», «Порядок внедрения фиксов».

Severity: **critical** (фича мертва/зависание/потеря данных) → **high** ( молчаливая потеря контента, утечки ресурсов, race) → **medium** (корректность в краевых случаях, консистентность) → **low** (чистка, мёртвый код, стиль).

---

## Метод проверки

Найдено и проверено чтением кода всё ниже перечисленное. Утверждения, помеченные «✅ воспроизведено», запускались в проектном venv (`venv/Scripts/python.exe`) отдельными скриптами:

- `JSONWebpageParser` не поддерживает `async with` → `TypeError` (EXT/CORE-1): ✅ воспроизведено.
- Дефолтный запуск в dev загружает **0** Imagus-sieve правил (PAT-1): ✅ воспроизведено (`imagus_rules total: 0`, native 32).
- `_sanitize_imagus_target` пропускает `$&` литералом (PAT-3): ✅ воспроизведено.
- Нативный tumblr-паттерн `_1280.$2` даёт буквальный `$2` в URL (PAT-4): ✅ (в Python `re.sub` `$2` не является группой — остаётся как есть).
- Тесты-пустышки (TST-1): ✅ прочитано напрямую (тесты пере-реализуют проверяемую логику инлайн).
- curl_cffi отсутствует в venv при наличии в requirements.txt (PKG-1): ✅ `import curl_cffi` → ModuleNotFoundError.
- Утечка escalation-сессии на HTTP>=400 (DL-1), `verify=False` (DL-4) и дублирующийся недостижимый `except` (DL-11): ✅ проверено чтением `media_downloader.py:214-237, 222, 491-497`.

Остальные находки верифицированы чтением кода построчно с указанием `файл:строка`.

---

# БЛОК A — Расширение Chrome (extension/)

Файлы: `manifest.json` (58 строк), `background.js` (506), `content_script.js` (173), `sieve.js` (171), `popup/popup.js` (498), `popup/popup.html` (72). MV3, service worker.

### EXT-1 · HIGH — `sieve-to` (преобразованные sieve полноразмерные URL) скрыты дефолтным фильтром «Full size»

`popup/popup.js:96-98` после скана ставит `activeSourceFilter = "fullsize"`, а `FULLSIZE_SOURCES = new Set(["sieve-res", "link-direct"])` (`popup.js:11`, дубль в `background.js:469`). При этом popup-трансформация помечает результат как `source: "sieve-to"` (`popup.js:143`). Итог: **самые ценные элементы — апгрейднутые sieve-ом fullsize — не попадают в дефолтный фильтр «Full size»**, а фильтр «Thumbnails» (`popup.js:194`: всё, что НЕ в FULLSIZE_SOURCES) их показывает как миниатюры. Пользователь по умолчанию не видит (и не скачает) именно то, ради чего sieve подключён.

**Фикс** (минимальный):
```js
const FULLSIZE_SOURCES = new Set(["sieve-res", "link-direct", "sieve-to"]);
```
в обоих файлах (`popup.js:11`, `background.js:469`). Лучше — вынести в общий модуль (см. EXT-9).

### EXT-2 · HIGH — Popup теряет оригинальный URL: отправляет несуществующее поле `item.original`

`popup/popup.js:405` собирает payload: `original_url: item.original || null`. Но трансформация пишет `item.original_url` (`popup.js:140`), поля `item.original` не существует ни в одном месте. Приложение принимает и сохраняет `original_url` (`src/gui/main_window.py:1124`) — всегда `null`. Метаданные «из чего апгрейднули» теряются, дедуп/лог по оригиналу невозможен.

**Фикс:** `original_url: item.original_url || null`.

### EXT-3 · HIGH — Команда «send-desktop» (Ctrl+Shift+D) делает мёртвую работу и игнорирует результат

`background.js:414-467`: для `send-desktop` выполняется полный конвейер `scanMedia` + `discoverFullsize` (до 50 линков, сетевые запросы), вычисляются `fullsize`/`items` (строки 446-447) — и всё это **не используется**: в `sendToDesktop` уходит только `[{ url: response.url }]` (строка 461). Логика противоречит видимойintent-конвенции: badge показывает «✓» независимо от результата скана; пользователь ждёт сканирования, которое ни на что не влияет.

**Фикс:** для `send-desktop` не сканировать вообще (сократить до: получить активную вкладку → `getPageContext` → `sendToDesktop([{url: tab.url}], false, context)`). Если скан нужен для badge-счётчика — показывать количество найденных элементов.

### EXT-4 · MEDIUM — Контракт POST /api/tasks: `source` перегружен, валидация на сервере отсутствует

Popup отправляет элементы `{url, source: item.pageUrl, type, original_url, transformed}` (`popup.js:401-407`) — поле `source` здесь означает **referer страницы**, тогда как в результатах скана `source` — это происхождение элемента (`img`, `srcset`, `sieve-res`...). Один идентификатор — две семантики. Сервер (`src/server/http_server.py:132-135`) не валидирует элементы вообще: `urls = body.get("urls", [])` уходит в колбэк как есть; при `{"urls": ["строка"]}` колбэк упадёт на `.get` (поймается общим 500).

**Фикс:** в popup переименовать в `referer`; на сервере отфильтровать не-объекты:
```python
urls = [u for u in body.get("urls", []) if isinstance(u, dict) and isinstance(u.get("url"), str) and u["url"].strip()]
```

### EXT-5 · MEDIUM — `chromeDownload`: зависший promise, если `onChanged` не приходит

`background.js:325-337`: завершение учёта завязано на `chrome.downloads.onChanged` c `state.complete|interrupted`. Если событие не приходит (скачивание отменено пользователем из полосы Chrome, редко — баг Chrome), `resolve` не вызывается никогда: popup-кнопка «Saving…» висит вечно (у popup нет таймаута на этот `sendMessage`).

**Фикс:** страхующий таймаут на каждый элемент:
```js
const watchdog = setTimeout(() => { /* считать interrupted */ }, 10 * 60 * 1000);
// в listener при complete/interrupted: clearTimeout(watchdog)
```
и/или слушать `chrome.downloads.onErased`.

### EXT-6 · MEDIUM — send-desktop (deep) и один_шот: ретрай/таймаут к localhost отсутствуют

`background.js:374-389` `sendToDesktop`: `fetch` без `AbortSignal.timeout`. Если процесс приложения «полужив» (порт открыт, но не отвечает), промис висит неограниченно долго; popup остаётся в «Sending...». Аналогично `getStatus` (строки 394-401).

**Фикс:** `signal: AbortSignal.timeout(5000)` в оба fetch; в `catch` текущая обработка уже корректная.

### EXT-7 · MEDIUM — MV3 CSP молча убивает JS-правила sieve в popup

`extension/sieve.js:127-136` строит `new Function(fnBody)` с кодом правил Imagus (`to: "..."` c `:`). В MV3 у extension-страниц CSP запрещает `unsafe-eval` → `new Function` бросает `EvalError`, перехватывается (`executeSieveJS` → null), правило тихо пропускается. Это **задокументированное ограничение** (popup.js:130 комментирует «JS rules need page DOM»), но поведение НЕ отражено в UI: счётчик «Sieve: N rules loaded» (popup.js:469) считает все правила, включая заведомо неработающие JS-правила. Пользователь не может понять, почему правило «не апгрейдит».

**Фикс (минимальный):** в `parseSieve` вернуть также `jsRuleCount` и показывать `Sieve: 823 rules (137 JS — skipped in popup)`.

### EXT-8 · MEDIUM — Дублирование и рассинхрон junk-фильтра расширения с приложением

`content_script.js:18-27` `JUNK_PATTERNS` безусловно выкидывает **все** `.png/.gif/.ico/.svg` (строка 26: `/\.(gif|png|ico)$/i`) и не имеет allowlist. Приложение имеет `junk_filter.py` + `resources/junk_allowlist.txt` (allowlist, кстати, в dev не грузится — см. PAT-8) и НЕ выкидывает png по умолчанию (`DEFAULT_ENABLED_IMAGE_FORMATS` содержит `.png`, constants.py:336-338). Результат: один и тот же сайт через «Save (Chrome)» и через «Download → desktop app» даёт **разный набор файлов** (пример: галерея с PNG-артом — расширение её не увидит вообще). Плюс дублирование регулярок `l-stat/userpic/avatar/...` в двух кодовых базах без механизма синхронизации.

**Фикс:** минимально — убрать жёсткое `\.(gif|png|ico)$/i` из JUNK_PATTERNS content-скрипта (оставить структурные паттерны `/logo|/favicon|...`), т.к. формат-фильтрацию делает приложение; принципиально — общий источник паттернов (тот же `sieve.json`-канал доставки или просто комментарий-якорь в обоих файлах с требованием синхронной правки).

### EXT-9 · MEDIUM — Дублированная логика правил sieve в трёх местах с разной семантикой

Sieve-правила парсятся/применяются независимо:
1. `background.js:44-83` `loadSieveResPatterns` — поля `link/res/url` (+POST-синтаксис `" :data"`, строка 72);
2. `popup.js:131-149` + `sieve.js:17-49` `parseSieve` — поля `img/to` (+`#ext#`-расширение, sieve.js:92-99, берёт только первый вариант);
3. приложение: `site_pattern_manager.py` (те же `img/to`, но `$&`/`$nn` конвертируются иначе — см. PAT-3/PAT-5).

`FULLSIZE_SOURCES` продублирован (`popup.js:11` / `background.js:469`), cap линков `slice(0, 50)` продублирован (`popup.js:123` / `background.js:440`). Один и тот же sieve.json даёт **разные результаты** на разных поверхностях (расширение `$&` понимает, Python — нет; расширение `#ext#` раскрывает первым значением, Python — см. PAT).

**Фикс (эволюционный, без большого рефакторинга):** завести `extension/shared.js` (или расширить sieve.js) с общими `parseSieveRules`, `applyUrlTransform`, `FULLSIZE_SOURCES`, `LINKS_CAP`; подключить и в popup, и в background (service worker может `importScripts`). Семантику `$&`/`$nn` привести к Python-стороне (см. PAT-3/PAT-5) — одно поведение на всех поверхностях.

### EXT-10 · LOW — manifest: избыточный `web_accessible_resources` и `activeTab`

`manifest.json:15-19` отдаёт `sieve.json` всем сайтам (`<all_urls>`). Для работы это не нужно: `background.js:17` и popup читают файл через `chrome.runtime.getURL` + `fetch` из extension-контекста, что НЕ требует web_accessible_resources. Текущая запись расширяет поверхность атаки (любой сайт может скачать весь sieve пользователя) без пользы. `activeTab` тоже избыточен при постоянном content script на `<all_urls>`.

**Фикс:** удалить блок `web_accessible_resources` целиком; рассмотреть удаление `activeTab`.

### EXT-11 · LOW — Доверие origin: любой чужое расширение может POST-ить задачи и cookies

`http_server.py:62-72` `_is_trusted_origin` принимает любой `chrome-extension://*`. Любое иное установленное расширение пользователя может слать `POST /api/tasks` с произвольным `cookies` (они попадут в `settings["extension_cookies"]` → `shared_session.py:91`) и списком URL. GET-эндпоинты безопасны (нет CORS-заголовка для чужих origin — прочитать ответ нельзя).

**Фикс:** конкретизировать origin: `origin.startswith("chrome-extension://")` → сравнение с фиксированным ID расширения (параметр `EXTENSION_ID` в constants + настройка). До публикации в Web Store ID нестабилен — хранить в настройках приложения, дефолт «любое расширение» оставить как осознанный компромисс, задокументировав в README.

### EXT-12 · LOW — Прочее расширение

- `background.js:150` — мёртвый no-op `lock` (пережиток портирования Python-кода). Удалить.
- `background.js:284` — комментарий «Only break if res found something» не соответствует коду (выход через `return` на первом совпадении, строка 280). Поправить комментарий.
- `content_script.js:132-145` `parseSrcset` смешивает w- и x-дескрипторы в одну шкалу (`x * 10000`) — эвристика; при `600w, 1.5x` выберет x-вариант всегда. Ограничить сравнение однотипных дескрипторов.
- `background.js:440`/`popup.js:123` cap 50 линков — задокументировать константой `LINKS_CAP = 50` (см. EXT-9).
- Версия манифеста `1.0.4` — см. кросс-находку X-5 (версионный дрейф).

### Что в расширении проверено и в порядке

- `Semaphore` в background.js корректен для одного воркера; `applyUrlTransform` через split/join корректно экранирует `$`.
- POST-синтаксис sieve (`url :data`) парсится стабильно; таймауты `AbortSignal.timeout(8000)` на все внешние fetch есть.
- `getPageContext` шлёт cookies только на localhost (127.0.0.1) — для локального desktop-приложения приемлемо; риск задокументирован в EXT-11.
- MV3-жизнь воркера: `cachedSieveRes` перестраивается на старте воркера (background.js:85) и по `storage.onChanged` (89-95) — stateless-дизайн выдержан.
- content_script корректно экранирует схемы (`javascript:`, `#`), резолвит относительные URL через `new URL(url, base)`.

---

# БЛОК B — Приложение (Python)

## B1. Ядро парсера (`webpage_parser`, `parser_manager`, `priority_url_queue`, `utils`, `json_parser`, `junk_filter`)

### CORE-1 · CRITICAL — JSON-API парсер мертв: `async with` на классе без контекст-менеджера

`src/parser/parser_manager.py:472`:
```python
async with JSONWebpageParser(url=url, settings=self.settings, external_session=session) as p:
    links_found, media_files_found = await p.parse()
```
`JSONWebpageParser` не определяет `__aenter__/__aexit__` — комментарий `src/parser/json_parser.py:45`: «__aenter__ and __aexit__ removed, session managed externally by AsyncClientManager». ✅ **воспроизведено:** `TypeError: 'JSONWebpageParser' object does not support the asynchronous context manager protocol`.

Каждый URL, распознанный `_determine_parser_type` как JSON (`parser_manager.py:460-465`: путь содержит `/api/`|`/json/`, заканчивается `.json`, query `format=json|output=json|callback=`), бросает TypeError, который глотается общим обработчиком (`parser_manager.py:801-803`) с логом «Error processing URL». **Из JSON-эндпоинтов не извлекается ни одного файла — фича мертва в проде.** Усилитель — CORE-10: если стартовый URL JSON-подобный, задача зависает навсегда.

**Фикс** (строка в `parser_manager.py`):
```python
if is_json_api:
    logger.debug(f"Using JSONWebpageParser for {url}")
    p = JSONWebpageParser(url=url, settings=self.settings, external_session=session)
    links_found, media_files_found = await p.parse()
```
**Тест:** гонять `_invoke_parser(..., is_json_api=True)` с мок-сессией (существующие тесты конструируют парсер напрямую — потому регрессия и прошла; см. TST-6). Риск: нулевой — путь сейчас 100% сломан.

### CORE-2 · HIGH — Утечка aiohttp-соединений в `_discover_linked_fullsize`

`src/parser/parser_manager.py:590-604`: `session.get/post` без `async with`; ранние `return` (Content-Type image/video — штатный путь; не-HTML) оставляют тело непрочитанным → соединение висит в пуле до keepalive-таймаута сервера. Галерея с сотнями ссылок на image-хосты может исчерпать коннектор общего `ClientSession` и застопорить всех воркеров.

**Фикс:**
```python
async def _fetch():
    if post_data:
        return await session.post(fetch_url, data=post_data, headers=headers, timeout=timeout, proxy=proxy_url)
    return await session.get(fetch_url, headers=headers, timeout=timeout, proxy=proxy_url)

async with await _fetch() as resp:
    ...
```
**Тест:** текущие фейки в `test_fullsize_discovery.py:101-186` не реализуют async-CM — потому утечка не видна; добавить фейку `__aenter__/__aexit__` и один тест на `aiohttp.test_utils.TestServer`.

### CORE-3 · HIGH — Неограниченный `Retry-After`-sleep под доменным семафором

`src/parser/webpage_parser.py:190-203`: `retry_after = max(0, int(raw))` без верхнего предела; сервер с `Retry-After: 86400` паркует воркера на сутки. Sleep происходит внутри пер-доменного семафора (`DOMAIN_CONCURRENCY_LIMIT=2`, парсер+загрузчик вместе) — весь домен (и загрузки, и парсинг) стоит. Выход — только Stop.

**Фикс:**
```python
retry_cap = self.settings.get(K.SETTING_PAGE_TIMEOUT, K.DEFAULT_PAGE_TIMEOUT)
retry_after = min(retry_after, retry_cap)
```

### CORE-4 · HIGH — Junk-фильтр: сильные токены `pixel` / `creative` молча выкидывают легитимный контент

`src/parser/junk_filter.py:60-66,137-153` (`AD_PATH_TOKENS` содержит `pixel`, `creative`; `_path_has_ad_token` срабатывает в одиночку, без подтверждения третьей стороной/размером — junk_filter.py:167). ✅ агентом воспроизведено: `cdn.artstation.com/pixel-art/scene1.png` и `i.imgur.com/creative-portfolio/shot.jpg` классифицируются как реклама и **тихо дропаются** перед постановкой в очередь загрузки (`parser_manager.py:824-827`). Тест-позитивы (`test_junk_filter.py:26-46`) используют `adsense/creative/2.jpg` — там совпадает `adsense`, поэтому фикс безопасен.

**Фикс:** убрать `"pixel"` и `"creative"` из `AD_PATH_TOKENS`; слабое правило `NNNxNNN`+third-party уже покрывает реальные трекер-креативы (`pixel_300x250.gif`).
**Тест:** добавить негативы `pixel-art`, `creative-portfolio`; существующие позитивы должны остаться зелёными.

### CORE-5 · MEDIUM — `is_media_url`: fullsize-индикатор срабатывает РАНЬШЕ исключения веб-страниц

`src/parser/utils.py:215-218` против `221-233`: подстрочная проверка индикаторов (`/large/`, `/original/`, ...) + медиа-расширение в URL возвращает `True` до исключения `.html/.php/...`. ✅ воспроизведено агентом: `is_media_url('https://site.com/large/photo.mp4.html') == True`. Такие страницы становятся «медиа», загрузчик тянет HTML, падает с «Webpage/script content» (`media_downloader.py:361,425`) — лишний цикл загрузки+interstitial-retry на каждый такой URL.

**Фикс:** переставить блок non-media-исключений выше fullsize-блока (полный код в отчёте ниже по ссылке на тест). **Тест:** `large/photo.mp4.html`, `original/scan.jpg.php` в `test_url_detection.py`.

### CORE-6 · MEDIUM — `is_same_domain`: последние-две-метки → bbc.co.uk == evil.co.uk

`src/parser/utils.py:348-366`. ✅ агентом: `is_same_domain('https://bbc.co.uk','https://evil.co.uk') == True`. Ослабляет `stay_in_domain` (расползание краула за пределы разрешённого домена на second-level-TLD хостах — в т.ч. compliance-риск) и решение о thumbnail parent-link (`webpage_parser.py:858`). Тот же изъян в `junk_filter._third_party` (junk_filter.py:183-188) — в пермиссивную сторону.

**Фикс:** `_registrable_domain()` с frozenset двухуровневых TLD (co.uk, com.au, org.ru, com.br, ... — список в патче агента) без новой зависимости. **Тест:** прямые юнит-тесты `is_same_domain` (сейчас нулевое покрытие).

### CORE-7 · MEDIUM — `_get_video_platform` матчит расширения как подстроки пути

`src/parser/webpage_parser.py:670-675`: `".ts" in path` → `/user.tsuji/page` = «direct-video»; аналогично `.mov`/`.avi`. Положительный platform превращает любой `<iframe>` в видео-embed без фильтра значимости (строки 966-972) — мусорные медиа-записи.

**Фикс:** `_DIRECT_VIDEO_RE = re.compile(r"\.(mp4|webm|avi|mov|flv|mkv|wmv|ts)(?:$|[/?#])", re.I)` по `urlparse(url.lower()).path`.

### CORE-8 · MEDIUM — CPU-экстракция на event loop; `_handle_dynamic_content` квадратичен по DOM

`webpage_parser.py:1501-1553`, особенно 1532-1538: `re.search(pattern, str(elem))` ре-сериализует каждый элемент с поддеревом → O(размер страницы × глубина). На форуме 1-2 МБ блокирует общий loop на секунды — стоп/пауза и все воркеры стоят. `parse()` (1378-1453): BeautifulSoup строится в executor (1380), но `_extract_images/_extract_videos/_extract_links/_handle_dynamic_content` — синхронные CPU-проходы на loop-потоке.

**Фикс (двухшагово):** (1) убрать `str(elem)` — проверять атрибуты напрямую (`elem.get("data-src")`, `v-lazy`, `lazyload`, `ng-src`); (2) опционально вынести все четыре прохода одним заданием в `run_in_executor`. **Тест:** `test_js_processing.py` должен остаться зелёным; ручная проверка отзывчивости GUI на большой странице.

### CORE-9 · MEDIUM — JSON-LD: теряются формы `image` и обходится фильтр значимости

`webpage_parser.py:1035-1056`: первый блок принимает `str|dict`, но не `list`; второй — `list`, но не `ImageObject`-узлы; `{@type: ImageObject, image: [...]}` даёт ничего. Добавление — с одним `is_media_url`, **без** `_is_significant_media` → VideoObject-превью и мелкие Article-миниатюры идут в очередь без фильтра, в отличие от всех остальных путей экстракции.

**Фикс:** один хелпер `_jsonld_image_urls(img)` (str/dict/list рекурсивно, `url`|`contentUrl`, лимит 5) + гейт `_is_significant_media(...)`. **Тест:** кейсы Article.image dict/list, ImageObject.image list, VideoObject-превью отбрасывается — покрытие `_extract_jsonld_media` сейчас нулевое.

### CORE-10 · MEDIUM — Естественное завершение невозможно при `pages_processed == 0` (зависание)

`parser_manager.py:396-397` (`continue` монитора) и `:424` (возврат True «продолжать») при `pages_processed == 0`. Инкремент — только в конце `_process_parser_results` (`:735`). Любое исключение на единственном посеянном URL (сегодня гарантировано CORE-1 для JSON-стартов) → вечный цикл опроса, `task_ended` не эмитится, очередь не двигается.

**Фикс:** в `except`-обработчике воркера (`:801-803`) считать страницу обработанной:
```python
if current_url not in self.processed_urls:
    self.processed_urls.add(current_url)
self.stats["pages_processed"] += 1
self._last_activity_time = time.time()
```

### CORE-11..20 · LOW (кратко)

- **CORE-11** `parser_manager.py:272-290` — если `_main_task` упадёт до внутреннего `try` (например `create_shared_downloader_session`, `:327`), `task_ended` не эмитится → зомби-«running» в UI. Фикс: `self.task_ended.emit("failed")` во внешнем except c флагом от двойного эмита.
- **CORE-12** `parser_manager.py:977-980,1016-1019` — `call_soon_threadsafe` после `is_closed()`-check может бросить RuntimeError в GUI-слот. Обернуть `try/except RuntimeError`.
- **CORE-13** `json_parser.py:180-185` — list-ветка `_process_potential_media` обходит format-allowlist (выключенные GIF/SVG из JSON-массивов скачиваются). Зеркаровать скалярную проверку `is_format_allowed`. (Путь сейчас мёртв из-за CORE-1 — чинить парой.)
- **CORE-14** Мёртвый код: `ParserManager.reset()` (`parser_manager.py:150-174`, вызовов нет, к тому же падает если вызвать до старта), `utils.extract_largest_image_from_srcset` (`utils.py:547-571`, вызовов нет), `utils.is_video_url` (`utils.py:282-345`, только тесты), `hasattr('_bypass_attempts')` (`webpage_parser.py:1358-1359`), несматчиваемый srcset-regex в `data_attributes` (`webpage_parser.py:74` — group(1) захватывает всю srcset-строку). Удалить.
- **CORE-15** Дубли ad-логики: `_AD_KEYWORDS/_AD_HOSTS` (utils) против `AD_HOST_SUFFIXES/AD_PATH_TOKENS` (junk_filter); `utils._AD_HOSTS` проверяет подстрокой: `facebook.com/tr` матчит `facebook.com/try-this` (`utils.py:596,648`). Централизовать в junk_filter (utils уже зовёт его первым — `utils.py:501,621`), `_AD_HOSTS` удалить.
- **CORE-16** `utils.py:60,299` — проверки `f"{ext}?" in path` мертвы (path из urlparse не содержит `?`). Удалить.
- **CORE-17** `webpage_parser.py:689-699,728-730` — `_get_best_image_url` смешивает приоритет и пиксели в одном поле `width` (именованные hi-res атрибуты = 100, generic = 999999, srcset = реальные px): при `min_image_width > 100` именованные атрибуты выпадают из substantial_candidates. Разделить `score` и `width`.
- **CORE-18** `webpage_parser.py:174-178` — `sock_read = page_timeout - 2` без clamp: hand-edited settings с page_timeout<=2 даёт sock_read<=0 (мгновенный таймаут всего). `max(1, ...)`.
- **CORE-19** `webpage_parser.py:887-895`, `json_parser.py:170` — выключенные по формату прямые ссылки ставятся в crawl-очередь с `from_image` и, будучи media-lookup, обходят stay_in_domain/depth — краулер тянет байты картинки как «веб-страницу» на каждый такой URL. Дропать вместо постановки в очередь.
- **CORE-20** `parser_manager.py:578,613-614,701` — `consumed.add(link_url)` до fetch: при сетевой ошибке линк исключён из краула, хотя fullsize не найден (остальное содержимое страницы-вьюера теряется). `consumed.discard(link_url)` в except.

### Проверено и в порядке (ядро парсера)

- PriorityURLQueue: lock+event-протокол без потерянных пробуждений; `PrioritizedURL(order=True)` с `compare=False` безопасен при равных приоритетах.
- Жизненный цикл loop: `set_event_loop` до создания Event/Queue/Lock; воркеры гасят `_active_tasks`/`task_done` в finally; `_main_task` собирает с `return_exceptions=True`.
- Межмодульные контракты: `AsyncClientManager` имеет корректные `__aenter__/__aexit__`; `transform_image_url` возвращает list (соответствует `webpage_parser.py:740`); `_cookie_lock` даунлоадера используется парсером (`parser_manager.py:503-511`).
- Карантин ограничен (`QUARANTINE_MAX_ITEM_RETRIES`); curl_cffi-эскалация — одна попытка, session закрывается в finally; JS-редиректы ≤5; gateway-клики ограничены 3 попытками с one-shot флагом.
- Учёт page_limit считает страницы-источники (не файлы) — покрыто `test_crawler_frontier.py:322-338` (но см. TST-1 о качестве этих тестов).

## B2. Загрузчик, HTTP-движок, JS-движок (Deno)

### DL-1 · HIGH — Утечка curl-сессии на HTTP-ошибке эскалации

`src/downloader/media_downloader.py:214-238` (✅ проверено чтением): `_try_escalate_get` закрывает сессию в except-ветке (234-237), но НЕ на пути `resp.status_code >= 400` (224-227) — только `resp.close()` и `return False`. `self._escalation_session` присваивается лишь при успехе (229), `_close_escalation_session` (240-248) трогает только его. Каждый заблокированный файл, где curl_cffi тоже получает 403/5xx (типичный исход Cloudflare), оставляет неоткрытую curl-сессию (easy+multi handle). `MediaDownloader` создаётся на каждый медиа-элемент (`parser_manager.py:902`) → галерея на 200 картинок на заблокированном домене течёт до 200 сессий. Нарушен контракт самого модуля (строки 199-200: «created, used once, and closed»).

**Фикс:**
```python
if resp.status_code >= 400:
    resp.close()
    session.close()
    logger.debug(f"Escalation GET returned HTTP {resp.status_code} for {self.url}")
    return False
```
**Тест:** юнит-тест, что `session.close` вызывается на 403-пути (мок `create_escalation_session`).

### DL-2 · HIGH — TOCTOU резервирования имени файла + коллизия temp-путей между потоками

`media_downloader.py:314-330, 441, 517`: `_ensure_unique_filepath_at_destination` проверяет `os.path.exists` под `_filename_lock`, но итоговый файл создаётся много позже (rename на 482). Две параллельных загрузки в один путь обе проходят проверку и пишут **один и тот же** `X.partial` (441) / `X.partN` (517). Досягаемо: парсер дедуплицирует по URL (`parser_manager.py:833-835`), но не по целевому пути — разные URL с одинаковым санитизированным basename (`1.jpg` на двух хостах с одной страницы) дают один `target_dir/basename` (`parser_manager.py:853`); воркеры параллельны (`parser_manager.py:332-336`). Последствия: интерливинг-запись → битый файл; на Windows `os.replace` файла, ещё открытого другим потоком → `WinError 5` → засчитывается в `domain_state["failures"]` → карантин домена после 3 (`constants.py:27`).

**Фикс:** уникальный temp-суффикс на загрузку + реестр занятых путей:
```python
_reserved_paths: set = set()   # module level

temp_path = f"{self.filepath}.{threading.get_ident():x}.partial"
temp_file = f"{self.filepath}.{threading.get_ident():x}.part{i}"

# в _ensure_unique_filepath_at_destination:
while os.path.exists(candidate) or os.path.normcase(candidate) in _reserved_paths:
    ...
_reserved_paths.add(os.path.normcase(candidate))
self._reserved_path = os.path.normcase(candidate)
# release в finally download(): _reserved_paths.discard(getattr(self, "_reserved_path", None))
```
**Тест:** два потока качают разные URL в один путь — различные temp-файлы, обе успешны.

### DL-3 · HIGH — Content-Length против распакованного размера: gzip-ответы признаются «Size mismatch», валидный файл удаляется, домен уходит в карантин

`media_downloader.py:472-479`: `Content-Length` — размер **сжатого** тела, `iter_content` отдаёт **распакованные** байты → `os.path.getsize(temp_path) != content_length` для любого сервера с `Content-Encoding` (shared session шлёт дефолтные requests `Accept-Encoding: gzip, deflate` — переопределяются только UA/Accept-Language, строки 80-83; curl-эскалация имперсонирует Chrome и анонсирует gzip/deflate/br). Валидный файл **удаляется** (476-478); ошибка `Size mismatch` не матчит `_RETRYABLE_ERROR_HINTS` (278-284) → финальная, без ретрая; не входит в `is_content_skip` парсера (`parser_manager.py:945-948`) → +1 failure домена → 3 случая карантинят здоровый домен.

**Фикс:**
```python
encoding = (response_get.headers.get("Content-Encoding") or "").strip().lower()
if content_length > 0 and not encoding:
    actual_size = os.path.getsize(temp_path)
    ...
```
**Тест:** мок GET с `Content-Encoding: gzip`, `Content-Length: <сжатый>` и распакованным телом → успех.

### DL-4 · MEDIUM (security) — Эскалационный GET отключает проверку TLS

`media_downloader.py:222` (✅ проверено): `verify=False` на каждом 403/5xx-триггере. MITM может скормить «блокирующий» ответ, включающий эскалацию, а затем перехватить уже неверифицированный запрос. Функциональной причины нет (имперсонация не требует отключения верификации). **Фикс:** убрать `verify=False`; ретест: self-signed хост должен падать fail-closed.

### DL-5 · MEDIUM — Мусорные `.partial` на путях отказа

`media_downloader.py:440-470`: при mid-stream сетевых ошибках и ошибках диска (461, 469-470) файл закрывается, но не удаляется. После исчерпания ретраев `.partial` копятся вечно; чистятся только жёстким Stop'ом (`task_queue_manager.py:211`). **Фикс:** обернуть запись в try/except с удалением temp-файла и re-raise (путь стопа на 449-450 сохранить).

### DL-6 · MEDIUM — fix_brotli ломает фетчинг ровно в том окружении, для которого сделан

`src/fix_brotli.py:33-46`, триггер `src/parser/shared_session.py:27-36`: фолбэк выполняется только когда `import brotli` провалился, и дописывает `", br"` в анонс `Accept-Encoding` aiohttp. Но aiohttp при получении br без декодера бросает `ContentEncodingError("br")` (проверено по исходникам aiohttp 3.13.5 в venv, `http_parser.py:1021-1022`). Итог: на инсталле без brotli патч заставляет серверы слать br и каждая такая страница **падает** — строго хуже, чем ничего. Плюс `patch()` возвращает True даже когда ветка не применилась (строка 45 вне if) — shared_session логирует ложное «Brotli support enabled». В этом venv латентно (brotli установлен), активно на сломанных инсталлах.

**Фикс:** удалить фолбэк-блок в `shared_session.py:26-36` (aiohttp сам анонсирует ровно то, что умеет декодировать), либо `patch()` → `return False` с комментарием.

### DL-7 · MEDIUM (security) — Cookies расширения утекают на каждый крawl-хост

`src/parser/shared_session.py:90-93`: cookies вкладки браузера (ставятся в настройки задачи на `main_window.py:1093`) кладутся **сессионным заголовком** в общий aiohttp-сеанс → уходят каждому хосту, который задача краулит на любой глубине (CDN, третьи стороны, рекламные эндпоинты, прошедшие junk-фильтр). Сессионные/auth-cookie сайта А отдаются сайту Б.

**Фикс:** скоупировать через cookie jar на стартовый домен задачи (парсить пары `name=value`, `jar.update_cookies(morsels, response_url=URL(start_url))`, передавать `start_url` в settings — parser_manager им владеет).

### DL-8 · MEDIUM — Таймаут JS-вызова (2.0 c) включает холодный старт Deno; churn респавнов молча отключает движок

`src/parser/js_engine/engine.py:51, 387-411, 473-522`: `DEFAULT_CALL_TIMEOUT = 2.0` покрывает spawn процесса + компиляцию + (DOM/gateway) импорт `npm:happy-dom` + парсинг + правило. По таймауту воркер убивается (464) → следующий вызов снова платит за холодный старт → на медленных дисках/машинах с антивирусом каждый DOM-вызов таймаутится, P1 молча возвращает None для всех правил. Дизайн-док задаёт `timeout=5.0` (`docs/DENO_JS_ENGINE_DESIGN.md`, строка 71) — дрейф код/док.

**Фикс:** `DEFAULT_CALL_TIMEOUT = 5.0` (+опционально грейс первому вызову после спавна).

### DL-9 · MEDIUM — kill() без wait(): незарипленные дети / утечка хендлов

`engine.py:266-271, 304-309, 349-354`: все три `_kill_*_proc` делают `terminate() → wait(2) → kill()` **без финального wait()** — на POSIX SIGKILL-нутый ребёнок остаётся зомби до GC Popen; пайпы не закрываются явно. В сочетании с churn из DL-8 накапливается. Нормальный shutdown корректен (`parser_manager.py:361` зовёт `shutdown()` в finally). **Фикс:** после `kill()` — `wait(timeout=1)` в try/except.

### DL-10 · LOW — Кэш результатов run_js не кэширует None

`engine.py:400-402, 410`: провалившиеся правила кэшируются как `None`, но `None` неотличим от промаха — каждое failing-правило пересполняется на каждую подходящую миниатюру (ровно тот workload, ради которого кэш существует). **Фикс:** сентиэл `_MISS = object()` вместо `is not None`.

### DL-11 · LOW — Мёртвый дублирующийся except

`media_downloader.py:491-497` (✅ проверено): два идентичных подряд `except http_engine.HTTP_ERROR_EXCEPTIONS` — второй недостижим. Удалить 495-497.

### DL-12 · LOW — Rate limit не применяется в multi-thread чанках; ответ не закрывается

`media_downloader.py:563-600`: `_download_chunk` игнорирует `self.rate_limit` (комментарий на 596 признаёт «Simplified rate limiting» — по факту отсутствует) → `threads_per_file > 1` молча обходит «Max Download Speed» (N потоков × безлимит). `response` (574) не закрывается в except → соединение висит до GC. **Фикс:** лимитер в чанк-цикл + `try/finally: response.close()`.

### DL-13 · LOW — Кривой `Content-Length` бросает неклассифицированный ValueError

`media_downloader.py:358, 428`: `int(...get("Content-Length", 0))` на мусорном заголовке → ValueError вне `NETWORK_ERROR_EXCEPTIONS` (371) → generic except (500), без ретрая. Обернуть try/except → 0.

### DL-14 · LOW — Фолбэк curl→requests теряет User-Agent

`src/parser/http_engine.py:130-144`: при падении impersonate-профиля возвращается голый `requests.Session()`. Вызывающие на curl-пути сознательно не ставят UA («чтобы не палить отпечаток») — фолбэк-сессия шлёт `python-requests/x.y`: более палевно, чем настроенный UA. **Фикс:** в фолбэке проставить UA/Accept-Language из settings.

### DL-15 · LOW (note) — Общий requests.Session из потоков: cookie-jar гонки остаются

`media_downloader.py:73-78`, `http_engine.py:167-169`, потребитель `parser_manager.py:502-516`: `_cookie_lock` прикрывает только set-записи парсера; потоки даунлоадера делают конкурентные `session.get` — извлечение cookies из ответов мутирует тот же jar без лока (requests.Session потокобезопасность не документирована). Полный фикс = маршрутизация cookie-обновлений через одну точку; краткосрочно — задокументировать остаточный риск.

### DL-16 · LOW — Stop-клинап может удалить легитимные медиа

`task_queue_manager.py:211` (+проход 0-байтовых файлов 216-217): glob `**/*.part*` матчит и `clip.part1.mp4`, `spare.parts.png`; 0-байтовый sweep накрывает пользовательские пустые файлы. Сузить до `("*.partial", "*.part[0-9]*")`, 0-байтовый проход ограничить `.partial`.

### DL-17 · LOW — fix_lxml использует print(); падает под pythonw

`src/fix_lxml.py:29, 32`, вызывается безусловно из `main.py:28`: `print()` при `sys.stdout is None` (pythonw) → AttributeError на старте. Заменить на `logging.getLogger(__name__).info(...)`.

### DL-18 · LOW — Магические строки настроек вне constants.py

`shared_session.py:62-63`: `"connect_timeout"` / `"sock_read_timeout"` без `SETTING_*`-констант и без продьюсера в settings-диалоге — всегда дефолты; нарушение конвенции AGENTS.md. `_get_default_headers` (72-76) хардкодит Chrome/130 UA вместо `K.DEFAULT_USER_AGENT` (сейчас строки совпадают — дрейф будет молчаливым; `test_shared_session.py:49` дублирует тот же литерал).

### DL-19 · LOW — Доступность gateway привязана к worker.js

`engine.py:537`: `run_gateway_click` гейтится на `self.available()`, который требует `worker.js`, хотя gateway'у нужны только `_gateway_worker` + `_deno_cache`. Если frozen-сборка потеряет worker.js — consent-bypass молча умрёт. Гейт: `bool(self._bin and self._gateway_worker and self._deno_cache)`.

### Проверено и в порядке (загрузчик/движки)

- Протокол engine↔воркеры консистентен поле-в-поле (`{id, code, groups, pageUrl}` ↔ worker.js; `_call_dom` ↔ dom_worker.js:199; gateway ↔ gateway_worker.js:116); JSON экранирует переводы строк — framing держится; CRLF от Windows-пайпов толерантен к `line.strip()`.
- Воркеры рерутят console.* в stderr, Python спавнит с `stderr=DEVNULL` — классический дедлок переполненного stderr-пайпа исключён.
- Эскалация ограничена: `_escalation_tried`, 429 исключён, не-403/5xx исключены, повтор на уже-curl невозможен (http_engine.py:74-88); fail-open без curl покрыт тестами.
- Отзывчивость Stop: `Retry(total=0)`, проверки stop_event в начале download() (297), в чанк-циклах (448, 581); MT-потоки daemon; MT part-файлы чистятся при сбое чанка и в finally (523-530, 555-561).
- Сабпроцессы: списки аргументов без `shell=True` → нет Windows-квотинга/инъекций; `CREATE_NO_WINDOW`/`start_new_session`; ни одного `--allow-*` дену не выдано; `DENO_DIR` через env; `doc.write` исполняет JS страницы только в no-permission песочнице.
- wrap-логика worker.js (expression/statement dual compile + wrapCache) корректна; `parseCookies`/`diffCookies` в dom_worker соответствуют спеке `document.cookie`; gateway_worker кэширует `origHref` до `defineProperty` — рекурсия реально исключена.
- shared_session: гонки двойного создания нет (нет await между проверкой и присваиванием), close идемпотентен.
- Даунлоадер: атомарный `.partial` → `os.replace`; пер-запросные Accept/Referer через `_get_per_request_headers` (нет перекрёстного заражения заголовков); MT проверяет суммарный размер (540-545); санитизация имён файлов на входе (`parser_manager._sanitize_filename`, 1060-1077) покрывает нелегальные символы/длину.
- DenoJsEngine: `shutdown()` в `_main_task.finally` — осиротевших воркеров на нормальном конце задачи нет.

### Тест-пробелы B2

1. 39 из 80 относящихся тестов скипаются на этой машине (все curl_cffi + все Deno) — DL-1/DL-4/DL-8/DL-14 и API-дрейф curl_cffi в этом окружении неверифицируемы (корень — PKG-1).
2. Нет теста на закрытие escalation-сессии при ошибке (поймал бы DL-1).
3. `test_media_downloader.py` — 3 поверхностных фильтр-теста: MT-чанк-путь, stop посреди загрузки, `.partial`-клинап, size-mismatch, Content-Encoding, гонка имён (DL-2/3/5/12) — не покрыты.
4. Нет теста скоупинга `extension_cookies` — утечка DL-7 невидима для сюиты.
5. `fix_brotli.py`/`fix_lxml.py` — ноль тестов (ловушки DL-6/DL-17 молчат).

## B3. GUI, сервер расширения, очередь задач

### GUI-1 · CRITICAL — Колбэки HTTP-сервера нарушают потоковую модель Qt: `QTimer.singleShot` из не-Qt-потока

`src/gui/main_window.py:1082-1164` (`add_tasks_from_extension` исполняется в потоке aiohttp-сервера):
- `main_window.py:1140` `QTimer.singleShot(0, _auto_start)` и `:1163` `QTimer.singleShot(100, _do_update)` вызываются из `threading.Thread` **без Qt event loop** → таймеры не стреляют вообще (Qt: «Timers can only be used with threads started with QThread»). **Автостарт one-shot задач расширения и обновление таблицы задач не работают** как задумано.
- `main_window.py:1087` `self.settings_dialog.get_settings()` — возвращает сохранённый dict (GUI-виджеты не трогаются — это безопасно), но `main_window.py:1142-1155` мутирует `task_queue` из HTTP-потока, тогда как `TaskQueueManager` сам документирует «must be called from the GUI thread» (`task_queue_manager.py:26-27`); параллельная `save()` из GUI даёт интерливинг записи JSON.

В коде есть след правильного решения — мёртвый слот `_updateUiFromExtension` (`main_window.py:1244-1249`, «called via QMetaObject» — нигде не вызывается).

**Фикс (минимальный и потокобезопасный):**
```python
# в __init__ MainWindow:
self._ext_update_signal = Signal()  # объявить на уровне класса: ext_tasks_added = Signal()
self.ext_tasks_added.connect(self._updateUiFromExtension)

# в add_tasks_from_extension (HTTP-поток) заменить оба QTimer.singleShot:
self.ext_tasks_added.emit()          # queued-connection -> GUI-поток

# а в слоте (GUI-поток) делать и auto-start:
def _updateUiFromExtension(self):
    self._refresh_task_table()
    if self.task_queue.queue:
        self.task_table.selectRow(0)
        self._update_start_button_state()
    pending = getattr(self, '_ext_pending_autostart', None)
    if pending and self.task_queue.active_task is None:
        self.task_queue.start_task(pending.id)
        self._launch_parser_for_task(pending)
        self.update_ui_state(True)
    self._ext_pending_autostart = None
```
а в HTTP-колбэке вместо `QTimer.singleShot(0, _auto_start)` сохранить `self._ext_pending_autostart = task` (обычный атрибут, GIL-безопасно) и эмитить сигнал. Сам `add_task`/`add_tasks` допустимо оставить в HTTP-потоке только если убрать мутирующие сигналы — чище: колбэк только готовит данные, вся работа с queue в слоте.

### GUI-2 · HIGH — Гонка жизненного цикла QThread при быстрой смене задач (утечка потока / порча статуса)

Две разные стратегии запуска дают две разные ошибки:
- `_launch_parser_for_task` (`main_window.py:809-837`) **отключает** старый `task_ended` (815-817), но не quit/wait старого `parser_thread` — при «пауза → быстро старт другой» старый QThread остаётся жить (утечка; риск «QThread destroyed while running»).
- `_start_from_url_input` (`main_window.py:839-891`) — почти дубликат, но сигнал **не отключает**: поздний `task_ended` старого pm вызовет `on_task_ended` с чужим reason → пометит НОВУЮ активную задачу completed/stopped (`main_window.py:1052-1062`), а при reason="completed" ещё и авто-стартует третью задачу (`:1073-1080`).

Корень: `task_ended` не несёт task_id — `on_task_ended` не может отличить «свою» задачу от чужой.

**Фикс:**
1. В `ParserManager` сигнал `task_ended = Signal(str, str)` (task_id, reason); в `on_task_ended` игнорировать несовпадение с текущей активной задачей.
2. В `_launch_parser_for_task` перед пересозданием гарантировать смерть старого потока:
```python
if self.parser_thread and self.parser_thread.isRunning():
    self.parser_thread.quit()
    if not self.parser_thread.wait(5000):
        self.log_handler.warning("Old parser thread did not stop in 5s")
```
3. Устранить дублирование: `_start_from_url_input` должен добавлять задачу в queue и звать `_launch_task_from_queue` (как это уже делает extension-callback), а не строить ParserManager второй раз.

### GUI-3 · HIGH — Stop чистит `*.part*` до фактической остановки потоков (Windows: заблокированные файлы)

`main_window.py:947-980`: `pm.stop_parsing()` асинхронный (только выставляет события), а `cleanup_partial_files` + удаление state-файла выполняются немедленно. Потоки-загрузчики ещё пишут: на Windows удаление открытого файла падает (OSError глотается, `:972-973`), новые `.part` создаются уже после чистки. Итог: мусорные частичные файлы остаются именно там, где пользователь ожидает чистоту.

**Фикс:** перед cleanup дождаться конца парсерного потока:
```python
if self.parser_thread and self.parser_thread.isRunning():
    self.parser_thread.quit()
    self.parser_thread.wait(10000)
```
и только потом `cleanup_partial_files`/удаление state.

### GUI-4 · HIGH — `task_queue.json`: неатомарная запись + один битый элемент стирает всю очередь

- `task_queue_manager.py:228-238`: `open("w")` + `json.dump` — крах посреди записи = битый файл.
- `load()` (`:240-258`): любой exception → return 0, **вся очередь молча теряется**; частные хрупкости: `TaskItem.from_dict` падает на неизвестном status (`task_item.py:73`) или битой дате (`:76-78`) — один кривой элемент убивает все задачи.

**Фикс:**
```python
def save(self, filepath):
    data = {...}
    tmp = filepath + ".tmp"
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, filepath)          # атомарно на Windows (Py3.3+)

def load(self, filepath):
    ...
    tasks = []
    for t in data.get("tasks", []):
        try:
            tasks.append(TaskItem.from_dict(t))
        except Exception as e:
            logger.warning(f"Skipping corrupt task entry: {e}")
    self._queue = tasks
```
**Тест:** юнит-тест на save→load roundtrip, битую запись, битый элемент.

### GUI-5 · MEDIUM — Extension-сервер не останавливается при закрытии окна

`main_window.py:1196-1227` `closeEvent` не трогает `_server_thread`/`extension_server`: daemon-поток убивается процессом жёстко; `ExtensionServer.stop()` (`http_server.py:54-60`) никогда не выполняется — сокет освобождается ОС, но в лог уходит ложное молчание, и graceful-shutdown код (написанный!) мёртв.

**Фикс:** в closeEvent перед accept:
```python
if self._server_thread and self._server_thread.isRunning():
    try:
        asyncio.run_coroutine_threadsafe(self.extension_server.stop(), self._server_loop).result(timeout=3)
    except Exception:
        pass
```
(сохранить loop сервера в `run_server` как `self._server_loop`); или проще — `self._server_thread` не daemon + join(3000).

### GUI-6 · MEDIUM — `get_settings_from_ui` теряет ключи при каждом сохранении (audio-форматы, http_impersonate)

`settings_dialog.py:729-806` собирает словарь с нуля и НЕ включает: `SETTING_ENABLED_AUDIO_FORMATS` (audio-allowlist существует в constants/sanitize/потребителях, но из UI пропадает → сбрасывается), `SETTING_HTTP_IMPERSONATE` (теряется при каждом Save), `SETTING_FILTER_HIDDEN_LINKS` в DEFAULT_SETTINGS_VALUES отсутствует (constants.py:243 определяет, в dict на :255-288 нет — см. GUI-8). Половина ключей пишется литералами (`"search_depth"`), половина через `K.*` — прямое нарушение конвенции «single source of truth» из AGENTS.md.

**Фикс:** добавить в `get_settings_from_ui`:
```python
settings[K.SETTING_ENABLED_AUDIO_FORMATS] = list(
    self.settings.get(K.SETTING_ENABLED_AUDIO_FORMATS, K.DEFAULT_ENABLED_AUDIO_FORMATS))
settings[K.SETTING_HTTP_IMPERSONATE] = self.http_impersonate_combo.currentData() or K.DEFAULT_HTTP_IMPERSONATE
```
и перевести все литералы на `K.SETTING_*` (K есть для всех, кроме `page_limit` — добавить `SETTING_PAGE_LIMIT` в constants).

### GUI-7 · MEDIUM — Логи вставляются в QTextEdit как HTML без экранирования

`log_handler.py:148-151`: `f'<span style="color: {color}; ...>{message}</span>'` + `text_edit.append(...)`. Любое `<` в сообщении (а их много: «Filtered <img> tag», HTML-фрагменты в ошибках) либо глотается как тег, либо ломает разметку строки. Реальный, ежедневно видимый дефект отображения.

**Фикс:**
```python
import html
formatted_message = f'<span style="color: {color}; font-family: monospace;">{html.escape(message)}</span>'
```

### GUI-8 · MEDIUM — Дрейф констант и настроек

- `SETTING_FILTER_HIDDEN_LINKS` (constants.py:243) не входит в `DEFAULT_SETTINGS_VALUES` — дефолт «растворён» в UI-коде.
- `SETTING_PAGE_LIMIT` не существует вовсе — `"page_limit"` живёт литералом (dialog `:752`, sanitize `:845`).
- `DEFAULT_PORT = 19876` захардкожен в `http_server.py:19`, в extension — `API_BASE` (background.js:6); docstring сервера говорит «configurable» — не настраивается.
- `main.py:40` версия `1.0.0`, `setup.py` — `1.1.0`, манифест — `1.0.4`, релиз — `1.2.0` (см. X-5).

**Фикс:** завести `K.APP_VERSION` и `K.EXTENSION_API_PORT`, использовать везде; пополнить `DEFAULT_SETTINGS_VALUES`.

### GUI-9 · LOW (прочее GUI/queue)

- `main_window.py:1096-1099` — `source_page.split("/")[2]` без try (в отличие от соседнего блока с try/except IndexError); та же логика извлечения домена продублирована 4 раза (`add_task_to_queue:405`, `_start_from_url_input:856`, extension-callback `:1099/:1148`, плюс popup) → вынести `url_to_folder_name(url)` в `src/parser/utils.py`.
- `main_window.py:1106` — `task = add_task(source_page or urls[0]["url"], ...)` использует task-URL=странице; при пустом `source` — первый медиа-URL как «страница». Задокументировать/явно.
- `main_window.py:1130` — `task._pending_downloads = items`: приватный атрибут TaskItem, поля нет в dataclass; поле не сериализуется → после рестарта one-shot задача превращается в crawl. Завести явное поле `pending_downloads: list` с default None в TaskItem (не сериализовать).
- `main_window.py:1116` — `media_type = item.get("type", "image" if is_media_url(url) else "image")` — обе ветки `"image"`: мёртвый условный. Упростить.
- `task_queue_manager.py:202-224` — `cleanup_partial_files` удаляет ВСЕ файлы нулевого размера в папке задачи (включая пользовательские пустые) и матчит `*.part*` также `foo.partial.png`. Сузить до `*.part` и `*.part[0-9]*` паттернов загрузчика.
- `task_queue_manager.py:153-162` — `start_next()` подхватывает и PAUSED-задачи: авто-старт после завершения задачи возобновит задачу, которую пользователь поставил на паузу сам. Если это не задумано — фильтровать только QUEUED для автостарта (оставить PAUSED для ручного Resume). Требует решения владельца (поведение, не баг).
- `main_window.py:1196-1227` — `closeEvent`: `pm.stop_parsing()` затем `quit()/wait()` без таймаута — зависший воркер замораживает закрытие; `wait(10000)` + лог.
- `_stats_timer` (main_window.py:67-69) тикает всегда, даже без активной задачи — дешёво, но можно стартовать/останавливать по update_ui_state.
- `settings_dialog.save_settings` (`:812-831`) — тоже неатомарная запись settings.json; тот же tmp+os.replace.

### Что в GUI/queue проверено и в порядке

- `GUILogHandler.emit` из любого потока → `log_signal` (queued) — потокобезопасно; history deque(5000) с автоочисткой.
- `TaskItem`/`TaskQueueManager` CRUD и вставка «после последней не-терминальной» корректны; signals эмитятся консистентно.
- `app_paths` формулы едины (`task_state_path` используется save/load/delete); portable-дев vs frozen расходятся правильно.
- `run_server` (main_window.py:1176-1194): port-bind ошибка ловится и логируется, loop закрывается в finally.
- `_refresh_task_table` сохраняет selection по task_id (не по строке) — корректно при перестановках.

## B4. Паттерны, sieve, ресурсы (PAT)

### PAT-1 · HIGH — Дефолтный запуск (dev и fresh clone) загружает 0 Imagus-sieve правил

`site_pattern_manager.py:68-123`: автоскан смотрит только `resources/patterns/` (dirname встроенного файла), exe-dir (только frozen) и dir пользовательского `custom_pattern_path`. Оба sieve-файла лежат в корне репо — вне всех точек. ✅ **воспроизведено:** `imagus_rules total: 0` (native 32). README обещает «849 правил при первом запуске»; settings.json разработчика вручную указывает `imagus_sieve_path` на dist-файл — ручной костыль, подтверждающий дыру. Флагманская фича тихо не работает на чистой машине.

**Фикс:** в `load_patterns` добавить корень приложения в поиск:
```python
search_dirs.append(exec_dir)  # exec_dir уже вычисляется как get_app_dir() для dev
```
и/или перенести актуальный sieve в `resources/patterns/` и дефолт `SETTING_IMAGUS_SIEVE_PATH` на него.
**Тест:** «SitePatternManager() с дефолтами грузит >0 sieve правил» (сейчас такого теста нет — потому дыра и жила).

### PAT-2 · HIGH — Frozen-сборка грузит ОБА sieve (1672 правила, 157 конфликтующих)

`build_exe.py:88-90` копирует все `Imagus_sieve*.json` рядом с exe; загрузчик сканит exe-dir и аппендит без дедупа по имени правила. Апрельский 849 + июльский 823 → 1672 правила; для 157 сайтов выполняются старый и новый трансформы (устаревшие кандидаты попадают в очередь), каждый URL прогоняется через двойной regex-набор.

**Фикс:** в build_exe копировать только свежий: `max(glob("Imagus_sieve*.json"), key=os.path.getmtime)`; в `_load_imagus_file` — дедуп по имени правила (последний файл выигрывает); устаревший файл удалить из репо (сначала обновить пины тестов — TST-5).

### PAT-3 · HIGH — `$&` (всё совпадение) в Imagus-шаблонах не конвертируется → буквальный `$&` в URL

`site_pattern_manager.py:232-237` конвертирует только `$n`. ✅ **воспроизведено:** `_sanitize_imagus_target('#$1_fullsize\n$1\n$&')` → `'#\\g<1>_fullsize\n\\g<1>\n$&'`; `m.expand` оставляет `$&` литералом → кандидат `https://$&` уходит в media_files (`webpage_parser.py:738-745` возвращает весь список). В июльском sieve таких правил 15 (`nsimg-CDN`, `Keepa`, `Tiermaker-g-p`, ...).

**Фикс:**
```python
def _sanitize_imagus_target(self, target):
    if not isinstance(target, str):
        return target
    target = re.sub(r'(?<!\\)\$&', r'\\g<0>', target)
    return re.sub(r'(?<!\\)\$(\d+)', lambda m: f'\\g<{m.group(1)}>', target)
```

### PAT-4 · MEDIUM — Нативные site_patterns применяют JS-синтаксис `$n` сырым `re.sub` → буквальный `$2` в URL

`site_pattern_manager.py:595,609` (+`:927` для global). ✅ подтверждено: tumblr `photo_transform` `{source: '_(\\d+)\\.(jpe?g|png|gif)$', target: '_1280.$2'}` → трансформ даёт `tumblr_xyz_1280.$2` (в Python `$2` — не группа). Все `$n`-цели нативных паттернов сломаны.

**Фикс:** пропускать target через `_sanitize_imagus_target()` на трёх местах применения (для целей без `$` — no-op).

### PAT-5 · MEDIUM — `$10`+ расходится с JS-семантикой; 28 правил молча умирают

`site_pattern_manager.py:236-237`: `\\$(\\d+)` без ограничителя разрядов → `$11200x1200...` правила Deezer превращается в `\\g<11>` (несуществующая группа) → исключение глотается (719-721) → правило мертво. JS читал бы `$1` + `"1200x1200…"`.

**Фикс:** контекстная подстановка в момент матча — в лямбде `re.sub` (строка ~704) клампить `min(n, m.re.groups)`: если групп меньше, откусывать старшие разряды как текст (поведение JS).

### PAT-6 · MEDIUM — Разделы site_patterns.json мертвы (дрейф схемы vs потребителя)

Живой верифицировано агентом:
- корневые `google_images`/`yandex_images` не читаются загрузчиком (тот смотрит только `data['patterns']`+`global_settings`, `:134-153`) — `google.com/imgres?imgurl=...` не апгрейдится;
- `imagus_patterns` у twitter/instagram/deviantart/reddit используют ключи (`pic`, `html`, `graphql`, `art`, `gallery`), которых нет в захардкоженном списке потребителя `['photo_transform','media','image']` (`:604`), или dict вместо list (`:606`);
- `nsfwalbum.image_transformations` — dict по хостам, потребитель требует `{replace_patterns: [...]}` → раздел игнорируется целиком;
- `imagus_pattern` (singular, imgur) не потребляется нигде (0 grep-хитов).

**Фикс:** либо расширить читатель (принимать dict-формы; читать google/yandex-корневые записи как паттерны), либо удалить мёртвые данные из JSON — половинчатое состояние хуже любого из вариантов. Минимум: google/yandex записи обрабатываются, т.к. это видимые фичи README.

### PAT-7 · MEDIUM — domain_blocklist раздвоен: dev и frozen блокируют разное

`parser_manager.py:176-226` (порядок поиска) + два файла: `src/parser/domain_blocklist.txt` (40 строк, устаревший: НЕТ соцсетей) и `resources/domain_blocklist.txt` (25 строк, актуальный: facebook/instagram/x...). Шаг 3 (`:196`) находит src-копию — dev всегда побеждает устаревший файл; frozen грузит resources-копию. `facebook.com` в dev парсится, в exe блокируется. В src-копии мусор (`चना.com`, ведущий пробел).

**Фикс:** удалить `src/parser/domain_blocklist.txt` из индекса — resources-копия выиграет через шаг 5.

### PAT-8 · MEDIUM — junk_allowlist.txt не находится в dev (офф-на-уровень-пути)

`junk_filter.py:82-87`: кандидат `dirname(dirname(__file__))` = `src/` + имя файла → `src/junk_allowlist.txt` (нет). Реальный файл — `resources/junk_allowlist.txt`. → allowlist («safety valve» по доке модуля, строки 16-18) в dev всегда пуст.

**Фикс:** добавить кандидат `os.path.join(app_root, "resources", ALLOWLIST_FILENAME)` (или использовать `src/app_paths.resource_path`).

### PAT-9/10/11 · LOW

- **PAT-9** `site_pattern_manager.py:100-102` + `115-123`: site_patterns.json грузится дважды в dev (built-in + скан той же директории). `continue` при совпадении путей.
- **PAT-10** `:73-84`: ранний `return` в frozen при site_patterns.json рядом с exe пропускает sieve-скан совсем (латентно, текущий build_exe не триггерит). Заменить return на пропуск к скану.
- **PAT-11** Эвристики: `_extract_domain_from_regex` требует экранированные точки (правила вида `^example.com/` падают в 800+ global-список); stripped только `www.`; правило `Ning` не компилируется и тихо пропускается (штатный fail-open). Задокументировать, не чинить срочно.

### Проверено и в порядке (паттерны)

- `pattern_manager.py` — чистый 14-строчный алиас на SitePatternManager, риск «воскрешения» нет.
- `imgbox/teenplanet/pichunter/nudegals` replace_patterns работают end-to-end (агент проверил живьём: `thumbs.imgbox.com/..._b.jpg` → `images.imgbox.com/..._o.jpg`).
- Пер-правильная изоляция исключений в `transform_image_url` и загрузчиках файлов: одно битое правило не роняет прогон.
- `apply_link_url_transform` с убывающим `$10`-раньше-`$1` корректен; `get_link_rule` гвардит `re.error`.

## B5. Упаковка и зависимости (PKG)

### PKG-1 · HIGH — curl_cffi объявлен, но не установлен в venv

✅ воспроизведено: `import curl_cffi` → ModuleNotFoundError; `pip list` его не содержит. Последствия: дефолтно-включённая эскалация (`SETTING_HTTP_ESCALATE: True`, constants.py:238-239) молча no-op'ает (fail-open корректен — `http_engine.py:43-62`); 9 тестов скипаются; `build_exe.py:66 --collect-all=curl_cffi` на этом venv соберёт exe без фичи.

**Фикс:** `venv/Scripts/python.exe -m pip install -r requirements.txt`, перезапустить тесты (9 скипов должны уйти).

### PKG-2 · MEDIUM — setup.py дрейфует от requirements.txt

Не хватает только `curl_cffi>=0.14.0` (`setup.py:21-35`); версия `1.1.0` против релиза `1.2.0`. `pip install .` даёт env, где `http_engine=curl_cffi` не работает (fail-open маскирует). **Фикс:** добавить строку, поднять версию, взять версию из K.APP_VERSION (см. GUI-8).

### PKG-3 · MEDIUM — Маркер cchardet сломан дважды; Linux-установка не импортирует парсер

`requirements.txt:21` и `setup.py:30`: `cchardet>=2.1.7; sys_platform != 'win32'` — но `webpage_parser.py:31` делает безусловный `import chardet`. cchardet не предоставляет модуль `chardet` → Linux-env по requirements вообще не может импортировать парсер. Плюс cchardet не собирается на Python 3.10+ (dev — 3.12).

**Фикс:** убрать пару маркеров, оставить единственный `chardet>=5.0.0` в обоих файлах.

### PKG-4/5/6/7 · LOW

- **PKG-4** Установлены и `brotli`, и `brotlicffi` — оба владеют модулем `brotli`; какой выиграет, зависит от порядка установки. Оставить `brotli` (CPython).
- **PKG-5** Неиспользуемые зависимости: `filetype` (0 импортов в src/main/build), `cryptography` (0 импортов; для requests/aiohttp не нужна; curl_cffi вендорит BoringSSL). Убрать или задокументировать пин.
- **PKG-6** `WebMediaParser.spec` покрывает только половину build_exe.py (без копирования sieve/allowlist/deno-обвязки) — трактовать spec как генерируемый артефакт (он и так перегенерится); дублированный блок комментариев build_exe.py:127-133 почистить; `find_packages()` в setup.py ставит код как top-level `src`-пакет (переименовать/явные packages).
- **PKG-7** 122 МБ `WebMediaParser_v1.2.0.zip` в корне не в .gitignore — один `git add .` от попадания в историю. **Фикс:** добавить `WebMediaParser_v*.zip` (и `release/`) в `.gitignore`.

## B6. Качество тестов (TST)

### TST-1 · HIGH — Три группы тестов не проверяют продакшн-код

- `test_sec3_fixes.py:64-89` (TestLikelyThumbnail): ✅ проверено чтением — тест инлайн «симулирует» логику (`if any(h in url ...)`) и ассертит собственную копию; `_extract_images` не вызывается. Пассуется при удалении фичи.
- `test_sec3_fixes.py:239-286` (TestBackupExcludes): патчит `backup.create_backup` локальной ре-имплементацией и проверяет фейк.
- `test_crawler_frontier.py:304-341` (TestPageLimitSemantics): все три теста дёргают `pm._pages_with_downloads` напрямую и ассертят арифметику множеств; продакшн-методы не вызываются.

**Фикс:** переписать на вызов реального кода (`_process_parser_results` с фейк-сессией; реальный `backup.create_backup` с monkeypatched-корнем; ассерты на attrs от `_extract_images`).

### TST-2 · HIGH — 40/220 (18%) скипов; целые поверхности не тестированы

Причины (живой `-rs`): Deno отсутствует — 30 тестов (весь P0/P1/P4 JS-стек: js_engine, gateway bypass); curl_cffi не установлен — 9 тестов (PKG-1, НЕ легитимный скип); 1 сетевой плейсхолдер с телом `pass` (`test_js_processing.py:138` — удалить). CI-конфига нет — скипнутый набор нигде не гоняется, регрессии Deno-фич невидимы.

**Фикс:** установить curl_cffi (уберёт 9); для Deno — CI-профиль с deno.exe или явная маркеровка машинного профиля; плейсхолдер удалить.

### TST-3/4/5/6 · MEDIUM

- **TST-3** Слабые ассерты: `test_pattern_manager.py:58-68` — `expected_result` распакован, но не ассертится (только `!=` оригиналу); `test_js_engine.py:455-482` — вакс-тест (комментарий признаёт «No crash is the core assertion»), нужен `else: pytest.fail`; `test_crawler_frontier.py:295-301` — только `p > 0`; `test_parser_manager_filtering.py:114-121` — ассерты на подстроки логов (хрупко к переформулировкам).
- **TST-4** Нет `conftest.py`: `sys.path.insert` в 12 файлах, `MockGUILogHandler` в 3, `_DummySession/_make_parser` в 2, два почти одинаковых `FakeResp` в одном файле. Вынести в conftest.
- **TST-5** Тесты пинуют РАЗНЫЕ снапшоты sieve (April в test_fullsize_discovery.py:27-28, July в test_js_engine.py:188-189) — следствие PAT-2; канонизировать один файл + тест «дефолты грузят sieve» (ловит PAT-1).
- **TST-6** Нет тестов ровно там, где сломано: `_sanitize_imagus_target` ($&/$nn — PAT-3/5), порядок `_load_domain_blocklist` (PAT-7), dev-путь `load_allowlist` (PAT-8 — тесты monkeypatch-ат функцию, путь недостижим), нет тестов трансформов из shipped site_patterns.json (PAT-6), нулевое покрытие `_extract_jsonld_media` (CORE-9), `_invoke_parser` JSON-ветки (CORE-1), `is_same_domain` (CORE-6), 429/Retry-After (CORE-3), `_get_video_platform` (CORE-7), http_server (origin-валидация, CORS — тривиально тестируемо).

### Хорошие тесты (не трогать, использовать как образец)

`test_fullsize_discovery.py`, `test_crawler_frontier.py` (F1-F5), `test_junk_filter.py`, `test_http_engine.py` (fail-open матрица), `test_bugfixes.py`, `test_format_filter.py`, `test_gateway_bypass.py` (20 тестов gateway-эвристик).

---

## Взаимосвязанные находки (расширение ↔ приложение)

### X-1. Двусмысленный протокол POST /api/tasks (без валидации на приёмнике)

Цепочка: `popup.js:401-407` → `background.js:374-389` → `http_server.py:110-148` → `main_window.py:1084-1164`.
1. Поле `source` в элементах означает referer (страницу), но в результатах скана то же имя означает происхождение (`img`, `sieve-res`...) — конфликт семантик на одной схеме (EXT-4).
2. `original_url` всегда null из-за опечатки `item.original` (EXT-2).
3. Сервер не валидирует элементы (EXT-4-фикс) — приложение падает в 500 на кривом payload.
4. Ответ сервера `{"ok": true, "added": N}` — `added` = `len(urls)` на сервере, но колбэк возвращает `{"added": added}` где added = число реально собранных items (меньше при фильтрации не-http) — значение перезаписывается spread'ом `**(result or {})` (`http_server.py:143`) — колбэк-значение выигрывает, повезло; но семантика «added» на сервере уже мертва. Убрать серверный подсчёт.

### X-2. Junk/формат-фильтрация расходится между «Save (Chrome)» и desktop-путём

Расширение выбрасывает все PNG/GIF/SVG до отправки (EXT-8); приложение имеет формат-allowlist с `.png` ВКЛЮЧЁННЫМ по умолчанию + junk_allowlist, который в dev вообще не грузится (PAT-8) + junk-токены `pixel/creative` роняют легитимный контент (CORE-4). Пользователь получает три разных результата на одном сайте в зависимости от пути. Синхронизировать: уб-hardcoded-формат-фильтр из content_script, фикс CORE-4, фикс PAT-8.

### X-3. Sieve-трансформации: одна и та же база правил, три разных движка, три разных результата

`$&` работает в расширении (background.js:138-140), не работает в Python (PAT-3). `#ext#` в popup берёт первое расширение (sieve.js:92-99), Python-сторона вариантов не раскрывает вовсе. `$10`+ умирает в Python (PAT-5). Дедуп правил отсутствует в frozen (PAT-2). Канонизировать семантику (лучше по образцу JS — она правильнее) и покрыть тестами обе стороны (TST-6).

### X-4. `send-desktop` (deep) и one-shot контракты — см. EXT-3 + GUI-1: автостарт one-shot задач не работает (QTimer из HTTP-потока), поэтому «Download» из popup с «Page only» добавляет задачу, которая не стартует сама — пользователь должен жать Start вручную. Это самый заметный пользовательский симптом GUI-1.

### X-5. Версионный дрейф: manifest 1.0.4 / main.py 1.0.0 / setup.py 1.1.0 / релизный zip 1.2.0. Ни один источник не авторитетен. Завести `K.APP_VERSION` (+ `extension/manifest.json` синхронизировать при релизе скриптом `generate_icons.py`-стиля или checklist'ом релиза).

### X-6. Порт API: 19876 в трёх местах (http_server.DEFAULT_PORT, background API_BASE, README) без единого настраиваемого механизма — «configurable» в docstring сервера ложен (GUI-8).

### X-7. Маршрут cookies: расширение собирает → localhost POST → сессионный заголовк на ВСЕ хосты краула

Полная цепочка утечки (только вместе, блоками A+B она не видна): `background.js:356-372` (`getPageContext` собирает все cookies вкладки) → `POST /api/tasks` (ext) → `main_window.py:1090-1093` (`settings["extension_cookies"]`) → `shared_session.py:90-93` (заголовок Cookie общего aiohttp-сеанса) → каждый хост на любой глубине краула получает чужие сессионные cookie (DL-7). Смягчения на краях цепочки есть (localhost-only, origin-check «chrome-extension://*»), но среднее звено делает их бессмысленными для целевого сценария. Фикс — DL-7 (jar стартового домена) + EXT-11 (пин origin на ID расширения).

---

## Остаточные риски (не чинить вслепую)

1. **CORE-8 (квадратичный dynamic-scan)** — полный вынос экстракции в executor меняет порядок побочных эффектов; делать двумя отдельными PR (сначала `str(elem)`, потом offload) с ручной проверкой большой страницы.
2. **TQ `start_next()` подхватывает PAUSED** (GUI-9) — поведение может быть задумано как «resume-механика после рестарта»; менять только после решения владельца.
3. **Extension origin-пининг (EXT-11)** — до публикации в Web Store ID нестабилен; жёсткий пин сломает dev-режим. Ввести через настройку.
4. **PAT-6 (мёртвые разделы site_patterns.json)** — расширение читателя меняет поведение скачивания для google/twitter/imgur; требует решения «чинить или удалять данные» по каждому разделу.
5. **Денормализация `FULLSIZE_SOURCES`/`LINKS_CAP`/junk-паттернов между extension и app** (EXT-8/9) — полный unification тянет канал доставки данных (storage/onMessage); минимальный вариант (комментарии-якоря + тест на синхронность констант) безопаснее первого шага.
6. **GUI-2 (task_id в task_ended)** — менять сигнатуру сигнала затрагивает все 5 подключений; выполнять вместе с GUI-1 одним PR по жизненному циклу.

## Допущения

1. «Dev-окружение» = запуск из репо на этой машине (venv, Windows); все PATH-находки (PAT-1/7/8) верны для fresh clone и не влияют на уже настроенную машину разработчика (где settings.json указывает sieve вручную).
2. Поведение «queue auto-starts next task on natural completion» (AGENTS.md) — целевое; находки про PAUSED-подхват — вопрос семантики, не дефект кода.
3. Deno-функциональность недоступна в этом окружении — статический анализ JS-воркеров выполнен, runtime-поведение gateway не проверялось (30 скипнутых тестов).
4. curl_cffi-ветки проверены чтением и fail-open-тестами, но не живыми запросами (PKG-1).
5. Релизный zip в корне считался артефактом (не кодом) — в аудит не входил.

## Порядок внедрения (минимальные безопасные PR)

1. **PR-1 (critical, 1 строка + тест):** CORE-1 (+ CORE-13 вместе) — оживление JSON-парсера. Тест: `_invoke_parser` JSON-ветка.
2. **PR-2 (hang):** CORE-10 (+ CORE-11) — завершение задач при ошибке первой страницы.
3. **PR-3 (ресурсы):** CORE-2 (`async with`), CORE-3 (cap Retry-After). Тесты: фейки с async-CM; mock sleep.
4. **PR-4 (потеря контента):** CORE-4 (убрать pixel/creative), PAT-3+PAT-4+PAT-5 (санитайзер $&/$nn + прогон нативных целей через него). Тесты: негативы junk; снапшоты трансформов.
4a. **PR-4b (загрузчик, валидные файлы не должны удаляться):** DL-3 (Content-Encoding → не сверять размер), DL-2 (реестр занятых путей + уникальные temp-суффиксы), DL-1 (закрытие escalation-сессии на >=400), DL-4 (убрать verify=False). Тесты: gzip-мок GET; конкуренция двух потоков в один путь; close-сессии на 403.
5. **PR-5 (GUI-потоки):** GUI-1 (сигнал вместо QTimer из HTTP-потока) + GUI-2 (task_id в task_ended, wait старого потока) + GUI-3 (stop: wait перед cleanup). Ручная проверка: расширение → Download (page only) → автостарт; пауза → быстрый старт другой; Stop при активных загрузках.
6. **PR-6 (данные):** GUI-4 (атомарный save/load + per-item skip), PAT-1 (app-root в поиске sieve), PAT-7 (удалить src-копию blocklist), PAT-8 (dev-путь allowlist).
7. **PR-7 (extension):** EXT-1 (sieve-to в FULLSIZE_SOURCES), EXT-2 (original_url), EXT-4 (referer+валидация на сервере), EXT-6 (таймауты localhost), EXT-10 (убрать WAR/activeTab).
7a. **PR-7b (безопасность/утечки):** DL-7 (cookies в jar стартового домена вместо сессионного заголовка), DL-6 (вырезать вредоносный fix_brotli-фолбэк), DL-5 (.partial на отказах), DL-8 (таймаут Deno 5.0), DL-9 (wait после kill).
8. **PR-8 (зависимости):** PKG-1 (pip install -r), PKG-3 (chardet единый), PKG-2 (setup.py), PKG-7 (.gitignore zip).
9. **PR-9 (тесты):** TST-1 (переписать 3 группы), TST-4 (conftest.py), TST-6 (тесты на починенные места), TST-5 (канонический sieve).
10. **PR-10 (чистка):** CORE-14/15/16 (мёртвый код, дубли ad-логики), GUI-6/8 (потерянные ключи, K.APP_VERSION/PORT), GUI-7 (html.escape), EXT-12, PAT-9/10, PKG-4/5/6.
