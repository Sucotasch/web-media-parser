# АУДИТ 2026-10-14 — полный инженерный код-ревью репозитория

> Этот блок заменяет по актуальности аудит 2026-09-09 (см. ниже, раздел
> «ИСТОРИЧЕСКИЙ АУДИТ 2026-09-09»). Историю правок не удалять — она в git.
> **Код не менялся.** Изменены только `Audit.md` и добавлен леджер приёмки.

**Дата:** 2026-10-14 · **Baseline:** коммит `3aa79ae` (после обновления `AGENTS.md`).
**Постановка:** `ReviewPrompt.txt` — сплошное ревью всего репозитория: корректность,
конкурентность, утечки ресурсов, подавление ошибок, качество тестов, гигиена сборки
и зависимостей, безопасность как одна из частей (не как главная цель).
**Приоритет:** корректность → консистентность → поддерживаемость → стандарты.

---

## 1. Метод и объём

Репозиторий разбит на 7 непересекающихся зон, каждая разобрана отдельно, затем все
находки сведены и перепроверены. Объём на baseline:

| Что | Кол-во |
|---|---|
| Python-модулей (`src/` + `main.py`) | 30 файлов / **10 524 строки** |
| JS/MJS (`extension/` + `extension/tests/`) | 21 файл / **4 701 строка** |
| Тестов (pytest) | 21 модуль, **349 тестов** (319 passed + 30 skipped) |
| Extension node-сьютов | 12 файлов `*.test.mjs` |

Модули свыше 800 строк (риск для поддерживаемости, но **не** дефект сами по себе):
`webpage_parser.py` 1586, `parser_manager.py` 1229, `main_window.py` 1203,
`site_pattern_manager.py` 1124, `settings_dialog.py` 947.

**Леджер приёмки:** `GATES.2026-10-14.md`. Все 9 гейтов выполнены; измеренные
значения — в §2. Пометки проверки: **[исп.]** — воспроизведено исполнением в этом
аудите; **[чит.]** — установлено чтением кода с трассировкой всех вызывающих.

---

## 2. Прогон валидации (измерено на `3aa79ae`)

| Команда | Результат |
|---|---|
| `python -m pytest tests -q` | **319 passed, 30 skipped** (22 c) · G-1 |
| `python -m pytest tests -q -rs` | все 30 скипов — Deno/happy-dom на этой машине · G-2 |
| `node --check extension/{background,content_script,sieve,sieve_updater,shared}.js` | exit 0 · G-3 |
| `node extension/tests/*.test.mjs` (12 файлов) | **12 × ALL PASS**, exit 0 · G-4 |
| `python -m pyflakes src/ tests/` | 1 находка — намеренный `noqa` на `brotli` в `shared_session.py:21` · G-5 |
| `python -m ruff check src/ tests/ --select E722,E402,E741,E401,F` | 4 находки: 3×E402 (`webpage_parser.py:35-37`) + 1×E401 (`tests/test_fullsize_discovery.py:328`) · G-6 |
| инвентарь LOC/файлов (без `__pycache__`, `deno_cache`) | см. §1 · G-7 |
| `git status --short` / `git diff --stat` | изменены только `Audit.md`, `GATES.2026-10-14.md` · G-9 |

Для проверки конкретных находок использовались исполняемые repro-скрипты (Python и
Node); их вывод приведён прямо в находках. Все repro выполнялись только на чтение.

---

## 3. Сводка по severity

| Уровень | Кол-во | Ключевое |
|---|---|---|
| **Critical** | 1 | `resources/patterns/site_patterns.json` в `.gitignore` → сборка и тесты падают на чистом клоне |
| **High** | 16 | Cookie на диске; потеря in-flight загрузок при pause; blocklist не матчит поддомены; 4 дефекта sieve-паритета; неограниченный размер файла; обход корня закачки; `Deno`-глобаль в sandbox; блокирующая запись в stdin; движок на потоке event-loop; зависание попапа на Chrome <111 |
| **Medium** | 48 | Гонки, утечки, атомарность, тихие отказы, блокировка GUI-потока, дрейф паритета, дыры в тестах |
| **Low** | 15 | Инкапсуляция, мёртвые эндпоинты, дрейф доков, вакуумные тесты, гигиена `.gitignore` |
| **Итого** | **80** | |

Порядок починки — §7.

---

# БЛОК A — приложение (Python)

## A-01 · CRITICAL · `resources/patterns/site_patterns.json` в `.gitignore` — чистый клон не собирается и не тестируется

**Где:** `.gitignore:185` (`site_patterns.json`); потребители — `build_exe.py:53`,
`WebMediaParser.spec:5`, `tests/test_pattern_manager.py:31`.

**Механизм [исп.]:**
```
$ git check-ignore -v resources/patterns/site_patterns.json
.gitignore:185:site_patterns.json  resources/patterns/site_patterns.json
$ git ls-files resources
resources/dark_theme.qss  resources/domain_blocklist.txt  resources/icon.ico  resources/junk_allowlist.txt
```
Файл **есть на диске, но не в git**. `--add-data=resources/patterns/site_patterns.json`
на чистом клоне получает несуществующий источник, и PyInstaller 6.22.2 бросает
`SystemExit('ERROR: Unable to find ...')` — причём `build_exe.py:26` до этого уже
успел выполнить `pip install -r requirements.txt`.

**Почему это критично:** имя правила слишком общее — `.gitignore:185` писалось для
раннтайм-копии, но матчит ещё и продуктовый ресурс. Последствия: (1) `build_exe.py`
падает на чистом клоне; (2) фича «pattern + sieve» молча исчезает из сборки;
(3) `test_pattern_manager.py:31` (`assert pattern_count > 0`) и все тесты с
`imagus_sieve_path=SIEVE_PATH` краснеют.

**Готовое решение:**
```gitignore
# .gitignore — раннтайм-копия рядом с exe остаётся игнором, бандл-копия — нет:
site_patterns.json
!resources/patterns/site_patterns.json
!resources/patterns/
```
```bash
git add -f resources/patterns/site_patterns.json
```

---

## A-02 · HIGH · Cookie браузера пишутся на диск в открытом виде и переживают перезапуск

**Где:** `src/gui/main_window.py:56-58` → `src/core/task_item.py:53` →
`src/core/task_queue_manager.py:254` → `src/app_paths.py:48-50`; вторая копия —
`src/parser/parser_manager.py:1249` → `src/app_paths.py:67-77`.

**Механизм [чит.]:**
```python
# main_window.py:58
settings["extension_cookies"] = cookies          # расширение присылает cookie вкладки
# task_item.py:53 — сериализуется ВЕСЬ settings
return {"id": self.id, ..., "settings": self.settings, ...}
# TaskQueueManager.save(...) → task_queue.json рядом с exe
# parser_manager.py:1249 — и ещё раз в pickle состояния задачи:
"settings": self.settings,   # → {download_path}/sessions/{task_id}/last_session.pkl
```
`shared_session.py:84-136` аккуратно скоупит cookie на один домен **в рантайме** — и
эта мера полностью обнуляется персистом: значения оказываются в двух файлах на диске, в
месте, которое пользователь синхронизирует/бэкаит/зипует, и воспроизводятся при каждом
resume. Срок годности и контроля со стороны пользователя нет.

**Готовое решение** (раннтайм продолжает работать; resumed-задача просто потребует
повторной авторизации — что строго безопаснее):
```python
# src/core/task_item.py — в to_dict()
_CRED_KEYS = ("extension_cookies",)
settings = {k: v for k, v in self.settings.items() if k not in _CRED_KEYS}
return {"id": self.id, ..., "settings": settings, ...}

# src/parser/parser_manager.py:1249 — в словаре состояния
"settings": {k: v for k, v in self.settings.items() if k != "extension_cookies"},
```

---

## A-03 · HIGH · `save_state()` безвозвратно теряет загрузки и страницы в полёте

**Где:** `src/parser/parser_manager.py:1214-1320`, точки `get()` —
`parser_manager.py:972` (downloader) и `:856` (parser); вызывающие —
`main_window.py:1021-1026`, `:1280-1284`.

**Механизм [чит.]:** при pause/close окно зовёт `save_state()`, пока воркеры ещё живы.
Снимок читает только `download_queue._queue` / `quarantine_queue._queue`, но элемент,
уже взятый `get()` воркером, в `_queue` не находится — при этом он есть в
`downloaded_files` / `processed_urls`. `load_state` (1306-1307) намеренно **не**
фильтрует по `downloaded_files`, поэтому элемент никогда не вернётся в очередь, а его
страница не будет перекраулена.

Grace-цикл 5 с (1217-1221) отваливается по таймауту на любой долгой загрузке, после
чего код только пишет `logger.warning` — для пользователя потеря тихая.
**Конкретный вход:** pause во время стрима 40 МБ MP4 → файл и текущая страница
навсегда исчезают из состояния resume.

**Готовое решение:**
```python
# __init__ (после строки 143)
self._inflight_media: Dict[str, dict] = {}
self._inflight_url_item: Optional[Tuple] = None

# _downloader_worker — после get() (около строки 976)
mu = media_item.get("url", "")
self._inflight_media[mu] = media_item      # видимо для save_state
...
finally:
    self._inflight_media.pop(mu, None)

# _parser_worker — после self._active_tasks += 1 (около 862)
self._inflight_url_item = (current_url, depth, source_page_url, context)
...
finally:
    self._inflight_url_item = None

# save_state — сразу после grace-цикла (около 1221)
if self._active_tasks > 0:
    download_queue_items.extend(self._inflight_media.values())
    if (self._inflight_url_item
            and self._inflight_url_item[0] not in self.processed_urls):
        url_queue_items.append(self._inflight_url_item)
```

---

## A-04 · HIGH · `domain_blocklist.txt` не матчит поддомены — 16 из 18 записей мертвы

**Где:** `src/parser/parser_manager.py:799-802` + `src/parser/utils.py:126-133`
(`get_domain()` возвращает `netloc` целиком) + `resources/domain_blocklist.txt:6-25`.

**Механизм [исп.]:**
```
blocklist size: 18
https://ad.doubleclick.net/x.jpg        domain=ad.doubleclick.net        blocked=False
https://www.googletagmanager.com/a.js   domain=www.googletagmanager.com   blocked=False
https://scontent.xx.fbcdn.net/y.jpg     domain=scontent.xx.fbcdn.net     blocked=False
https://www.facebook.com/x              domain=www.facebook.com          blocked=False
https://video.twimg.com/x.mp4           domain=video.twimg.com           blocked=False
https://cdn.taboola.com/a.png           domain=cdn.taboola.com           blocked=False
https://doubleclick.net/x.jpg           domain=doubleclick.net           blocked=True
```
Проверка — точное членство в `self.blocked_domains`, а список содержит
регистрируемые домены. Социальные записи (именно те, что **не** покрыты
`junk_filter`) не срабатывают никогда; единственный живой `doubleclick.net` уже
ловится суффиксным матчером `junk_filter._host_matches_ad` с точной граничной точкой.
То есть блоклист не добавляет ничего из того, что декларирует.

**Готовое решение:**
```python
# src/parser/parser_manager.py:799
disc_domain = registrable_domain(get_domain(abs_disc_url))   # было: get_domain(...)
```
```python
def test_blocklist_blocks_subdomains(tmp_path, monkeypatch):
    from src.parser.parser_manager import ParserManager
    from src.parser.utils import get_domain, registrable_domain
    pm = ParserManager.__new__(ParserManager)
    pm.blocked_domains = ParserManager._load_domain_blocklist(pm)
    assert "doubleclick.net" in pm.blocked_domains
    assert registrable_domain(get_domain("https://www.facebook.com/x")) == "facebook.com"
    assert registrable_domain(get_domain("https://ad.doubleclick.net/x.jpg")) == "doubleclick.net"
```

---

## A-05 · HIGH · URL с нулевым приоритетом навсегда отравляет `_seen` и не может быть добавлен снова

**Где:** `src/parser/priority_url_queue.py:542` (`self._seen.add(url)`) и `:561-563`
(`if priority <= 0.0: return`); список исключений дедупа — `:538`.

**Механизм [исп.]** (воспроизведено на реальном классе):
```
S-01 after plain put:      queued=0 seen=1
S-01 after from_image put: queued=0 seen=1   <-- 1 expected, 0 means poisoned
```
`_seen.add` стоит **до** проверки приоритета, поэтому любой отклонённый URL
(`priority <= 0`) помечается просмотренным навсегда. Контекст `from_image` исключён
внутри `_is_downward_url` (`:89`), но **не** в списке исключений дедупа (`:538`
перечисляет только `interstitial_retry` / `from_media_item`), поэтому повторная
постановка того же URL из пре-прохода `from_image` (`parser_manager.py:807/824`)
молча отбрасывается.

Живой путь: `parser_manager.py:937` (`from_media_item`, кросс-хост media→webpage)
считает приоритет относительно `start_url`, получает 0, помечает URL просмотренным;
тот же внешний fullsize-viewer URL позже приходит как thumbnail-ссылка `from_image` и
теряется → цепочка sieve fullsize-discovery для этого хоста не запускается **всю
задачу**.

**Готовое решение:**
```python
            priority = self._calculate_url_priority(url, depth, effective_source_url, context)
            if priority <= 0.0:
                logger.debug(f"Skipping URL with zero priority: {url}")
                return
            self._seen.add(url)          # «просмотренным» считается только реально поставленный URL
```
(плюс тест: отклонить URL с нулевым приоритетом, затем пере-поставить его с
`from_image: True` и assert, что он в очереди.)

---

## A-06 · HIGH · Протокол-относительный шаблон `to` даёт `https:////host/path`

**Где:** `src/parser/site_pattern_manager.py:949-951` (regex-путь) и `:899-901`
(вызываемый путь); правильная ветка для сравнения — `:925-926`.

**Механизм [исп.]:**
```
S-02 transform_image_url:
    https://pics.photo-forum.net/static/site_thumbs/abc.jpg
    https:////pics.photo-forum.net/static/site_pics/abc.jpg      <-- мусор
```
Когда `to` начинается с `//host/...`, а матчинг произошёл на схемо-очищенном
варианте URL, код добавляет `scheme://` и получает `https:////host`. В реальном
sieve 19 правил с `to`, начинающимся на `//` (в т.ч. `[Google_Images]`,
`Imgflip-h-p`, `OfferUp-pp`, `MakeAGif-g-p`, `Gofile.io-h-p`, `BHPhotoVideo-s`,
`Hearst|Cosmo|Elle|Esquire|Harper-m-p`).

**Готовое решение** (в оба места, по образцу строки 925):
```python
                                     if v_url != results[0] and '://' not in substituted:
                                         if substituted.startswith('//'):
                                             substituted = 'https:' + substituted   # протокол-относительный, не относительный
                                         else:
                                             scheme = results[0].split('://', 1)[0]
                                             substituted = f"{scheme}://{substituted}"
```

---

## A-07 · HIGH · Максимальный размер файла не ограничен нигде — диск можно забить одним скачиванием

**Где:** `src/downloader/media_downloader.py:412` (HEAD `Content-Length`),
`:497-501`, `:521-532` (цикл стрима), `:684-693` (MT-чанк).

**Механизм [чит.]:** `Content-Length` читается, но используется только для
*минимального* порога (425-429), решения MT/no-MT (437-441) и математики прогресса
(531). Проверка размера (557-564) выполняется **после** окончания стрима, поэтому
остановить заполнение диска не может. В `constants.py` есть `SETTING_MIN_IMG_SIZE` /
`SETTING_MIN_VID_SIZE` и **нет** ключа максимального размера (grep
`MAX_FILE|SIZE_LIMIT|max_file` по `src/` — пусто). **Триггер:** хост, отдающий
корректный `Content-Length`, а затем бесконечный поток — запись идёт до заполнения
тома.

**Готовое решение:**
```python
# __init__ (после строки 153)
self.max_bytes = int(self.settings.get("max_file_size", 0)) * 1024

# в цикле single-thread — после downloaded_bytes += len(chunk) (строка 529)
# и в MT-чанке — после downloaded_this_chunk += len(chunk_data) (строка 693):
if self.max_bytes and (downloaded_this_chunk + len(write_buffer_chunk)) > self.max_bytes:
    raise IOError(f"Aborted: exceeds max_file_size ({self.max_bytes} bytes)")
```
Плюс `SETTING_MAX_FILE_SIZE` / `DEFAULT_MAX_FILE_SIZE` в `constants.py` и поле в
`settings_dialog.py`, чтобы предел был управляемым, а не хардкодом.

---

## A-08 · HIGH · `Deno`-глобаль доступна коду правил → инъекция строк в JSON-протокол воркера

**Где:** `src/parser/js_engine/worker.js:120-123`,
`src/parser/js_engine/dom_worker.js:226-231`; непроверяемый ответ —
`src/parser/js_engine/engine.py:434-441`.

**Механизм [чит.]:** `new Function` компилирует тело правила в **глобальной** области,
поэтому идентификатор `Deno` резолвится в `globalThis.Deno`. Флаги `--allow-*` **не**
ограничивают `Deno.stdout.writeSync()` / `Deno.stdin.readSync()` / `Deno.exit()`.
Правило вида
```js
Deno.stdout.writeSync(new TextEncoder().encode('{"id":0,"result":"https://evil/x.jpg"}\n'));
Deno.exit(1)
```
пишет лишнюю строку протокола, которую Python читает `_read_line_timeout` и
приписывает текущему запросу (`id` ответа не сверяется — `engine.py:437`).
Атакующий получает управление тем, что возвращается как «fullsize» URL. **Триггер:**
sieve JSON, загруженный с пользовательского URL репозитория — в
`settings_dialog.py:509-510` само предупреждение «sieve rules may contain executable
[JS]». Граница безопасности, заявленная в `engine.py:26-29` («no --allow-*»), для
stdio/exit неверна.

**Готовое решение** (1 строка в каждый воркер; на легитимные правила не влияет — ни
одно не ссылается на `Deno`):
```js
// worker.js — в handle(), при создании функции:
const fn = new Function("$", "location", "document", "window", "URL",
  '"use strict";\nconst Deno = undefined;\n' + wrapped);
```
```js
// dom_worker.js:226 — тот же лексический shadow:
'"use strict";\nconst Deno = undefined;\n' + code,
```
Плюс (см. B-16) сверять `id` ответа с `id` запроса и при несовпадении убивать воркер.

---

## A-09 · HIGH · Расширение матчит sieve-правила по полному URL — 425 правил с `^`-якорем мертвы

**Где:** `extension/background.js:638` (`applySieveRulesAll(item.url, ...)`) →
`extension/sieve.js:107-129` (`:110` — `rule.linkRegex.test(pageUrl)`,
`:113` — `url.match(rule.imgRegex)`).

**Механизм [исп.]** (реальный компилятор `parseSieve`, реальный синтаксис правил):
```
A) link-регекс без якоря:
   full URL     -> []
   stripped URL -> ["https://y.example.com/big/abc.jpg"]
B) link-регекс ^-якорный (конвенция Imagus, 457/823 правил):
   full URL     -> []
   stripped URL -> []
shipped rules with BOTH link and img ^-anchored: 168
shipped ^-anchored plain-template rules: 275
```
Два независимых дефекта в одном пути:
1. `sieve.js:113` матчит `imgRegex` по **полному** URL (`item.url` со схемой).
   Конвенция Imagus и Python-двойник (`site_pattern_manager.py:876-878`) матчат
   схемо-очищенный вариант. 275 non-JS правил с `^`-якорем на `img` не срабатывают.
2. `sieve.js:110` тестирует `linkRegex` только по `pageUrl` **со схемой**, тогда как
   `background.js:393` в twin-пути тестирует **оба** варианта:
   `if (!linkRegex.test(linkUrl.replace(/^https?:\/\//,"")) && !linkRegex.test(linkUrl)) continue;`
   168 shipped-правил с якорем и в `link`, и в `img` мертвы полностью.

**Готовое решение:**
```js
// extension/sieve.js — applySieveRulesAll
function applySieveRulesAll(url, pageUrl, rules) {
  const strip = (u) => u.replace(/^https?:\/\//, "");
  const urlForms = url === strip(url) ? [url] : [url, strip(url)];
  const pageForms = pageUrl === strip(pageUrl) ? [pageUrl] : [pageUrl, strip(pageUrl)];
  for (const rule of rules) {
    if (rule.linkRegex && !pageForms.some((p) => rule.linkRegex.test(p))) continue;
    for (const u of urlForms) {
      const match = u.match(rule.imgRegex);
      if (!match) continue;
      if (rule.isJS) {
        const result = executeSieveJS(rule.toPattern, url, match, pageUrl);
        if (result && result !== url) return [result];
        continue;                       // JS-правило без результата не маскирует следующие
      }
      const variants = applySubstitutionAll(u, match, rule.toPattern)
        .filter((v) => v && v !== url);
      if (variants.length > 0) return variants;
    }
  }
  return [];
}
```

---

## A-10 · HIGH · `$&` не подставляется в JS — 15 shipped-правил дают буквальный `$&`

**Где:** `extension/sieve.js:148-164` (`applySubstitutionAll`), особенно `:150-152`.

**Механизм [исп.]:**
```
applySubstitutionAll('x', ['tiermaker'], '$&')     -> ["$&"]
applySubstitutionAll('x', ['tiermaker'], '$&#mp4') -> ["$&#mp4"]
```
`pattern.replace(/\$(\d+)/g, ...)` не трогает `$&` (в нём нет цифр), поэтому он уходит
в результат дословно. В Python-двойнике `$&` → весь матчинг
(`site_pattern_manager.py:385`, `re.sub(r'(?<!\\)\$&', r'\\g<0>', ...)`; проверено:
`_sanitize_imagus_target("//x/$&$1.jpg", 1)` → `'//x/\\g<0>\\g<1>.jpg'`). JS-файл умеет
это в **своей** соседней функции: `background.js:215-217` (`applyUrlTransform`
заменяет `$&` на `matchGroups[0]`) — в одном расширении две разные семантики
подстановки, и неверная используется в sieve-пути. Затронутых shipped-правил: **15**
(`Bluesky|Bsky-p`, `BoundHub-x-pp`, `F95zone-x-p`, `Keepa`, `Newgrounds-g-pp`,
`nsimg-CDN`, `Pikabu.ru`, `Tiermaker-g-p`, `Tudocdn-CDN`, `Xfree.tube-x-p`,
`Yodayo-g-p`, `[XenForo]`, …).

**Опаснее всего:** JS-сьют **закрепляет** дефект —
`extension/tests/sieve_rules.test.mjs:76-79` утверждает
`applySubstitutionAll("x", [], "https://cdn/$&") === ["https://cdn/$&"]`. Тест надо
править вместе с кодом.

**Готовое решение:**
```js
// extension/sieve.js — applySubstitutionAll
  let result = pattern
    .replace(/\$(\d+)/g, (_, num) =>
      (match[num] !== undefined ? match[num] : "$" + num))   // семантика String.replace (clamp)
    .split("$&").join(match[0] || "");   // $& = весь матчинг (двойник: background.js:216)
```
```js
// extension/tests/sieve_rules.test.mjs:77-79 — развернуть ожидание
check("plain pattern yields a single variant",
      api.applySubstitutionAll("x", ["abc"], "https://cdn/$&"),
      ["https://cdn/abc"]);
```

---

## A-11 · HIGH · Многострочный `to` и второй `#блок#` сворачиваются в один URL

**Где:** `extension/sieve.js:155-163` (расширяется только **первый** `#…#`; символ
`\n` не обрабатывается вообще).

**Механизм [исп.]:**
```
'#$1_fullsize\n$1\n$&'   -> ["#_fullsize\n\n$&"]
shipped rules with multi-line to: 203
```
203 shipped-правила имеют многострочный `to`. В Python-двойнике `_expand_variants`
(`site_pattern_manager.py:667` и далее) делит по `\n` и рекурсивно раскрывает **все**
`#…#` блоки. В расширении N вариантов становятся 1 URL'ом с литеральным переводом
строки и нераскрытым `#…#` внутри — скачивание гарантированно невозможно.

**Готовое решение:**
```js
// extension/sieve.js — локальный помощник + замена возврата
function _expandLine(line) {
  const m = line.match(/#([^#]+)#/);
  if (!m) return [line];
  const pre = line.slice(0, m.index), post = line.slice(m.index + m[0].length);
  return m[1].trim().split(/\s+/).flatMap((o) => _expandLine(pre + o + post));
}
// в applySubstitutionAll вместо блока extMatch / return [result]:
return result.split("\n").map((l) => l.trim()).filter(Boolean).flatMap(_expandLine);
```
Тест на `\n` и на несколько `#блоков#` нужен в обе стороны (`tests/` и
`extension/tests/`).

---

## A-12 · HIGH · Расхождение clamp для выходящего за диапазон `$N` (`$5` при 2 группах)

**Где:** `src/parser/site_pattern_manager.py:392-396` (Python) против
`extension/sieve.js:151` (JS).

**Механизм [исп.]** — обе стороны прогнаны на одном входе:
```
Python: _sanitize_imagus_target("//x/$5.jpg", 2 groups)  -> '//x/$5.jpg'    <-- плейсхолдер выжил
        _sanitize_imagus_target("//x/$11.jpg", 1 group)  -> '//x/\\g<1>1.jpg'  (корректный clamp)
JS:     applySubstitutionAll("x.jpg", ["a"], "//y/$5.jpg") -> ["//y/.jpg"]  <-- группа выброшена
```
Цикл `for cut in range(len(digits)-1, 0, -1)` пуст для одной цифры, поэтому срабатывает
`return m.group(0)` и `$5` уходит в URL скачивания (404 или файл буквально с именем
`$5`). JS-двойник возвращает пустую строку.

**Готовое решение:**
```python
                 for cut in range(len(digits) - 1, 0, -1):
                     head = int(digits[:cut])
                     if head <= group_count:
                         return f'\\g<{head}>' + digits[cut:]
-                return m.group(0)
+                return ''          # нет такой группы — как JS-двойник: match[n] || ""
```
Плюс тест на одиночный выходящий за диапазон `$N` (сейчас `$10`/`$11` покрыты, а
`$5` — нет).

---

## A-13 · HIGH · Отсутствие `minimum_chrome_version` + промис-style `chrome.alarms` → попап висит навсегда

**Где:** `extension/manifest.json:1-15` (объявлен `strict_min_version` только для
Firefox, `:34-39`) · `extension/background.js:555,559,565,825,1091` ·
`extension/popup/popup.js:543`.

**Механизм [чит.]:** `chrome.alarms.create()` возвращает Promise **только с
Chrome 111+**. На Chrome 88–110 он возвращает `undefined`, и `.catch` на `undefined`
даёт `TypeError` внутри исполнителя `new Promise` (`background.js:807-825`, где
`keepaliveAcquire()` — первая инструкция). Экзекутор бросает → промис отклоняется →
`chromeDownload(...).then(sendResponse)` (`:1091`) **без `.catch`** → канал сообщения
не получает ответа → `await chrome.runtime.sendMessage(...)` в `popup.js:543` не
завершается никогда. Попап заморожен на «Saving N file(s)…», обе кнопки выключены,
ошибки не видно. В SW — необработанный rejection.

**Готовое решение:**
```json
  "minimum_chrome_version": "111",
```
```js
// background.js:825 — обернуть вызов, чтобы отказ keepalive не убивал операцию
try { keepaliveAcquire(); } catch (e) { resolve({ saved: 0 }); return; }
```
И (см. D-09) добавить `.catch` во все ветки `onMessage`.

---

## A-14 · HIGH · Нет взаимоисключения клавишной команды и попапа → дубликаты скачиваний

**Где:** `extension/background.js:984,993-1059` (`commandBusy` защищает только
командный путь) против `:662-740` (`handleStartScan`) и `:806-924` (`chromeDownload`).

**Механизм [чит.]:** `commandBusy` выставляется/сбрасывается только внутри
`commandScanAndProcess` (`:994-995`, `:1057`). Путь «попап → SW» добавлен позже и его
не затрагивает. **Триггер:** `Ctrl+Shift+S`, пока в попапе идёт сканирование или
«Save (Chrome)». Два проверенных исхода:
1. **Дубли скачивания.** Оба пути зовут `chromeDownload` с пересекающимся набором, а
   `conflictAction: "uniquify"` (`:868`) *гарантирует* второй физический файл
   (`name (1).jpg`) вместо дедупа. Два писателя `emitProgress()` (`:818-824`)
   перезаписывают `downloadProgress` друг друга.
2. **Перемешанное состояние скана.** Два `handleStartScan` пишут
   `scanProgress`/`scanResult` (`:672,692,723`) для одного `tabId`; побеждает
   последний писатель, поэтому попап может отрисовать медиа **старого** скана.

**Готовое решение:**
```js
let _scanBusy = false;                        // module level, рядом с commandBusy

async function handleStartScan(tabId) {
  if (_scanBusy) return { ok: false, error: "Another scan is already running" };
  _scanBusy = true;
  try { /* существующее тело */ }
  finally { _scanBusy = false; }
}
```
И в `commandScanAndProcess`: `if (commandBusy || _scanBusy) return;`

---

## A-15 · HIGH · Неограниченная блокирующая запись в stdin воркера на потоке event-loop

**Где:** `src/parser/js_engine/engine.py:423` (`_call`), `:506` (`_call_dom`),
`:554` (`run_gateway_click`).

**Механизм [чит.]:** `proc.stdin.write(request + "\n")` — полностью блокирующая запись
(`bufsize=1` + `text=True` флашит немедленно). DOM/gateway-воркеры не начинают читать
stdin, пока не резолвнется их верхний `import ... from "npm:happy-dom"`
(`dom_worker.js:18`, `gateway_worker.js:25`); даже `worker.js` доходит до цикла чтения
только после шага «Check» Дено (`worker.js:147`). Всё, что больше буфера
`BufferedWriter`+пайпа (~72 КБ — то есть любая реальная страница галереи), блокирует
вызывающего, пока ребёнок впервые не прочитает. Комментарий `engine.py:46-50` сам
фиксирует, что холодный старт может длиться **секунды** на машинах с антивирусом, а
таймаут 5 с (`:443-470`) покрывает только **чтение**. Последствие: `shutdown()`
блокируется навсегда на `_dom_lock` (`:361`), `task_ended` не эмитится
(`parser_manager.py:365-386`) — операция неубиваема пользователем.

**Готовое решение:**
```python
def _send(self, proc, payload: str, killer):
    """Одна JSON-строка, с ограничением: не читающий ребёнок не должен вешать вызывающего."""
    err = []
    def _w():
        try:
            proc.stdin.write(payload + "\n"); proc.stdin.flush()
        except Exception as e:          # BrokenPipe → вызывающий пересоздаст воркер
            err.append(e)
    t = threading.Thread(target=_w, daemon=True); t.start(); t.join(self._timeout)
    if t.is_alive() or err:
        killer()
        raise OSError("Deno worker stdin write blocked" if t.is_alive() else err[0])
```
Заменить три блока записи на `self._send(proc, request, self._kill_proc)` /
`self._kill_dom_proc` / `self._kill_gateway_proc`.

---

## A-16 · HIGH · Блокирующий API движка вызывается на потоке event-loop — «конкурентность» и бюджет 5 с фиктивны

**Где:** `src/parser/js_engine/engine.py:443-470` (синхронный `t.join(timeout)` +
`threading.Lock` на всё вызов), вызывающие — `src/parser/site_pattern_manager.py:918`,
`:1060`, `:1124`.

**Механизм [чит.]:** `run_js` / `run_dom_js` синхронны и держат `threading.Lock` весь
вызов (до 5 с + до 3 с terminate/escalate). Все три точки вызова — на единственном
event-loop: `webpage_parser.py:765` внутри `async def _get_best_image_url` ←
`async def _extract_images` (`:820`) ← `parse()` (`:1439`), и `parser_manager.py:655`
внутри `async def probe()`, защищённый только
`asyncio.Semaphore(K.FULLSIZE_DISCOVER_CONCURRENCY)` (`:557`). Семафор сериализует
одинаковую работу **и** замораживает loop, на котором живут монитор завершения
(`parser_manager.py:388`) и stop-событие. **Триггер:** одно правило, которое вешается,
× галерея из 300 разных thumbnail'ов — ключ кэша включает `groups`
(`engine.py:399`), значит 300 промахов × (5 с таймаут + ~300 мс холодный респаун) ≈
25 минут, в течение которых loop не обрабатывает Stop, сигналы прогресса и закачки.
Путь gateway делает это **правильно** (`run_in_executor`, `webpage_parser.py:667`).

**Готовое решение** — вынести оба блокирующих вызова в executor, ровно как уже сделано
для gateway:
```python
# parser_manager.py, внутри probe():
transformed = await loop.run_in_executor(None,
    lambda: self.pattern_manager.apply_link_url_transform(rule, match, page_url))
...
res_urls = [u for u in await loop.run_in_executor(None,
    lambda: self.pattern_manager.extract_res_urls(rule, html, page_url=page_url,
                                                  groups=link_groups, href=link_url))
            if is_format_allowed(u, "image", self.settings)]
```

---

# BLOCK B — MEDIUM (parser / downloader / js_engine)

## B-01 · MEDIUM · `pickle.dumps` runs in a thread while the loop mutates `stats` / `domain_health`

**Где:** `src/parser/parser_manager.py:1249-1251, 1257-1258`

`state` хранит **живые ссылки** `self.stats` и `self.domain_health`; все остальные поля
защитно копируются через `list()`. `pickle.dumps` уходит в executor, пока поток loop
продолжает `self.stats["images_found"] += 1` (959/960),
`self.domain_health[domain] = {...}` (991), `domain_state["failures"] += 1` (1059).
Вставка в dict, по которому идёт C-пиклер, даёт
`RuntimeError: dictionary changed size during iteration`. `try` на 1265 защищает только
запись файла, поэтому исключение уходит в `future.result()`, проглатывается GUI-овским
`except Exception`, и задача встаёт на паузу со **старым/отсутствующим** файлом состояния.

```python
# parser_manager.py:1249-1251 — снапшот, как уже делают все остальные поля
"stats": dict(self.stats),
"domain_health": {d: dict(v) for d, v in self.domain_health.items()},
```

## B-02 · MEDIUM · Закачки и HTML-парсинг делят *дефолтный* ThreadPoolExecutor

**Где:** `parser_manager.py:1012` + `webpage_parser.py:1436, 306, 384, 527`

Каждый блокирующий вызов идёт в `loop.run_in_executor(None, ...)`: `MediaDownloader.download()`
(1012, длится всю закачку), `BeautifulSoup(...)` (1436), синхронный фолбэк `_sync_fetch`
(306, до 10 с), `_try_escalate_fetch` (384), `_execute_bypass` (527). Дефолтный пул —
`min(32, cpu_count+4)` = 8 на 4-ядерной машине, при
`DEFAULT_DOWNLOADER_THREADS=8` + `DEFAULT_PARSER_THREADS=4` = 12 параллельных задач.
Один медленный синхронный вызов или большая закачка голодает за счет несвязанных
страниц, обнучая настроенные счётчики потоков и per-domain семафор
(`DOMAIN_CONCURRENCY_LIMIT = 2` подразумевает не mehr 2 операции на домен, но глобальный
пул сериализует приложение целиком).

```python
# _main_task (parser_manager.py:333)
self._dl_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=max(1, downloader_count), thread_name_prefix="media_dl")
# строка 1012 — отдельный пул для закачек
result = await self.loop.run_in_executor(self._dl_executor,
                                        lambda: downloader.download(timeout=timeout_val, retries=retries_val))
# _main_task finally (после строки 371)
self._dl_executor.shutdown(wait=False)
```

## B-03 · MEDIUM · Определение кодировки семплирует 2 КБ, игнорирует `<meta charset>`, а `errors="replace"` маскирует провалы

**Где:** `src/parser/webpage_parser.py:407-412, 324-336`

`_detect_encoding` вызывает `chardet.detect(content_bytes[:2048])` и игнорирует явное
объявление в HTML; декод затем всегда использует `errors="replace"`, поэтому неверная
догадка never не поднимает исключение, а блоки `except UnicodeDecodeError` (328-336)
недостижимы. **Отказный вход:** `windows-1251`-страница, чьи первые 2 КБ — только
разметка/JS (очень часто на русских сайтах — основной аудитории приложения) → chardet
вернёт `ascii`/`windows-1252` → все кириллические href декодируются в кракозябры →
молча неверные URL медиа и 404 при закачке.

```python
def _detect_encoding(self, content_bytes: bytes) -> str:
    if content_bytes.startswith(b"\xef\xbb\xbf"): return "utf-8-sig"
    elif content_bytes.startswith(b"\xff\xfe") or content_bytes.startswith(b"\xfe\xff"): return "utf-16"
    m = re.search(rb'charset\s*=\s*["\']?\s*([A-Za-z0-9_.:\-]+)', content_bytes[:4096], re.I)
    if m:                                   # явное объявление важнее догадок
        try: return m.group(1).decode("ascii")
        except Exception: pass
    detected = chardet.detect(content_bytes[:16384])   # больше — надёжнее
    return detected["encoding"] or "utf-8"
```

## B-04 · MEDIUM · `requests.Session` течёт на каждом раннем возврате из `parse()`

**Где:** `src/parser/webpage_parser.py:1428-1432` vs 1513-1516, 1520-1525

Синхронная сессия создаётся в `_get_sync_session` (300), но закрывается только на
успешном пути (1513-1516) и в outer `except` (1520-1525). Ранние возвраты на 1429
(`error_status`) и 1432 (`not content`) оставляют её открытой. Триггер: любая страница,
падающая с `PARSER_HTTP_ERROR_4XX/5XX` через фолбэк (309) или не декодируемая (336),
течёт `requests.Session` + пул urllib3.

```python
def _close_sync_session(self) -> None:
    if self._sync_session:
        try: self._sync_session.close()
        except Exception: pass
        self._sync_session = None
# parser lines 1429 и 1432 — перед каждым `return ...`:
self._close_sync_session()
```

## B-05 · MEDIUM · `<img>` внутри `<picture>` извлекается дважды

**Где:** `src/parser/webpage_parser.py:806-818` + `842`

`_extract_picture_sources` добавляет лучший URL внутреннего `<img>` (814-817);
последующий цикл `soup.find_all("img")` (842) — обход всего документа, который
переобрабатывает тот же `<img>` (BS4 находит потомков и не пропускает детей
`<picture>`). Каждый `<picture><source><img></picture>` даёт дубликат
`("image", url, attrs)`, раздувая `update_domain_score` через `len(media_files)`
(parser_manager 209-211); только дедуп `downloaded_files` на 929-930 скрывает это от
пользователя.

```python
picture_imgs = {id(i) for p in soup.find_all("picture") for i in p.find_all("img")}
for img in soup.find_all("img"):
    if id(img) in picture_imgs: continue   # уже выдано проходом <picture>
```

## B-06 · MEDIUM · Универсальный обход атрибутов превращает не-URL значения в закачки медиа

**Где:** `src/parser/webpage_parser.py:719-722`

Всеобъемлющий обход принимает *любой* строковый атрибут, чьё значение merely содержит
расширение изображения, и регистрирует его как кандидата URL с `score = 0`. Когда у
элемента нет настоящего `src`/`data-*` (`<img alt="cover.jpg" title="x">`), этот
мусорный кандидат остаётся единственным, поэтому `filtered_candidates[0]` — это
`"cover.jpg"` → `urljoin(self.url, "cover.jpg")` → в `media_files` попадает фиктивный
URL. `<img alt="sunset.jpg">` ставит в очередь `https://site/sunset.jpg`.

```python
for attr_name, value in element.attrs.items():
    if (isinstance(value, str)
            and re.match(r'^(?:https?:)?//|^[/.]', value)          # должно выглядеть как URL
            and re.search(r"\.(jpg|jpeg|png|webp|gif|avif|tiff|bmp)(?:$|[?#])", value.lower())):
```

## B-07 · MEDIUM · Пустое тело 200 вызывает дубль-запрос и reporting как сетевую ошибку

**Где:** `src/parser/webpage_parser.py:270-271, 283, 321-322`

`content_bytes = await response.read(); break` может дать `b""`. Тогда
`if not content_bytes and not escalation_tried:` (283) истинно, поэтому **синхронный
фолбэк на requests перекачивает тот же URL**; если и он пуст, строка 322 возвращает
`PARSER_NETWORK_ERROR, "Failed to retrieve content bytes after all attempts"`. Два
лишних запроса на страницу с пустым телом (204, пустые заглушки HTML) и mislabelled
провал — успешный HTTP 200 считается сетевой ошибкой и питает счётчики
`domain_health`/карантина (parser_manager 1056).

```python
content_bytes = await response.read()
empty_body = not content_bytes      # 200 с пустым телом — НЕ провал загрузки
break # Success with aiohttp
...
if not content_bytes and not empty_body and not escalation_tried:   # строка 283
```

## B-08 · HIGH · Выход за корень закачки: `..` в переданном `filepath` пишется дословно

**Где:** `src/downloader/media_downloader.py:382, 504-511, 514`; путь строится в
`parser_manager.py:944-953`

`_do_download` принимает `self.filepath` и делает `os.makedirs(os.path.dirname(...),
exist_ok=True)` + `open(temp_path, "wb")` без проверки принадлежности корню. `urlparse`
не нормализует точечные сегменты, а `parser_manager.py:946-948` вырезает `<>:"/\|?*`, но
не `..`. Проверено: `source_url "https://x.com/../../.."` → `"D:\dl\x.com\..\.."` →
`os.path.normpath == "D:\photo.jpg"`, при этом `os.path.dirname(...)` уже существует,
поэтому `makedirs(..., exist_ok=True) молча проходит`. Краулинг страницы с `..` в URL
пишет файлы на уровень выше выбранной папки закачки.

```python
# media_downloader.py:367 — первая инструкция внутри лока _ensure_unique_filepath_at_destination
norm = os.path.normpath(current_filepath)
if any(part == ".." for part in norm.split(os.sep)):
    raise ValueError(f"Path traversal rejected: {current_filepath}")
candidate = current_filepath
```

## B-09 · MEDIUM · Настройка `verify_tls` мертва на всех путях запроса закачки

**Где:** `media_downloader.py:59-76, 78-113, 179-212, 409, 462, 668` vs `244`

Только *эскалационный* GET читает настройку (строка 244, `legacy_default=False`).
`create_shared_downloader_session` (обе ветки), `_create_session` и все
`session.get/head` никогда не выставляют `session.verify` и не передают `verify=`,
поэтому и `requests.Session`, и `curl_cffi.Session` используют жёсткий дефолт
`verify=True`. При этом `webpage_parser.py:303,380,522,524` передают
`tls_verify(settings)` с `legacy_default=True` → `DEFAULT_VERIFY_TLS = False`. Итог:
одна и та же настройка даёт проверку ВКЛ для закачек и ВЫКЛ для загрузки страниц.
Пользователь за MITM-прокси, отключивший проверку TLS (**дефолт приложения**), всё
равно получает ошибки сертификата на каждой закачке медиа, хотя парсинг работает.

```python
# в конец create_shared_downloader_session и _create_session, перед return
session.verify = http_engine.tls_verify(settings, legacy_default=False)
```
`session.verify`honoured и `requests.Session` (мержится в `Session.request`), и curl_cffi
(`verify_list=[self.verify, verify]`) — одна строка, оба движка.

## B-10 · MEDIUM · Многопоточная сборка пишет финальный файл неатомарно

**Где:** `src/downloader/media_downloader.py:626-639`

`_download_with_threads` склеивает части **напрямую в `self.filepath`**
(`with open(self.filepath, "wb")`) вместо `.partial` с переименованием, как делает
однопоточный путь (514 + 567 `os.replace`). Сбой или прерывание оставляет
урезанный, но правдоподобно названный файл на видном конечном пути; при перезапуске
`_ensure_unique_filepath_at_destination` (367-374) видит, что он существует, и выбирает
`name_1.jpg`, поэтому урезанный файл never не детектится и не заменяется.

```python
# media_downloader.py:626
final_path = self.filepath
asm_path = f"{final_path}.mt.partial"
with open(asm_path, "wb") as outfile:
    for temp_file in temp_files:
        ...
# строка 634, после with-блока
if os.path.getsize(asm_path) != total_size:
    ...
os.replace(asm_path, final_path)
```

## B-11 · MEDIUM · Доверие серверному `Content-Type`: нет сниффинга содержимого, узкий блоклист

**Где:** `src/downloader/media_downloader.py:417, 490, 676-680`

Все три проверки типа блокируют only литеральные подстроки `text/html`,
`application/javascript`, `text/javascript`, `text/css`, `application/json`. Все
остальные типы проходят, включая `text/plain`, `application/octet-stream`,
`image/svg+xml` и **отсутствующий** `Content-Type` (тогда `get_content_type` — `""`, а
`any(t in "")` — False). Фотохост, отдающий свою логин/промежуточную страницу как
`text/plain; charset=utf-8` или вообще без `Content-Type`, получает этот HTML на диск под
именем `.jpg` и отчёт об успехе. Нет и content-based определения расширения:
`is_format_allowed` (`utils.py:84-123`) фильтрует only по расширению **URL**.

```python
# после проверки Content-Type в GET (строка 495) — один раз сниффим первые байты
head_bytes = b""
for _b in response_get.iter_content(chunk_size=32):
    head_bytes += _b; break
if head_bytes.lstrip()[:16].lower().startswith((b"<!doctype", b"<html", b"<?xml", b"{")):
    response_get.close()
    return {"success": False, "error": "Body is not media (sniffed text content)",
            "failure_kind": K.FAILURE_CONTENT_PAGE}
```
(Передайте `head_bytes` в цикл записи, чтобы не перекачивать.)

## B-12 · MEDIUM · Тело неизвестной длины принимается как полное без проверки целостности

**Где:** `src/downloader/media_downloader.py:497-501, 556-564`

Если HEAD ничего не ответил, а в GET нет `Content-Length` (chunked / close-delimited),
`content_length` остаётся `0`, поэтому единственная проверка целостности (557-564)
пропускается полностью. Поток, завершившийся раньше без исключения, тогда попадает под
`{"success": True}` и остаётся на диске. Существующие тесты этого не затрагивают:
`_HeadRefusingSession` (тест, 388-413) всегда ставит `Content-Length`.

```python
# строка 557, заменить условие
if content_length > 0 and not encoding:
    ...
elif content_length == 0:
    te = response_get.headers.get("Transfer-Encoding", "").lower()
    if "chunked" not in te:
        raise IOError("Truncated body: no Content-Length and no chunked framing")
```

## B-13 · MEDIUM · Backoff повтора спит не глядя на `stop_event`

**Где:** `src/downloader/media_downloader.py:350`

`download()` проверяет `stop_event` в начале каждой попытки (335) и внутри потока (522),
но backoff `time.sleep(0.5 * attempt)` выполняется безусловно. При `retry_count = 5`
кнопка Stop игнорируется до ~7.5 с суммарно — ровно та задержка, ради убирания которой
ввели `Retry(total=0)` (docstring модуля 56-57).

```python
# строка 350
deadline = time.time() + 0.5 * attempt
while time.time() < deadline:
    if self.stop_event and self.stop_event.is_set():
        return {"success": False, "error": "Download manually aborted"}
    time.sleep(0.1)
```

## B-14 · MEDIUM · Module-global флаги предупреждений гоняются из пула потоков закачки

**Где:** `src/parser/http_engine.py:41, 59-64, 74, 91-97, 157, 164-169`

`_missing_warned` и `_unverified_warned` — обычные модульные булевы, защищённые только
оператором `global`. `create_shared_downloader_session` / `_create_session` выполняются
внутри воркеров `ThreadPoolExecutor` (`parser_manager.py:1012-1014`), поэтому несколько
потоков закачки одновременно проходят проверку `if not _missing_warned` и каждый логирует
предупреждение о фолбэке.

```python
_warn_lock = threading.Lock()          # около строки 41
...
if not verify:
    with _warn_lock:
        if not _unverified_warned:
            _unverified_warned = True
            logger.warning("TLS verification is disabled ...")
```

## B-15 · MEDIUM · `_kill_worker` проглатывает любой отказ: выживший воркер невидимо течёт процессом и FD пайпов

**Где:** `src/parser/js_engine/engine.py:284-285` (голый `except Exception: pass`), при
`:288-289` (`_kill_proc` обнуляет `self._proc` безусловно)

Если `proc.terminate()` или `proc.kill()`/`wait()` бросает (Windows
`ERROR_ACCESS_DENIED` на уже закрытом хэндле; POSIX, если процесс уже переиспользован,
а `poll()` гонясь), outer `except: pass` это проглатывает и возвращается как будто ребёнок
мёртв — но `_kill_proc` уже поставил `self._proc = None`, поэтому никто и никогда его не
убьёт и не переиспользует. Ничего не логируется, пайпы не закрываются. Триггер: любой
путь таймаута (465), чей `terminate()` бросил → один утёкший `deno.exe` + 2 утёкших
хэндла пайпа **на** каждый зависший вызов; патологическое правило в долгом краулинге
течёт воркером каждые 5 с. Docstring на 262-266 обещает «fully reap (DL-9)».

```python
@staticmethod
def _kill_worker(proc):
    if proc is None: return
    try:
        if proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=2)
            except Exception:
                proc.kill()
                proc.wait(timeout=1)
        for s in (proc.stdin, proc.stdout):   # FD: сбежавший ребёнок никогда не увидит EOF
            try: s.close()
            except Exception: pass
    except Exception as e:
        logger.debug(f"Deno worker reap failed: {e}")   # никогда молча
```

## B-16 · MEDIUM · Нет корреляции `id` в ответах, а гонка таймаута отбрасывает валидный ответ

**Где:** `src/parser/js_engine/engine.py:462-466`, `:416-421` / `:434-441`

(а) `t.join(timeout)` возвращается, пока поток-читатель ещё жив пару микросекунд после
того, как `readline()` вернулся — поток пишет строку в `result_holder` *после* join,
поэтому `is_alive()` — True, мы убиваем воркер и бросаем `TimeoutError`, хотя ответ
пришёл; следующий вызов платит полный холодный спавн `deno run` (~300 мс) ни за что.
(б) Запрос всегда несёт `"id": 0`, а `id` ответа never не сверяется, поэтому рассинхрон
потока (сегодня достижим только через A-08) не детектится, и неверный ответ
приписывается запросу.

```python
t.join(timeout=timeout)
if t.is_alive():
    t.join(0.05)                       # Grace: readline() мог только что вернуться
    if not result_holder[0]:
        (killer or self._kill_proc)()
        raise TimeoutError("Deno worker timed out")
    line = result_holder[0]
else:
    line = result_holder[0]
if not line:
    raise OSError("Deno worker closed stdout")
```
(сверка `id` — в `_call`/`_call_dom` сразу после `json.loads`)

## B-17 · MEDIUM · `Window` happy-dom создаётся на каждый запрос и никогда не закрывается → неограниченная память

**Где:** `src/parser/js_engine/dom_worker.js:204-231`, `gateway_worker.js:121-131`

Каждый запрос строит свежий `new Window({...})` и парсит в него весь HTML страницы; ничего
никогда не разбирается. Процесс воркера живёт всю задачу (ленивый спавн один раз,
`engine.py:236-258`), а `run_dom_js` зовётся на каждую связанную страницу на каждое
правило (`parser_manager.py:655`). Триггер: галерея из 500 thumbnail'ов → 500
удержанных `Window` + разобранных деревьев документов в одном процессе; RSS растёт до
конца задачи.

```js
const win = new Window({ url: pageUrl || "https://local.invalid/",
                         settings: { enableJavaScriptEvaluation: false } });
try {
  ... существующее тело ...
  return JSON.stringify({ id, result });
} catch (e) {
  return JSON.stringify({ id, error: String((e && e.message) || e) });
} finally {
  try { win.happyDOM.close(); } catch (_) {}   // освобождаем разобранный документ
}
```

## B-18 · MEDIUM · Нет предела на накопленную строку запроса ни в одном воркере (неограниченная `acc`)

**Где:** `src/parser/js_engine/worker.js:150`, `dom_worker.js:252`, `gateway_worker.js:196`

`acc += decoder.decode(...)` растёт до прихода `\n`, without максимума. Python шлёт *весь*
HTML страницы одной строкой (`engine.py:497-504`), поэтому страница в 50 МБ даёт
JS-строку в 50 МБ + копию `JSON.parse` + (для gateway) сериализованный `html_after`
обратно. Одна oversized-страница в цепочке `link->url->res` (`parser_manager.py:621-684`)
взрывает RSS воркера или роняет его по OOM; Python видит только мёртвого ребёнка и
открывается по fail-open.

```js
if (acc.length > (64 << 20)) Deno.exit(1);   //oversized-запрос → умираем; Python переспавнит
```
(`Deno.exit` здесь легален — это сам воркер, не код правила. `_call` тогда бросает
`OSError("Deno worker closed stdout")` → fail-open.)

## B-19 · MEDIUM · `stderr=DEVNULL` на всех трёх воркерах: обречённый воркер полностью нем

**Где:** `src/parser/js_engine/engine.py:247, 302, 338`

Весь stderr отбрасывается, а все пути отказа логируют только на `debug` (`:409, 492, 566,
579`). Триггер: bundled `deno_cache/npm` есть, но неполон (заполняется на сборке
`build_exe.py:136-155`, которая проверяет only существование *каталога*) → воркер
мгновенно падает на каждом вызове; `dom_available()` всё ещё возвращает True (проверяет
только `isdir`, `engine.py:192-200`), поэтому каждое `res`-правило вечно открывается с
нулем строк выше DEBUG в логе. То же для синтаксической ошибки в bundled `worker.js`.
Пользователь, включивший `js_engine=deno`, получает нулевую наблюдаемость.

```python
self._stderr_log = tempfile.TemporaryFile("w+")   # на инстанс движка
...
Popen(..., stderr=self._stderr_log, ...)           # вместо DEVNULL
...
def _log_stderr_tail(proc_hint):
    try:
        self._stderr_log.flush(); self._stderr_log.seek(0)
        tail = self._stderr_log.read()[-2000:]
        if tail.strip(): logger.warning(f"Deno worker stderr: {tail!r}")
    except Exception: pass
```
(вызывать из ветки отказа `_kill_worker` и на первом отказе `_call` на спавн)

---

# BLOCK C — MEDIUM (GUI / core / server)

## C-01 · MEDIUM · Конфликт порта extension-сервера проглатывается; пользователь не узнаёт

**Где:** `src/server/http_server.py:52-53` и `src/gui/main_window.py:1233-1252`

`await self._site.start()` бросает `OSError [WinError 10048]`, когда второй инстанс
приложения уже держит порт 19876. Исключение ловится в `main_window.py:1242` и только
пишется в `logger.error`; после чего поток выходит. `_start_extension_server` возвращается
как здоровый, поэтому окно выглядит полностью рабочим, а каждый клик расширения падает с
«Desktop app not reachable: Failed to fetch», без сообщения в статус-баре, без `MessageBox`,
без лога, который пользователь читает.

```python
# src/server/http_server.py — start()
async def start(self):
    self.start_error = None
    try:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        await self._site.start()
    except OSError as e:
        self.start_error = f"Extension API port {self.port}: {e}"
        logger.error(self.start_error)
        await self.stop()
```
```python
# src/gui/main_window.py — сразу после loop.run_until_complete(...start())
msg = getattr(self.extension_server, "start_error", None)
if msg:
    self.log_handler.error(msg)
    self.status_bar.showMessage(msg, 20000)
```

## C-02 · MEDIUM · Кнопка «Download latest» sieve блокирует GUI-поток до ~60 с

**Где:** `src/gui/settings_dialog.py:579-611`, `src/parser/site_pattern_manager.py:68`

`_on_sieve_download` — прямой слот `clicked`, который зовёт
`SitePatternManager.download_imagus_from_url(url)`, а тот делает синхронный
`session.get(url, timeout=timeout)` (68). При отказе URL `raw.githubusercontent.com`
обработчик повторяет через зеркало jsDelivr (`settings_dialog.py:602-606`), поэтому худший
случай — два последовательных блокирующих GET по 30 с. Окно перестаёт перерисовываться
(Windows помечает «Not responding»), метка «Downloading...» не появляется, отменить нельзя
— ровно тот класс дефекта, которого собственные комментарии codebase избегают
(`main_window.py:1129-1134`).

```python
# settings_dialog.py — добавить в класс
_sieve_done = Signal(bool, str)

def _on_sieve_download(self):
    ...
    self._sieve_done.connect(lambda ok, m: (
        self.sieve_status_label.setText(m), self.sieve_download_btn.setEnabled(True)))
    self.sieve_status_label.setText("Downloading...")
    self.sieve_download_btn.setEnabled(False)
    import threading
    def run():
        ok, msg = pm.download_imagus_from_url(url)   # та же логика, вне потока
        if not ok and url.startswith("https://raw.githubusercontent.com/"):
            m = pm.jsdelivr_mirror(url)
            if m: ok, msg = pm.download_imagus_from_url(m)
        self._sieve_done.emit(ok, msg)
    threading.Thread(target=run, daemon=True).start()
```
(`_sieve_done` — queued `Signal`, поэтому обновление метки идёт на GUI-потоке. Повтор
через зеркало должен переехать внутрь `run`.)

## C-03 · MEDIUM · Закачка sieve пишет в *ранее сохранённый* путь, игнорируя видимый

**Где:** `src/gui/settings_dialog.py:590` vs `:650-651`, `:902`

`_on_sieve_download` читает `sieve_path = self.settings.get(K.SETTING_IMAGUS_SIEVE_PATH, ...)`.
`self.settings` обновляется из виджетов только в `save_settings()` (`:902`).
`browse_imagus_file` (`:650-651`) пишет новый путь прямо в `self.imagus_sieve_edit`, не
трогая `self.settings`. Триггер: открыть настройки, выбрать файл sieve, который никогда не
сохраняли, нажать «Download latest» → либо отказ с «Configure a sieve file path first», хотя
путь явно показан в поле, либо атомарная перезапись *старого* файла sieve с уничтожением
не тех данных.

```python
sieve_path = self.imagus_sieve_edit.text().strip() or None
if not sieve_path:
    self.sieve_status_label.setText("Choose a sieve file first (Imagus Sieve File).")
    return
self.settings[K.SETTING_IMAGUS_SIEVE_PATH] = sieve_path  # держим view-model в синхроне
```

## C-04 · MEDIUM · Тело POST от расширения: `urls` валидируется, а `user_agent`/`cookies`/`one_shot` — нет

**Где:** `src/server/http_server.py:147-149` → `src/gui/main_window.py:55-59` →
`src/parser/shared_session.py:66-69, 124`

`urls` фильтруется до dict'ов с непустым `str` url (`:143-146`), но `one_shot`,
`user_agent`, `cookies` берутся из JSON-тела сырыми. `{"user_agent": 5}` попадает в
`settings["user_agent"] = 5` (main_window 56), а оттуда в dict заголовков на
`shared_session.py:66-69`, где aiohttp бросает `TypeError` при сборке запроса.
`{"cookies": {"a": "b"}}` доходит до `ext_cookies.split(";")` (`:124`) → `AttributeError`
(ловится, поэтому деградирует до предупреждения в логе). Заметьте: `sanitize_settings`
(`settings_dialog.py:929-1011`) *вырезает* CRLF из `user_agent`/`proxy`, но применяется
только при загрузке `settings.json`, никогда на этом пути инъекции, поэтому управляющие
символы для инъекции заголовков здесь тоже не блокируются.

```python
one_shot = body.get("one_shot", False) if isinstance(body.get("one_shot"), bool) else False
user_agent = body.get("user_agent", "") or ""
cookies = body.get("cookies", "") or ""
if not isinstance(user_agent, str): user_agent = ""
if not isinstance(cookies, str): cookies = ""
user_agent = user_agent.replace("\r", "").replace("\n", "")[:512]
```

## C-05 · MEDIUM · Тихий потолок тела 1 МБ: крупные легитимные батчи отбрасываются и репортятся как «Invalid JSON»

**Где:** `src/server/http_server.py:29, 136-138`; `extension/background.js:947-959`

`web.Application()` создаётся без `client_max_size`, поэтому применяется неявный дефолт
aiohttp в **1 МиБ**. `request.json()` (`:136`) бросает
`aiohttp.HTTPRequestEntityTooLarge`, когда тело больше; голый `except Exception` на `:137`
возвращает `{"error": "Invalid JSON"}` со статусом 400. Расширение шлёт весь массив
результатов одним нечанкированным POST (`background.js:949-959`) без ограничения размера.
Страница-галерея на 4000 картинок легко превышает 1 МиБ JSON: пользователь жмёт «send to
desktop», получает «Invalid JSON» и решает, что приложение сломано — при этом настоящая
причина невидима. И 413 мисклассифицируется как ошибка разбора 400.

```python
# http_server.py:29
self._app = web.Application(client_max_size=16 * 1024 * 1024)  # large gallery batches
```
```python
# http_server.py:135-138
try:
    body = await request.json()
except web.HTTPRequestEntityTooLarge:
    return web.json_response({"error": "Payload too large (max 16 MB)"},
                             status=413, headers=self._cors_headers(request))
except Exception:
    return web.json_response({"error": "Invalid JSON"}, status=400,
                             headers=self._cors_headers(request))
```

## C-06 · MEDIUM · Старый `QThread` бросается без `deleteLater()`; таймаут `wait()` превращает это в живой краш

**Где:** `src/gui/main_window.py:914-927` и `:943`

`_launch_parser_for_task` quit'ит прошлый поток, `wait(5000)`, логирует предупреждение при
таймауте, а затем безусловно переприсваивает `self.parser_thread = QThread()`. У старой
Python-обёртки `QThread` теперь ноль ссылок. Если `wait()` таймаутнул, поток ещё жив,
поэтому GC обёртки запускает деструктор `QThread` на живом потоке → *"QThread: Destroyed
while thread is still running"* и далее неопределённое поведение. Связки
`finished`→`deleteLater()` нет нигде в файле. Собственный комментарий кода («would
otherwise leak the QThread and risk 'QThread destroyed while running'») показывает
намерение, но охрана только *логирует*.

```python
old = self.parser_thread
self.parser_thread = None
if old is not None:
    old.finished.connect(old.deleteLater)   # даже если wait() таймаутнет
    if old.isRunning():
        old.quit()
        if not old.wait(5000):
            self.log_handler.warning("Old parser thread did not stop in 5s")
```

## C-07 · MEDIUM · Pause / pause-for-switch блокирует GUI-поток до 15 с

**Где:** `src/gui/main_window.py:898-901` и `:1023-1026`; `src/parser/parser_manager.py:1217`

И `_pause_current_for_switch`, и `toggle_pause` делают
`asyncio.run_coroutine_threadsafe(pm.save_state(...), pm.loop)` с последующим
`future.result(timeout=15)` **на GUI-потоке**. Сам `save_state` намеренно ждёт до 5 с
незавершённых закачек (1217-1221) перед пиклингом. Клик по Pause во время долгой закачки
замораживает окно на секунды без смены курсора и без отмены.

```python
async def _save_then_stop(pm, path):
    try:
        await pm.save_state(path)
    finally:
        pm.stop_parsing()
asyncio.run_coroutine_threadsafe(_save_then_stop(pm, active.download_path), pm.loop)
```
(`stop_parsing` уже спроектирован для межпоточного вызова — он только планирует
`_stop_event.set` через `call_soon_threadsafe`, `parser_manager.py:1124-1136`.)

## C-08 · MEDIUM · Поток HTTP-сервера читает и меняет GUI-овское состояние без синхронизации

**Где:** `src/gui/main_window.py:1198-1229`, `:1214`, `:1204-1206`

`set_callbacks` ставит две обычные функции, которые aiohttp зовёт на потоке event-loop
сервера. `get_status()` читает `self.task_queue.active_task` (итерируя
`TaskQueueManager._queue`, `task_queue_manager.py:47-54`) и `len(self.task_queue.queue)`
(`:59`, `list(self._queue)` — полная копия), пока GUI-поток может быть внутри
`add_task`/`remove_task`/`_move_task` (`insert`/`pop` по тому же списку).
`add_tasks_from_extension` также зовёт `self.settings_dialog.get_settings()` и **добавляет
в обычный список** `self._pending_extension_payloads` (`:1214`). Собственный docstring
`TaskQueueManager` говорит «must be called from the GUI thread» (`:27-29`); путь чтения это
нарушает. CPython делает `list.append` атомарным, поэтому исключения нет — режим отказа
это *молча неверный* ответ, и показание статуса из расширения расходится с UI.

```python
# TaskQueueManager.__init__
self._lock = threading.Lock()
# snapshot() — используется HTTP-callback'ом
def snapshot(self):
    with self._lock:
        a = self._active_id
        t = next((x for x in self._queue if x.id == a), None) if a else None
        return t.id if t else None, len(self._queue), dict(t.stats) if t else {}
```
```python
# main_window.py — защитить и список ожидания
with self._payload_lock:
    self._pending_extension_payloads.append(payload)
```

## C-09 · MEDIUM · Очередь пишется только при чистом закрытии окна

**Где:** `src/gui/main_window.py:1269` (единственный вызов `TaskQueueManager.save`)

`grep` подтверждает: `task_queue.save(` встречается во всём репо ровно один раз. Задачи,
добавленные через UI, CSV-импорт или расширение, живут только в памяти, пока пользователь
не закроет окно нормально. Любой краш (класс отказа C-06), `TaskKill` или Windows
«End task» теряют всю очередь, включая уже скачанные файлы в их пер-тасковых папках —
осиротевшие папки больше не привязать к задачам. У `load()` аккуратная обработка
повреждения на каждую запись, так что он явно *задуман* как хранилище состояния; его
просто никто никогда не пишет.

```python
# main_window.py — _load_queue_state() ставит self._queue_dirty = False
# затем в путях add/remove/move: self._queue_dirty = True
# и в _update_active_task_stats():
if getattr(self, "_queue_dirty", False):
    self._queue_dirty = False
    try: self.task_queue.save(queue_path())
    except Exception as e: self.log_handler.error(f"Queue autosave failed: {e}")
```

---

# BLOCK D — MEDIUM (extension JS)

## D-01 · MEDIUM · Сканирование по клавиатурной команде идёт `discoverFullsize` без keepalive

**Где:** `extension/background.js:1027-1034` (зовёт `discoverFullsize` на 1030) vs `:670` и
`:825`

`discoverFullsize` может легально идти до 45 с (`deadline = Date.now() + 45000`, строка
447) плюс 15 с дренажа карантина, с долгими окнами тишины между fetch'ами. MV3
гасит SW простаивая после ~30 с без события. Путь скана из попапа берёт keepalive
(`:670`), путь закачки берёт его (`:825`) — **путь команды не берёт ни одного**. Триггер:
`Ctrl+Shift+S` на странице с медленными ссылками → SW подвешен посреди скана,
`commandScanAndProcess` убит, а бейдж остаётся на «...» с несброшенным `commandBusy`
(охрана `:994` мертва в перезапущенном воркере, частичные результаты скана потеряны).
Тот же пробел у `runSieveUpdate` (`:72-91`): до 2 URL × 3 повтора с 10 с таймаутами и
backoff-снами.

```js
// background.js, commandScanAndProcess
  keepaliveAcquire();
  try {
    /* существующее тело (send-desktop / save-chrome) */
  } finally {
    keepaliveRelease();
    commandBusy = false;
  }
```

## D-02 · MEDIUM · Финальная запись результата скана падает молча → попап навсегда застрял на «Scanning...»

**Где:** `extension/background.js:722-727`

`scanResult` + `scanProgress:{status:"done"}` пишутся в **одном** вызове, и единственный
обработчик отказа — `{}`. При любом отказе (квота `chrome.storage.local` — `scanResult`
держит полный массив медиа большой страницы — или убитый SW посреди записи) событие
`scanResult`/`done` не эмитится никогда: `chrome.storage.onChanged` не срабатывает,
поэтому `popup.js:171-206` никогда не узнаёт, что скан кончился. Собственный
`await sendMessage({action:"startScan"})` попапа возвращает `{ok:true,...}` (728), поэтому
путь ошибки попапа (popup.js:156-160) пропускается. Итог: кнопка скана остаётся
выключенной и читает «Scanning...», а `restoreScanState` (popup.js:299-305) снова
показывает «Scanning...» навсегда — лечится только перезагрузкой страницы.

```js
} catch (e) {
  console.error("scanResult not stored:", e);
  await chrome.storage.local.set({
    scanProgress: { status: "error", phase: "error",
                    error: "Result could not be stored: " + e.message, tabId },
  }).catch(() => {});
  return { ok: false, error: "Result too large to store (" + media.length + " items)" };
}
```
(popup.js:183-187 уже сбрасывает кнопку на `status === "error"`.)

## D-03 · MEDIUM · Расширение щупает *каждый* анкор; desktop ограничивает немедийные ссылки 200 (паритет)

**Где:** `extension/content_script.js:168`
(`return { media, links: links.concat(linkRest) }`); двойник: `src/constants.py:75`,
`src/parser/parser_manager.py:779-790`

`linkRest` держит анкоры, которые **не** показывают медиа (chrome сайта: Front Page /
Popular / теги / пагинация / логин). Их конкатенатят в тот же список обнаружения и шлют
через `sendMessage`; `discoverFullsize` щупает их по порядку без ограниче��ия. Триггер:
menu-heavy страница (форум-хаб, индекс e-hentai) с 500-1000 навигационными ссылками — 20
настоящих thumbnail-ссылок стоят за всеми ними, и бюджет 45 с (background.js:447) уходит
на навигационные страницы. Desktop ограничивает ровно этот класс ссылок и явно **не**
ограничивает медийные (`parser_manager.py:773-790`:
`excavation = [u for u in ... if not from_image]; excavation.sort(priority);
excavation[:max_links]` с `K.DEFAULT_MAX_LINKS_PER_PAGE = 200`), при этом ссылки
`from_image` «никогда не ограничиваются по содержимому». В расширении `links` ≈
`from_image: True`, а `linkRest` ≈ `from_image: False`, так что ограничение потерялось и
там, и там.

```js
const MAX_NAV_LINKS = 200;   // двойник на desktop: DEFAULT_MAX_LINKS_PER_PAGE (constants.py:75)
return { media, links: links.concat(linkRest.slice(0, MAX_NAV_LINKS)) };
```
(`discover_queue.test.mjs:34` закрепляет неограниченное поведение, поэтому его тоже надо
обновить.)

## D-04 · MEDIUM · Вся cookie-банка браузера POST'ится по открытому HTTP (безопасность)

**Где:** `extension/background.js:929-945` (`getPageContext`), `:934`; `:947-965`
(`sendToDesktop`)

`chrome.cookies.getAll({ url: tab.url })` возвращает **каждую** cookie URL, включая
`HttpOnly` и сессионные cookie несвязанных путей на этом домене, без allowlist'а имён, без
сужения области и без opt-out. Результат склеивается в одну строку заголовка (936) и
POST'ится на `http://127.0.0.1:19876/api/tasks`. Локальный сервер биндится только на
loopback (`http_server.py:52`) и отвергает кросс-ориджин браузерные POST'ы
(`_is_trusted_origin`, `:64-80`), поэтому экспозиция локальна: любой процесс/пользователь
на машине, кто может наблюдать loopback-трафик или занять порт 19876 первым, получает
аутентифицированные cookie жертвы. `getPageContext` зовётся на **каждом** «Send to App».

```js
if (cookies.length > 0) {
  context.cookies = cookies
    .filter((c) => !c.httpOnly && c.path === "/")   // сужаем область
    .map((c) => `${c.name}=${c.value}`).join("; ");
}
```

## D-05 · MEDIUM · Стрип WordPress-размерного суффикса (`-300x200.jpg → .jpg`) есть только на desktop (паритет)

**Где:** `extension/background.js:615-660` (`upgradeMediaList`); двойник:
`site_pattern_manager.py:981-991`

`upgradeMediaList` выполняет ровно два шага апгрейда: правила sieve, затем нативный порт
`[Resize]` (`applyResizeUpgrade`, background.js:235-250). У desktop'ового
`transform_image_url` есть **шаг 4** после стрипа resize:
`re.sub(r"(-\d{2,4}x\d{2,4})(\.(?:jpe?g|png|webp|avif))(?=$|\?)", r"\2", ...)`. Триггер:
WordPress- thumbnail галереи `https://wp.example/wp-content/uploads/2024/01/photo-300x200.jpg`
— desktop скачивает `photo.jpg`, расширение скачивает thumbnail 300x200. Ни один тест
расширения это не покрывает.

```js
// step 4 (двойник на desktop: site_pattern_manager.py:981-991)
const WP_SIZE_RE = /(-\d{2,4}x\d{2,4})(\.(?:jpe?g|png|webp|avif))(?=$|\?)/i;
const wp = item.url.replace(WP_SIZE_RE, "$2");
if (wp !== item.url) { out.push({ ...item, url: wp, transformed: true, source: "sieve-to" }); }
else out.push(item);
```

## D-06 · MEDIUM · `fetchSieve` не имеет предела размера ответа до буферизации/парсинга

**Где:** `extension/sieve_updater.js:130-145`

`await resp.text()` буферизует *всё* тело, затем `JSON.parse` строит граф объектов, затем
`mergeSieve` + `JSON.stringify` (187) аллоцирует третью копию — всё это до любой проверки
валидности (`validRuleCount`, 181). Единственные границы — 10 с таймаут fetch'а и
 eventually ошибка квоты `chrome.storage.local` (поднимается только после 3 повторов ×
2 URL). Сегодня источник — фиксированная HTTPS-константа `SIEVE_REPO_URL` (23), а
поставляемый файл 884 КБ, поэтому это латентно — но `updateSieve` читает URL из storage
(`sieveRepository`, background.js:78/84; никакой UI его не пишет), поэтому скомпрометированный
апстрим или ручная правка storage может заслать в SW файл произвольного размера.

```js
const MAX_SIEVE_BYTES = 8 * 1024 * 1024;
const text = await resp.text();
if (text.length > MAX_SIEVE_BYTES) throw new Error("Sieve too large (" + text.length + " bytes)");
const data = JSON.parse(text);
```

## D-07 · MEDIUM · `filename` закачки — сырой basename URL без санитизации

**Где:** `extension/popup/popup.js:518` и `extension/background.js:1047`
(`item.url.split("/").pop().split("?")[0]`)

Поставляемый сервером basename идёт прямо в `chrome.downloads.download({filename})`
(`background.js:865-869`). URL, чей последний сегмент — `..%2F..%2Fx.png`,
зарезервированное Windows-имя (`CON.png`) или имя, оканчивающееся на `.`/пробел, даёт
`filename` с этими байтами; если Chrome его отвергает, элемент отбрасывается как отказ,
видимый только как `console.error` (871) и завышенный счётчик `failed:`
(`emitProgress` считает `completed - saved`, `:821` — элементы ещё в полёте считаются
отказавшими). Собственная валидация имён Chrome — единственная защита, поэтому поведение
расходится по платформам/сборкам.

```js
function safeFilename(url) {
  const raw = decodeURIComponent((url.split("/").pop() || "").split("?")[0]);
  const base = raw.replace(/[\\/:*?"<>|\x00-\x1f]/g, "_").replace(/^[. ]+|[. ]+$/g, "");
  return base || "media";
}
```

## D-08 · MEDIUM · Обнарурование молча деградирует в «ничего не найдено» на битом наборе правил

**Где:** `extension/background.js:121-157` (`loadSieveResPatterns`): пустые catch на 153 и 156

`JSON.parse(result.sieveRules)` (119) внутри `try`, чей обработчик — `{}`. Если
`sieveRules` битый (частично записанное значение, ручная правка storage, или объект вместо
строки, записанный загрузчиком попапа — popup.js:720 хранит сырой *текст* файла, поэтому
не-JSON-как-объект отвергается апстримом, но массив `[]` распарсится иитерируется в
пустоту), `cachedSieveRes` остаётся `{}`, и каждый зонд падает в слепой регекс `<img>`
(420-436). Пользователь видит «0 found» без диагностики; тот же молчаливый путь скрывает
один битый регекс правила (153).

```js
} catch (e) { console.warn("loadSieveResPatterns: sieve rules unusable, discovery falls back:", e.message); }
```
(и на строке 153: `catch (e) { console.debug("sieve rule skipped:", name, e.message); }`.)

## D-09 · MEDIUM · Обработчики сообщений могут оставить отправителя ждущим вечно

**Где:** `extension/background.js:1070-1113`

Каждая async-ветка — `promise.then(sendResponse)` **без `.catch`** (1072, 1076, 1080, 1091,
1095, 1105, 1110). `handleStartScan`, `discoverFullsize`, `chromeDownload`,
`sendToDesktop`, `getStatus` глотают свои ошибки внутренне, поэтому сегодня висят только
*непроглоченные*: `runSieveUpdate` → `chrome.storage.local.get` (78) /
`loadSieveResPatterns` (88, охраняется) вне внутреннего try `updater.updateSieve`. Триггер:
отказ storage, пока попап жмёт «Update now» → промис не settling'ится никогда →
`popup.js:738` оставляет кнопку на «Updating...» навсегда. Краш алярма A-13 попадает в тот
же приёмник через `chromeDownload`.

```js
const reply = (p) => p.then(sendResponse, (e) => sendResponse({ error: e.message }));
// например reply(updateSieve-safely), reply(chromeDownload(items, limit)), reply(handleStartScan(tabId))
```

## D-10 · MEDIUM · Асимметрия junk-токенов между гейтом медиа и гейтом краулинга

**Где:** `src/parser/junk_filter.py:150-166` vs `src/parser/utils.py:541-548` (и третья
таблица, `DEFAULT_LINK_SKIP_SEGMENTS` на `utils.py:615`)

Почти одинаковые матчеры токенов с двумя независимо поддерживаемыми таблицами ключевых
слов, поэтому один и тот же сегмент пути — мусор для одного решения и норма для другого:
`is_ad_url("https://x.example/impressions/1.jpg")` → True (медиа отброшено), тогда как
`should_skip_crawl_url("https://x.example/impressions/1")` → False (тот же сегмент
краулится). `is_banner_or_ad` (`utils.py:561-565`) запускает **оба** классификатора,
поэтому URL медиа получают объединение, а ссылки краулинга — ни один.

```python
 def _path_has_ad_token(url: str) -> bool:
-    combined = f"{urlparse(url).netloc}/{urlparse(url).path}".lower()
-    tokens = [t for t in re.split(r"[^\w]+", combined) if t]
-    for tok in tokens: ...
+    from src.parser.utils import _ad_tokens_from_url, _matches_ad_keyword
+    return any(_matches_ad_keyword(t) for t in _ad_tokens_from_url(url))
```

## D-11 · MEDIUM · Угадывание типа медиа расходится между JSON- и HTML-маршрутами обнаружения

**Где:** `src/parser/json_parser.py:280-307` vs `src/parser/webpage_parser.py:1609, 1656, 1671, 1678

Угадывание типа медиа существует как `json_parser._guess_media_type`` (полные таблицы
расширений) и как четыре инлайновых копии
`media_type = "video" if any(ext in abs_url for ext in [".mp4",".webm"]) else "image"` в
HTML-пути. Один и тот же URL получает разные типы по маршруту обнаружения, а тип выбирает
разрешающий список форматов — `json_parser.py:190` зовёт `is_format_allowed(abs_url,
"video", ...)`, тогда как HTML-маршрут зовёт `is_format_allowed(abs_url, "image", ...)`
для, скажем, `clip.mov` / `movie.mkv` / `song.mp3`. Итог: идентичное медиа
сохраняется или отбрасывается в зависимости от того, где его нашли.

```python
 # utils.py
 def guess_media_type(url: str) -> str: ...        # moved verbatim from json_parser._guess_media_type
 # webpage_parser.py x4
-media_type = "video" if any(ext in abs_url for ext in [".mp4",".webm"]) else "image"
+media_type = guess_media_type(abs_url)
```

## D-12 · MEDIUM · Хост в верхнем регистре не находит правил sieve вовсе

**Где:** `src/parser/site_pattern_manager.py:856-857` + `src/parser/utils.py:126-134`

`get_domain()` возвращает `netloc` дословно, тогда как ключи `imagus_rules` приводятся к
нижнему регистру `_extract_domain_from_regex` (`:656`), а `get_patterns_for_url` сам
приводит свой домен к нижнему регистру (`:699`). Поэтому хост в верхнем регистре не находит
правил sieve: `pm.transform_image_url("https://I.4CDN.ORG/g/123s.jpg", ...)` возвращает
только входной URL, тогда как форма в нижнем регистре даёт 5 вариантов. HTML href'ы вида
`<a href="HTTPS://CDN.Example.com/...">` легальны, и `urljoin` сохраняет регистр. Та же
асимметрия в `priority_url_queue._get_domain` (`:71-76`).

```python
 def get_domain(url):
     try:
-        return urlparse(url).netloc
+        return urlparse(url).netloc.lower()    # ключи/сравнения уже в нижнем регистре
```

---

# BLOCK E — MEDIUM (tests / build / deps / docs)

## E-01 · MEDIUM · `build_exe.py` полностью зависит от cwd и проглатывает чистые отказы

**Где:** `build_exe.py:26, 30-38, 51-53, 88-89`

Все пути относительные (`requirements.txt`, `resources/...`, `os.listdir(".")` на `:89`,
`dist/WebMediaParser` на `:88`). Запуск `python D:\...\build_exe.py` из любого другого cwd →
`pip install -r requirements.txt` ставит ничего, затем источники `--add-data` отсутствуют →
PyInstaller `SystemExit`. Отдельно `shutil.rmtree` на `:30-38` ловит `Exception` и только
**печатает**, поэтому при залоченном `dist/` (запущенный `WebMediaParser.exe`) сборка
продолжается в `-y` PyInstaller по **устаревшему** `dist/` — смешивая старые и новые
артефакты без жёсткого отказа.

```python
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)                       # build_exe.py:22 — все пути относительно корня
...
        except Exception as e:      # build_exe.py:37
            raise SystemExit(f"could not clean {d}/ ({e}): close the running app")
```

## E-02 · MEDIUM · Тест дедупа sieve ничего не утверждает о дедупе (и его комментарий устарел)

**Где:** `tests/test_pattern_manager.py:99-110`

`pm = SitePatternManager()` сканирует корень проекта, находит **ровно один** sieve
(`Imagus_sieve_2026.07.15_823.json` = 823 верхнеуровневых ключа — проверено), и
`assert total < 1000` проходит тривиально: дублирующихся имён правил не существует, поэтому
путь «newest file wins» не exercising'ится вовсе. Хуже: тест привязан к
ambient-состоянию разработчика — добавление легитимного второго `Imagus_sieve_*.json` в
корень проекта (документированный способ ставить новые правила) делает `total` ~1672 и
красит сьют.

```python
def test_sieve_dedup_newest_file_wins(tmp_path, monkeypatch):
    import json
    (tmp_path / "Imagus_sieve_2026.01.01_000.json").write_text(
        json.dumps({"Same_Name": {"link": "^https?://a[.]example/", "to": "img"}}), encoding="utf-8")
    (tmp_path / "Imagus_sieve_2026.07.15_823.json").write_text(
        json.dumps({"Same_Name": {"link": "^https?://b[.]example/", "to": "img"}}), encoding="utf-8")
    pm = SitePatternManager(enable_built_in=False)
    assert sum(len(r) for r in pm.imagus_rules.values()) == 1   # дедуплировано, не 2
    assert pm.get_link_rule("https://b.example/pic") is not None
```

## E-03 · MEDIUM · Пиннинг снапшота sieve падает невнятно, а один файл скипается молча

**Где:** `tests/helpers.py:12-15`; `src/parser/site_pattern_manager.py:149`;
`tests/test_js_engine.py:212-213`

`SIEVE_PATH` никогда не валидируется, а `SitePatternManager.__init__` охраняет sieve через
`if self.imagus_sieve_path and os.path.exists(...)` — то есть отсутствующий/переименованный
снапшот *молча* пропускается. Проверено: с фиктивным путём `imagus_global_rules == 0`, а
`get_link_rule(...)` возвращает `None`, поэтому `test_fullsize_discovery.py:44` умирает с
`TypeError: 'NoneType' object is not subscriptable` вместо «prerequisite missing», тогда как
`test_js_engine.py:212` просто делает `pytest.skip`.

```python
if not os.path.exists(SIEVE_PATH):   # падаем на коллекции, а не через 6 утверждений
    raise FileNotFoundError(
        f"Imagus sieve snapshot missing: {SIEVE_PATH} - see tests/helpers.py (TST-5)")
```

## E-04 · MEDIUM · Тест, написанный чтобы проходить и так, и так (JS-охрана `data:` не exercising'ится)

**Где:** `tests/test_fullsize_discovery.py:57-66` vs `src/parser/site_pattern_manager.py:1061, 1069-1070`

`TestSieveLinkChain.setUpClass` (`:41`) строит `SitePatternManager(..., imagus_sieve_path=SIEVE)`
**без `js_engine`** → `js_engine=None`, поэтому `apply_link_url_transform` returns'ится на
`:1061` *до* охран C-4 `data:` на `:1069-1070`. Утверждение
`self.assertTrue(result is None or not result[0].startswith("data:"))` (и его собственный
комментарий «assert the contract holds either way») гарантирует проход в обоих мирах.
Единственный тест, который *действительно* доходит до JS-ветки — `test_js_engine.py:430` —
Deno-скип. Охрана JS `data:` имеет **ноль эффективного покрытия** на этой машине.

```python
# tests/test_fullsize_discovery.py — заменить дизъюнктивное утверждение:
        self.assertIsNone(result)   # js_engine is None here: JS branch not reachable
# ...и покрыть охрану моком движка (Deno не нужен):
        eng = MagicMock(); eng.run_js.return_value = "data:,$1"
        pm  = SitePatternManager(enable_built_in=False, js_engine=eng)
        self.assertIsNone(pm.apply_link_url_transform(rule, m))   # js_engine.py:1069
```

## E-05 · MEDIUM · Критичные модули с нулём тестов (включая пути, горячие для упаковки)

**Где:** `src/app_paths.py:14-64`, `src/gui/main_window.py` (1203 строки),
`src/gui/settings_dialog.py` (947), `src/gui/log_handler.py` (135), `src/fix_lxml.py`,
`src/server/http_server.py:22-136`

Полная карта import→coverage показывает, что **ни один тестовый файл не импортирует**
`src.fix_lxml`, `src.gui.log_handler`, `src.parser.pattern_manager`; `src.gui.main_window`
затрагивается только ради двух чистых хелперов на `test_parser_lifecycle.py:30`;
`settings_dialog` — только ради статического `sanitize_settings`; `http_server` — только
ради `_handle_add_tasks`; `app_paths` — только ради `clear_task_sessions`/`task_state_path`.
Функции, решающие, найдёт ли *замороженное* приложение свои ресурсы — `get_app_dir()`,
`get_resource_dir()`, `resource_path()`, `settings_path()`, `queue_path()`, `sessions_dir()`,
`default_download_dir()` — не имеют тестов вовсе, хотя это ровно та логика, которая молча
ломается от сборки к сборке.

```python
# tests/test_app_paths.py — freeze-образные фикстуры для слоя путей
def test_resource_dir_prefers_meipass_when_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert get_resource_dir() == str(tmp_path / "resources")
def test_settings_and_queue_next_to_exe(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app.exe"), raising=False)
    assert settings_path() == str(tmp_path / "settings.json")
    assert queue_path() == str(tmp_path / "task_queue.json")
```

## E-06 · MEDIUM · Дрейф доков: устаревшие счётчики, удалённый файл и переименованный файл состояния

| doc:line | утверждение | реальность |
|---|---|---|
| `knowledge.md:9` | «265 passed / 30 skipped» | **319** passed (проверено) |
| `knowledge.md:23`, `CONTEXT.md:133`, `README.md:63`, `README_EN.md:62` | состояние возобновления `sessions/{task_id}/state.pkl` | `state.pkl` имеет **0 ссылок в `src/`**; это `app_paths.task_state_path()` → `{download}/sessions/{task_id}/last_session.pkl` (проверено grep'ом; уже исправлено в освежённом `AGENTS.md`) |
| `knowledge.md:38` | «`setup.py` install_requires **устарел** — верьте requirements.txt» | они **идентичны** (13 записей, тот же порядок/пины, проверено через AST); строка ещё и ссылается на `QWEN.md`, который был **удалён** в коммите `51c78c3` |
| `CONTEXT.md:74` | «tests/ — 15 файлов pytest» | 21 модуль в `tests/` |
| `CONTEXT.md:214` | «180 passed, 40 skipped (2026-08-16)» | 319 / 30 |
| `CONTEXT.md:217` | «9 — curl_cffi не установлен в venv» | `curl_cffi 0.16.0` **установлен**; все 30 скипов — Deno (проверено) |
| `GATES.md:3`, `PLAN.md:9` | «Baseline ... 265 passed / 30 skipped» | 319 / 30 |

```diff
- python -m pytest tests -q    # 180 passed, 40 skipped (2026-08-16)
+ python -m pytest tests -q    # 319 passed, 30 skipped (2026-10-14)
- **Gotchas:** `setup.py` install_requires is **stale** - trust `requirements.txt`.
+ **Gotchas:** `setup.py` mirrors `requirements.txt` exactly (verified 2026-10-14).
```

## E-07 · MEDIUM · Пары CHECK/EXPECT в `GATES.md` невыполнимы как написано

**Где:** `GATES.md:19-20, 24, 27-28` (и тот же список ruff-правил, что был в `AGENTS.md`)

`G-P1-3` гоняет `pytest tests/test_fullsize_discovery.py tests/test_parser_manager_filtering.py`
и ждёт `test_referer_from_context_source_url` в выводе — этот тест живёт в
`tests/test_gateway_bypass.py:73`. Проверено: команда даёт `24 passed`, имя не печатается
никогда. `G-P1-4` ждёт «`1 passed`» для `tests/test_app_paths.py` → факт **`3 passed`**.
`G-P1-5` выбирал `E722,E402,E741,E731`, который не видит существующий
`tests/test_fullsize_discovery.py:328 E401`; только более новый `GATES.2026-10-14.md`
выбирает `E401,F`. Любой, кто перепрогонит старый леджер, воспроизводит два падающих гейта
на зелёном коде.

```diff
-- CHECK: `python -m pytest tests/test_fullsize_discovery.py tests/test_parser_manager_filtering.py -q`
-- EXPECT: ... new test name `test_referer_from_context_source_url` appears.
+- CHECK: `python -m pytest tests/test_gateway_bypass.py -q`
+- EXPECT: ... `test_referer_from_context_source_url` appears; `3 passed` for tests/test_app_paths.py.
-- CHECK: `python -m ruff check src/ tests/ --select E722,E402,E741,E731 --output-format=concise`
+- CHECK: `python -m ruff check src/ tests/ --select E722,E402,E741,E401,F --output-format=concise`
```

## E-08 · MEDIUM · PyPI-пакет `deno` — документированный шаг разрешения Deno, но не заявленная зависимость

**Где:** `src/parser/js_engine/engine.py:78-84` (`_package_candidate`); `requirements.txt`;
`setup.py:23-39`

`_package_candidate()` делает `import deno; return deno.find_deno_bin()`. Это шаг 2
порядка разрешения, документированного в `AGENTS.md`/`knowledge.md`, но `deno` не
встречается ни в одном файле зависимостей и отсутствует в venv (проверено: `pip list` не
знает `deno`), поэтому на любой машине, собранной из
`pip install -r requirements.txt`, этот шаг никогда не может удаться. Документированный
путь установки молча отключает движок Deno (фолбэк на Static), а это user-видимая фича
(`js_engine=deno`, DOM/gateway-воркеры), а не краш.

```text
# requirements.txt (+ зеркальная строка в setup.py install_requires)
deno==2.* ; sys_platform == "win32"   # engine.py:_package_candidate() Deno resolution
```

## E-09 · MEDIUM · Гейт `ruff` невозможно прогнать внутри project venv, который заставляет создавать docs

**Где:** `GATES.md:27`; `AGENTS.md` Quick start (`.\venv\Scripts\activate`)

`.\venv\Scripts\python.exe -m ruff --version` → `No module named ruff` (проверено); ruff
0.11.13 существует только в *системном* Python (совпадает с `.ruff_cache/0.11.13`), и в
`requirements.txt` его нет. То есть последовательность «активировать venv (quick start
AGENTS) → прогнать линт-команду GATES» падает.

```text
# requirements.txt — dev/lint тулинг, используемый GATES.md (гонится вне app venv)
ruff>=0.6 ; python_version >= "3.8"
```

---

# БЛОК F — LOW (significant but not today's defects)

| ID | Где | Механизм | Исправление |
|---|---|---|---|
| **F-01** | `settings_dialog.py:84-110` vs `constants.py:70-72` | `SettingsDialog.default_settings` дублирует `constants.py` и расходится: его литеральный `stop_words` содержит **25** записей, `K.DEFAULT_STOP_WORDS` — **9**; собственный фолбэк `sanitize_settings` восстанавливает список из 9 слов. Удаление `settings.json` молча расширяет фильтр краулинга в 2.7 раза. Он также опускает `filter_junk`, `js_engine`, `http_engine`, `http_impersonate`, `http_escalate`, `verify_tls`, `enabled_audio_formats`, поэтому каждый опирается на *третий* дефолт в `.get(key, fallback)` консьюмера. | `self.default_settings = {**K.DEFAULT_SETTINGS_VALUES, "page_limit": 1000, "last_download_dir": os.path.join(get_app_dir(), "downloads"), "log_to_file": False, "log_file_path": "web_media_parser.log"}` |
| **F-02** | `main_window.py:691-694` | `_clear_download_history` дёргает `task_queue._queue.clear()` и `._active_id = None` напрямую, минуя `clear_active()` — единственный путь, эмитящий `active_task_changed` (`task_queue_manager.py:203-209`). Break инкапсуляции с живым последствием в момент, когда консьюмер подпишется на этот сигнал. | `if self.task_queue._active_id: self.task_queue.clear_active()`, затем `for tid in [t.id for t in self.task_queue.queue]: self.task_queue.remove_task(tid)` |
| **F-03** | `http_server.py:31, 107-116` | `GET /api/queue` зарегистрирован и делегирует в `get_status_callback`, поэтому возвращает ровно то же, что `/api/status`. `grep` по `extension/` показывает, что расширение фетчит только `/api/tasks` и `/api/status`. Зарегистрированный эндпоинт, врущий о своём содержимом, — ловушка для следующего сопровождающего, и он удваивает поверхность read-пути (`_is_trusted_origin` применяется только к `POST /api/tasks`). | Убрать роут или реализовать его через `get_queue_callback`, возвращающий `[t.to_dict() for t in queue]`. |
| **F-04** | `http_server.py:104, 115, 162` | Все три обработчика кладут сырой текст исключения в JSON-ответ. `add_tasks_from_extension` бросает на отказах `os.makedirs` / `add_task`, а `OSError.__str__` содержит полный локальный путь — поэтому любой локальный клиент может перечислить раскладку каталогов пользователя. Сервер также логирует без `exc_info=True`, поэтому диагностика на стороне оператора хуже, чем на стороне атакующего. | `logger.error("Error adding tasks from extension", exc_info=True)` + `return web.json_response({"error": "Failed to add tasks"}, status=500, headers=...)` |
| **F-05** | `main.py:60-64`; `build_exe.py:46` | `--windowed` означает отсутствие консоли и `sys.stderr is None` в замороженном приложении; в `main()` нет `try/except` вокруг `MainWindow()` или `app.exec()`. Любой отказ времени конструирования даёт молчаливый выход без видимого пользователю вывода. Stylesheet открывается с платформенной кодировкой по умолчанию (`main.py:54`) — сейчас чистый ASCII, поэтому латентно, но любая локализация `.qss` молча ломает старт. | Обернуть конструирование в `try/except` + `QMessageBox.critical(traceback...)`; `open(stylesheet_path, "r", encoding="utf-8")` |
| **F-06** | `js_engine/engine.py:355-364` | `shutdown()` убивает процессы и чистит кэш, но не ставит флаг closed, а `_ensure_*_proc` респавнит по требованию. Вызов, пришедший после `shutdown()` (например, probe-корутина, пережившая `gather` на `parser_manager.py:350`), стартует новый `deno.exe`, который никто никогда не убьёт, а `self._proc is None` после shutdown делает картину чистую. | `self._closed = False` в `__init__`; `def shutdown(self): self._closed = True; ...`; `def _ensure_proc(self): if self._closed: raise OSError("engine shut down")` |
| **F-07** | `extension/popup/popup.js:344-459` | `renderMediaList` перестраивает все строки с нуля на каждую смену фильтра и создаёт по одному `new Image()` на элемент (399-400), который браузер декодирует — ничто не ограничивает количество, а количество диктуется *счётчиком картинок страницы* (неограниченно, см. D-03). На странице-галерее в 2000 картинок это ~10 000 DOM-узлов плюс 2000 одновременно декодируемых thumbnail'ов в процессе попапа, у которого бюджет кучи много меньше вкладки. | Рендерить `filtered.slice(0, 300)` и добавить `...and N more (narrow the filters)`; или `loading="lazy"` на thumbnail. |
| **F-08** | `tests/test_fullsize_discovery.py:278, 309, 329`; `tests/test_task_queue_persistence.py:20` | Голый `tempfile.mkdtemp()` без очистки (это классы `unittest.TestCase`, поэтому фикстура `tmp_path` недоступна). Проверено: прогон только `test_fullsize_discovery.py` вырастил `%TEMP%` на **+3**; весь сьют — на **+8**. Неограниченный осадок вне репо, невидимый для pytest. | `def setUp(self): self._tmp = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self._tmp, True)` |
| **F-09** | `tests/test_json_parser.py:208, 99`; `tests/test_shared_session.py:166` | AST-обход всех 21 тестовых модулей нашёл ровно один тест **вообще без утверждений** (`test_scan_is_bounded_on_pathological_payload`) и две тавтологии (`assertIsInstance(links, set)`; `assertIn(result, (True, False))`). Они never не сигналят о регрессии в том поведении, ради которого названы. | Утверждать заявленное: `self.assertEqual(links, set())`, `self.assertIn(self.FEED + "?page=2", links)`, `self.assertFalse(result, "brotli patch must be a no-op check")` |
| **F-10** | `requirements.txt:1-29`; `setup.py:23-39` | `pytest` не заявлен нигде (`extras_require` пуст — проверено через AST), хотя это единственный автоматический гейт. Чистая машина, следующая README, не может прогнать сьют, который README/GATES/AGENTS считают обязательным. | `extras_require={"test": ["pytest>=8.0"]}` в `setup.py` |
| **F-11** | `.gitignore:34, 182` | (а) `WebMediaParser.spec` исключен `*.spec`, поэтому untracked — он был сравнен с `build_exe.py` построчно и **дрейфа флагов не имеет**, но из-за игнора никто не может его review'ить, а на чистом клоне его нет. (б) Правила для `node_modules/` нет, хотя документированный гейт расширения — `node extension/tests/*.test.mjs`. (в) `*.exe` означает, что закоммиченный `bin/deno.exe` (документированное место разрешения Deno) нельзя добавить без `-f`. | `!WebMediaParser.spec` + `node_modules/` |
| **F-12** | `priority_url_queue.py:44, 56` | `_url_scores` нигде не пишется и нигде не читается (мёртвое состояние). `_seen` не имеет предела и очищается только в `reset_async_primitives()` (вызывается один раз за запуск), а записи `from_media_item`/`interstitial_retry` полностью минуют проверку дедупа и могут толкать один и тот же URL повторно. Память растёт с каждым когда-либо *рассмотренным* URL, включая отклонённые. | Удалить мёртвый dict; ограничить `_seen` (или задокументировать, что per-task время жизни намеренно, и сделать это явным, вместо опоры на пересоздание очереди в `parser_manager`). |
| **F-13** | `parser_manager.py` (нет покрытия тестами) | Нет тестов на: round trip `save_state`/`load_state`, потерю in-flight элементов, персист `domain_health`/`quarantined_domains`; маршрутизацию карантина, выбор таймаута пробации/повтора, ре-очередь `FAILURE_CONTENT_PAGE` (988-1048); idle-завершение `_completion_monitor` + завершение `_monitor_progress` (388-427, 1138-1148); `_get_filename_from_url` / построение подкаталога на страницу (1152-1164, 944-951); гейт junk-фильтра в `_process_media_batch`, ре-очередь `is_webpage_url`, сортировку по приоритету (914-963). Это ровно те пути персистенции состояния и конкарренси, которые регрессируют молча. | Один страховочный тест: `test_save_state_keeps_inflight_download(tmp_path)`, утверждающий `download_queue.qsize() == 1` после round trip с элементом в `_inflight_media`. |
| **F-14** | `tests/test_media_downloader.py`, `test_http_engine.py`, `test_shared_session.py` | Пробелы: нет end-to-end теста `_download_with_threads` (собранный файл == конкатенация частей, срабатывает финальная проверка размера, успешный путь не оставляет `.part*`); нет теста, что MT-путь использует temp+rename; нет теста на `progress_callback` (остаётся ли в `[0,100]`, не регрессирует ли); нет тестов на пути `stop_event` (single-thread 522, MT 686), поэтому удаление part-файла на Stop не имеет страховочной сети; нет теста, что `verify_tls` доходит до основной сессии; нет теста на идемпотентность `close()` или на фолбэки заголовков к константам `K`. | Один параметризованный тест на оба пути: `test_stop_event_removes_temp_and_reports_abort(tmp_path)`, утверждающий `success is False`, `"aborted" in error` и `not list(tmp_path.glob("*.partial"))`. |
| **F-15** | `tests/test_js_engine.py`, `tests/test_gateway_bypass.py` | Оба сьюта на этой машине скипаются, и ни один контракт жизненного цикла процесса не утверждается нигде: путь записи в stdin с полезной нагрузкой > 64 КБ; смерть воркера посреди запроса и `BrokenPipeError` на записи → респавн; таймаут → `_kill_proc` → следующий вызов респавнит и всё ещё возвращает корректный результат; детекция id/рассинхрона; два потока, зовущие `run_js` конкурентно (запросы не должны перемежаться на одном воркере); правило с `while(true){}` должно таймаутить, не вешая сьют. | `test_run_js_recovers_after_worker_death(monkeypatch)`: прогнать раз, `e._proc.kill()`, прогнать снова, утвердить тот же корректный результат и `e._proc.poll() is None`. |

---

# Оставшиеся риски, которые нельзя безопасно починить автоматически

1. **B-08 (обход корня закачки через `..`)** — обоснованность проверки `..` в `filepath`
   на стороне закачки нужно согласовать с владельцем: `parser_manager` уже подрезает
   часть недопустимых символов (`:946-948`), и жёсткая проверка может ломать
   легитимные сценарии с точками в имени папки. Нужен пользовательский тест на
   «страница с `..` в URL».
2. **A-02 (cookie на диске)** — предложенное решение меняет видимое поведение: после
   resume расширенческой задачи cookie придётся отправлять повторно. Это
   намеренный размен (нет секретов в состоянии), но его нужно вынести в релиз-ноты.
3. **D-04 (вся cookie-банка открытым текстом по loopback)** — сужение области (отказ
   от `httpOnly`, ограничение `path === "/"`) может сломать сайты, которым нужны
   именно эти cookie. Безопаснее всего — сделать отправку cookie опциональной
   настройкой, но это уже изменение продукта, а не правка.
4. **A-13 / Chrome < 111** — подтвердить `minimum_chrome_version` можно только на
   реальной сборке Chrome < 111; в аудите проверена семантика промисов по
   документации, не исполнением.
5. **B-17 (`window.happyDOM.close()`)** — наличие этого API в вендоренной версии
   happy-dom не проверено: `src/parser/js_engine/deno_cache/` в репозитории
   отсутствует (генерируется сборкой). Нужно проверить на машине с установленным
   Deno перед применением.
6. **B-18 (потенциально квадратичный обход DOM в `findNode`)** — профиль подтверждён
   чтением кода, но численные оценки не измерялись. Перед оптимизацией стоит снять
   замеры на реальной большой странице.
7. **Размер файлов** — `webpage_parser.py` (1586), `parser_manager.py` (1229),
   `main_window.py` (1203), `site_pattern_manager.py` (1124),
   `settings_dialog.py` (947) превышают 1000 строк. Это не дефект, но любая новая
   логика в них должна выноситься в отдельные модули, а не дописываться внутрь.

# Допущения

- **Только отчёт.** `ReviewPrompt.txt` требует «Don't change anything in the code»,
  поэтому находки не исправлены. Правки внесены только в `Audit.md` и добавлен леджер
  `GATES.2026-10-14.md`.
- **Язык документа** — русский, как у остальной проектной документации
  (`CONTEXT.md`, `knowledge.md`, предыдущий аудит).
- **Исторический аудит 2026-09-09 сохранён** ниже без изменений, а не заменён: git
  уже хранит историю, но пометки «ИСПРАВЛЕНО / не исправлено» в старом блоке полезны
  при работе с находками A-1…C-6.
- **Baseline** — `3aa79ae`; все числа (319/30, 12 node-сьютов, 1 pyflakes, 4 ruff)
  измерены на нём. Пары «синхронизация Python ↔ extension JS» не проверялись на живых
  сайтах — только по построению URL и по коду.
- **Deno отсутствует** на машине ревью, поэтому 30 скипов и находки A-08, A-15,
  A-16, B-15…B-19 установлены чтением кода с трассировкой вызовов, а не исполнением
  воркеров. Это отмечено `[чит.]` в каждой такой находке. Находки с `[исп.]`
  воспроизведены реальными скриптами на Python/Node.
- **Severity** выставлена по последствиям для пользователя (потеря/порча данных,
  молчаливый отказ, безопасность), а не по объёму правки.

# Рекомендуемый порядок исправления

**Сначала (ломает сборку и тесты, дешевле всего):**
A-01 → E-03 → E-02 → F-11 → E-06/E-07 (дрейф доков и гейтов).

**Затем (безопасность и целостность данных):**
A-02, B-08, B-10, B-11, B-12, C-04, C-05, D-04, D-06, D-07.

**Затем (молчаливые потери и консистентность):**
A-03, A-04, A-05, A-06, A-07, A-09, A-10, A-11, A-12, D-10, D-11, D-12.

**Затем (конкарренси и ресурсы):**
B-01, B-02, C-06, C-07, C-08, C-09, A-15, A-16, B-13, B-14, B-15, B-16, B-17, B-18.

**Затем (наблюдаемость и UX):**
C-01, C-02, C-03, B-19, F-04, F-05, A-13, D-01, D-02, D-08, D-09, F-07.

**Последними (гигиена и тесты):**
E-01, E-05, E-08, E-09, F-01…F-03, F-06, F-08…F-10, F-12…F-15, D-03, D-05.

Каждая находка в этом аудите содержит готовый фрагмент; при правках держать
`python -m pytest tests -q` зелёным и расширять `extension/tests/` для всех изменений
в sieve/upgrade-логике (паритет двухсторонний — см. A-09…A-12).

---

# ИСТОРИЧЕСКИЙ АУДИТ 2026-09-09

> Ниже — предыдущий аудит без изменений. Актуальность отдельных пунктов см. в блоках
> A–F выше.

# Audit.md — полный инженерный аудит репозитория (2026-09-09)

> **АКТУАЛИЗАЦИЯ 2026-09-10:** находки A-1…A-8, B-1…B-4 и C-1…C-4 (частично) ИСПРАВЛЕНЫ
> в коммите **`7415163`** (`fix: audit P1-P5 — MT-download closed-file bug, settings
> isolation, sieve parity + online update`). Конкретные решения, гейты приёмки и журнал
> новых дефектов, найденных при реализации — в `PLAN.md` (§8) и `GATES.md`. Ниже в тексте
> у каждого пункта стоит маркер статуса. Не выполнено: A-9.1/A-9.2/A-9.5 (низкий
> приоритет), C-5 (отдельная задача), C-6.3-хвост, часть B-4.1 (дедуп в popup).

**Дата:** 2026-09-09 · **Baseline:** коммит `7bac98b` (после `chore: extend review prompt scope…`).
**Метод:** сплошное чтение кода всех модулей приложения и расширения, прогон тестов,
линта и статического анализа, точечные runtime-проверки подозрений. Предыдущий аудит
(2026-08-16) заменён этим документом — история сохранена в git.

**Прогон валидации (исходный, на бейзлайне `7bac98b`):**
- `python -m pytest tests -q` → **265 passed, 30 skipped** (12.3 c). Скипы: Deno-зависимые
  тесты (нет deno на PATH в этом окружении), curl_cffi-тесты (P3), сетевой плейсхолдер.
- `python -m pyflakes src/ tests/` → 1 замечание (намеренный side-effect import `brotli`
  в `shared_session.py`, помечен `noqa`, но pyflakes его не понимает).
- `python -m ruff check src/ tests/` → 120 ошибок, из них 116 — E701/E702 (компактный
  стиль с `;` — намеренный, соответствует Karpathy-дисциплине проекта, чинить не нужно);
  реальные: 1×E722 (bare `except`), 1×E402 (import после кода), 1×E741, 1×E731.
- Type-checker: mypy отсутствует в проекте (не настроен).

**Прогон валидации (после исправлений, `7415163`):**
- `python -m pytest tests -q` → **289 passed, 30 skipped** (+24 новых теста, 0 failed).
- `python -m ruff check src/ tests/ --select E722,E402,E741,E731` → 0 ошибок.
- Расширение: `node --check` на всех затронутых файлах — чисто; `node extension/tests/
  url_transform.test.mjs` и `sieve_merge.test.mjs` — ALL PASS.
- curl_cffi **установлен** в текущем окружении (0.14.0) — остаточный риск №3 исходного
  аудита снят (venv обновлён).

**Сводка по severity (исходная):**

| Уровень | Кол-во | Ключевое | Статус |
|---|---|---|---|
| Critical | 1 | MT-download пишет в закрытый файл | ✅ FIX (7415163) |
| High | 3 | Загрязнение настроек; мёртвый Referer; неверный путь session-очистки | ✅ FIX (7415163) |
| Medium | 7 | Гонка очереди; `verify=False`; unbounded `wait()`; sieve: `off`/`loop`/`data:,`; рассинхрон sieve | ✅ FIX (7415163) |
| Low | 8 | Bare except, мёртвый код, стиль, мелкие edge-case'ы | ✅ частично (A-9.1/9.2/9.5 и B-4.1 остались) |

---

# БЛОК A — Приложение (Python)

## A-1 · CRITICAL — `_download_chunk` пишет остаток буфера в уже закрытый файл; «Threads per File > 1» не работает никогда

> **✅ ИСПРАВЛЕНО (`7415163`)** — flush перенесён внутрь `with`; добавлен успешный
> MT-тест `test_mt_chunk_success_writes_full_file` (3×600 KB — остаток буфера гарантирован).

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

> **✅ ИСПРАВЛЕНО (`7415163`)** — `build_extension_task_settings()` возвращает независимую
> копию; тесты `test_extension_add_does_not_mutate_global_settings` и
> `test_extension_add_without_overrides_keeps_base_values`.

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

> **✅ ИСПРАВЛЕНО (`7415163`)** — обе точки чтения теперь берут
> `settings.get("_source_url") or context.get("source_url")`; ParserManager дополнительно
> зеркалирует context в копию settings. Тесты: `test_referer_from_context_source_url`,
> `test_referer_not_sent_for_self_source`, `test_referer_suppressed_with_policy_none`
> (последний поймал ловушку: ключ настроек — `"referrer"`, не `"referrer_policy"`).

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

> **✅ ИСПРАВЛЕНО (`7415163`)** — `app_paths.clear_task_sessions()` (тестируемая чистая
> функция) обходит `{download_dir}/{task}/sessions/`; user-файлы не трогает. Тесты:
> `tests/test_app_paths.py` (3 шт.).

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

> **✅ ИСПРАВЛЕНО (`7415163`)** — чистая функция `build_extension_task_payload()` считает
> payload в HTTP-потоке; материализация (`add_task` + mkdir) — в GUI-слоте
> `_apply_extension_task_payload` через существующий сигнал. Реализация вскрыла два
> дефекта (журнал PLAN.md §8): потеря задач при шатдауне (closeEvent теперь сбрасывает
> буфер перед сохранением очереди) и очистка буфера в Clear History.

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

> **✅ ИСПРАВЛЕНО (`7415163`, доработано повторной проверкой)** — настройка
> `verify_tls` (чекбокс Settings → HTTP) + `http_engine.tls_verify(settings,
> legacy_default=…)`. Важная поправка повторной проверки: пути с историческим
> `verify=False` сохраняют дефолт False, но escalation GET раньше НЕ передавал verify
> вообще (curl_cffi-дефолт = True) — первая версия фикса молча ослабила бы TLS там;
> `legacy_default=False` возвращает verify=True по умолчанию. Однократный warning при
> каждом невифицированном запросе. Проверено по документации curl_cffi: `verify`
> поддерживается её requests-совместимым API. Тесты: DL-4 обновлён + три состояния в
> `test_verify_tls_flag_passed_to_session`.

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

> **✅ ИСПРАВЛЕНО (`7415163`)** — `quit()` + `wait(10000)` + warning при таймауте в обоих
> обработчиках (в closeEvent уже был).

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

> **✅ ИСПРАВЛЕНО (`7415163`)** — `ruff --select E722,E402,E741,E731` чист; E701/E702
> намеренно не тронуты.

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

> **СТАТУС:** п.3 — подтверждён и остаётся осознанным поведением (см. исходный текст);
> п.4 — подтверждён, оставить; п.1, п.2, п.5 — **не выполнены** (низкий приоритет,
> решения описаны ниже и готовы к применению).

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

> **✅ ИСПРАВЛЕНО (`7415163`)** — `extension/sieve.json` = 823 правила от 2026.07.15;
> `SIEVE_VERSION = "2026.07.15"`; сверх минимального фикса внедрён полный C-1
> (онлайн-обновление) — рассинхрон больше не накапливается.

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

> **✅ ИСПРАВЛЕНО (`7415163`)** — замена в нисходящем порядке (зеркало Python-твина);
> тест `extension/tests/url_transform.test.mjs` (6 кейсов, ALL PASS).

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

> **✅ ИСПРАВЛЕНО (`7415163`)** — `let watchdog` с гардом `if (watchdog)` и сбросом в
> таймаут-колбэке; TDZ-окно устранено.

`background.js` — `const watchdog` объявлен **после** `addListener(listener)`, а listener
вызывает `clearTimeout(watchdog)`. В MV3-окружении событие не может прийти между
синхронными строками (events — асинхронные), поэтому TDZ на практике не срабатывает, но
это хрупкая конструкция: любой рефакторинг, сделавший listener синхронным вызовом,
уронит background-воркер с `ReferenceError`.

**Фикс:** объявить `let watchdog = null;` до `addListener`, присваивать в callback'е
`chrome.downloads.download`, и в listener проверять `if (watchdog) clearTimeout(watchdog)`.

## B-4 · LOW — Прочее (расширение)

> **СТАТУС:** п.3 (45s-бюджет `discoverFullsize`) — **✅ ИСПРАВЛЕНО (`7415163`)**;
> п.2 (`JUNK_PATTERNS` с `\b` после alternation) — подтверждён чтением
> `content_script.js:18-32`, паттерны `/\/(prev|next|close|…)\b/i` действительно могут
> отсечь легитимные пути (например `/nextgen/photo.jpg`) — решение описано ниже,
> НЕ применено (нужна проверка на живых сайтах, чтобы не потерять фильтрацию); п.1
> (дедуп popup) — подтверждён: `mediaItems.push(...response.media)` (popup.js:118) и
> `push(...linked.media)` (popup.js:127) без сквозного дедупа — решение ниже, НЕ применено.

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

> **✅ РЕАЛИЗОВАНО (`7415163`)** — десктоп: `SitePatternManager.download_imagus_from_url`
> (validRuleCount>0, атомарный os.replace, reload), поле + кнопка в Settings → HTTP,
> jsDelivr-фолбэк, ТОЛЬКО ручной запуск. Расширение: `extension/sieve_updater.js`
> (порт updateSieve: conditional GET, зеркало, backoff, merge с сохранением
> `_-правил`/`off`/ghost-правил), weekly `chrome.alarms` + кнопка "Update sieve now".
> Тесты: mock-HTTP (3), `sieve_merge.test.mjs` (12).

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

> **✅ РЕАЛИЗОВАНО (`7415163`, desktop)** — рекурсивное повторное разрешение в
> `_discover_linked_fullsize`: `loop & 1`, ровно один res-URL, совпадающий с другим
> правилом → следующий хоп; лимит `K.SIEVE_LOOP_MAX_HOPS = 5`, seen-set против циклов,
> общий 45s-бюджет (без деградации). Тесты: multi-hop разрешает CDN, no-loop сохраняет
> старое поведение, цикл обрывается. **В расширении НЕ реализовано** — сделать после
> стабилизации desktop-семантики (см. Остаточные риски).

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

> **✅ РЕАЛИЗОВАНО (`7415163`)** — `off: 1` скипается при загрузке (ДО отметки имени —
> поздний файл может дать включённую вариацию); `dc` загружается с debug-логом.
> Тесты: `test_imagus_off_rule_skipped`, `test_imagus_off_rule_shadowed_name_can_reappear`,
> `test_imagus_dc_rule_debug_logged`.

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

> **✅ РЕАЛИЗОВАНО (`7415163`)** — `apply_link_url_transform` возвращает None для
> `data:`-результатов в обеих ветках (строковой и JS); расширение — гард
> `startsWith("data:")` в discoverFullsize. Caller корректно зондирует саму страницу.
> Тесты: `test_data_template_rejected`, `test_data_template_rejected_js_branch`,
> data:-кейс в url_transform.test.mjs.

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

> **СТАТУС: НЕ РЕАЛИЗОВАНО** — отдельная задача (изменение архитектуры расширения:
> перенос исполнения JS-правил в content script с URL-шимом для `this.node`);
> решение в исходном тексте ниже остаётся актуальным.

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

> **СТАТУС:** п.1 (валидация перед заменой) — **✅ РЕАЛИЗОВАНО** в
> `download_imagus_from_url`; п.3 (merge) — **✅ РЕАЛИЗОВАНО** в расширении
> (`mergeSieve`); для desktop native-паттерны остаются отдельным файлом, ничего делать
> не нужно; п.2 (grants) — не нужно (осознанно, см. исходный текст).

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

> **АКТУАЛИЗАЦИЯ:** дыры №1-5 закрыты в `7415163` (24 новых теста, 265 → 289 passed).
> Актуальны №6 (Deno в CI) и №7 (вынос чистых функций GUI — частично сделан:
> `build_extension_task_settings`/`build_extension_task_payload`/`clear_task_sessions`
> уже тестируются без Qt; полный вынос — по мере надобности).

**Покрытие в целом хорошее** (было 265 passed / 30 skipped, модули: url-detection, js-processing,
js-engine, pattern-manager, parser-manager-filtering, media-downloader, shared-session,
http-engine, gateway-bypass, crawler-frontier, fullsize-discovery, format-filter, junk-filter,
bugfixes, sec3-fixes, parser-lifecycle, parser-phase2/5, task-queue-persistence, http-server,
json-parser; стало 289 passed — добавлены app_paths, settings-isolation, referer,
MT-success, loop-chain, off/dc/data:, sieve-download, payload-buffering, verify-tls).

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
3. ~~**`http_engine` импорты в `try/except` на уровне модуля** — curl_cffi отсутствует в
   venv~~ **✅ СНЯТО 2026-09-10:** curl_cffi 0.14.0 установлен, `import curl_cffi`
   проходит — P3-функциональность (эскалация, impersonation) в этом окружении жива.
4. ~~**Расширение отстаёт от приложения по sieve** — B-1~~ **✅ СНЯТО:** bundled sieve
   синхронизирован (823/2026.07.15) и внедрён C-1 — онлайн-обновление не даёт
   рассинхрону накапливаться.
5. **GIF/SVG/ICO выключены по умолчанию** — часть галерей (эмодзи-паки, стикеры) будет
   пропущена; это намеренная настройка по умолчанию (Settings → Filters), не баг.

---

# БЛОК E — Дефекты, найденные ПРИ внедрении фиксов (второй проход, 2026-09-09/10)

Все исправлены в `7415163`; полный журнал с гейтами — `PLAN.md` §8, `GATES.md`.

## E-1 · NEW DEFECT — `extract_res_urls` калечил относительные res-совпадения

`site_pattern_manager.py` (ветка regex-res): старый код
```python
u = m.group(1)
if not u.startswith('http'):
    u = 'https:' + u   # 'abc.jpg' → 'https:abc.jpg' — мусор
```
Проблема была скрыта, потому что в поставляемом sieve группа 1 почти всегда захватывает
protocol-relative URL (`//host/path`). Вскрылось тестами loop-цепочки.
**Фикс (в `7415163`):** `//`-префикс → `https:` (семантика расширения сохранена); всё
остальное относительное → `urljoin(href or page_url, u)` — базой служит СФЕТЧЕННАЯ
страница (правильно для multi-hop), а не исходная страница краула.

## E-2 · REGRESSION INTRODUCED & FIXED — A-6 ослабил TLS на escalation-пути

Первая версия `tls_verify()` подставляла `verify=False` по умолчанию ВЕЗДЕ, включая
`_try_escalate_get`, который исторически вообще не передавал `verify` (curl_cffi-дефолт
= True) — т.е. верифицированный путь стал бы невифицированным. Проверено по докам
curl_cffi (requests-совместимый API принимает `verify`).
**Фикс:** сигнатура `tls_verify(settings, legacy_default=True)`; escalation-вызов
передаёт `legacy_default=False`. Урок: «дефолт по умолчанию» — не всегда legacy-поведение;
проверяй исходный контракт каждой точки вызова.

## E-3 · NEW DEFECT — потеря extension-задач при шатдауне (следствие A-5)

Буферизация payload'ов в HTTP-потоке создала окно: `closeEvent` сохранял очередь ДО
материализации отложенных payload'ов — задачи, запрошенные из расширения за секунды до
выхода, терялись.
**Фикс:** `closeEvent` сбрасывает `_pending_extension_payloads` через
`_apply_extension_task_payload` перед `task_queue.save()`; `_clear_download_history`
отбрасывает буфер (пользователь попросил очистить всё).

## E-4 · NEW DEFECT — loop-цепочка освобождала не тот URL при ошибке

Обработчик исключений делал `consumed.discard(link_url)`, но `link_url` мутирует между
хопами — сбой на середине цепочки возвращал бы в краул промежуточный хоп, а исходная
thumbnail-ссылка терялась.
**Фикс:** `original_link_url` фиксируется до цикла; в `consumed` кладётся/убирается
только он (промежуточные хопы — временные fetch-цели, в `consumed` не попадают).

## E-5 · Ловушка ключей настроек — `"referrer"`, не `"referrer_policy"`

`K.SETTING_REFERRER_POLICY = "referrer"` (constants.py:225). Тест, написанный с
`"referrer_policy"`, молча ничего не проверял (dict.get вернул дефолт). Ключи настроек
брать ТОЛЬКО из `K.*` — рукописные строковые ключи не проходят линт и создают такие
ловушки (там же: `page_limit` в parser_manager.py:87 до сих пор строкой — A-9.5).

---

# Допущения

1. Сравнение sieve-механик проведено по исходникам Mod-расширения
   (`background/service.js`, `data/sieve.json`, `content/content.js`); runtime-прогон Mod не
   выполнялся (сторонний проект вне репозитория). **Обновление:** Mod принадлежит автору
   этого проекта (MIT в его README) — копирование механик/кода разрешено; C-1 реализован
   как перенос с адаптацией к нашей архитектуре, C-2 — по исправленной семантике из
   `src/includes/content.js`.
2. Баг A-1 воспроизведён изолированно; **после фикса** успешный MT-путь покрыт тестом
   `test_mt_chunk_success_writes_full_file`; сквозной прогон с живой сетью не выполнялся
   (нет Accept-Ranges-сервера в окружении).
3. Ключи настроек считались каноническими из `src/constants.py` (`K.*`); несоответствия
   (`page_limit` строкой) отмечены как minor (A-9.5, не исправлено).
4. «Clear History» не удаляет скачанные файлы — по заявленному поведению UI; аудит не
   меняет этого, только чинит session-путь (A-4).
5. Стиль `t1.start(); t2.start(); ...` и компактные однострочники — намеренная
   Karpathy-дисциплина проекта; ruff E701/E702 не чинились.
6. Предыдущий `Audit.md` (2026-08-16) содержал ~90 находок; большинство уже исправлено
   (маркеры `CORE-*`, `DL-*`, `EXT-*`, `GUI-*`, `PAT-*`). История — в git.
7. **Реализация фиксов (7415163) проверена повторным проходом** — найдено и исправлено
   3 собственных дефекта (E-2, E-3, E-4) + 1 скрытый баг движка (E-1); это записано в
   `PLAN.md` §8 с полными гейтами.
8. Ручные гейты, требующие человека за машиной, НЕ выполнялись: (а) `python main.py` →
   Settings → «Download latest» против живого репозитория; (б) load-unpacked расширения →
   «Update sieve now». Код покрыт юнит-тестами с mock-HTTP.

---

# Итоговый статус для ReviewPrompt.txt

| Требование ReviewPrompt | Статус |
|---|---|
| Коммит всех изменений перед ревью | ✅ `7bac98b` перед аудитом, `7415163` после внедрения |
| Валидация: tests / lint / static analysis | ✅ pytest (265→289), pyflakes, ruff; mypy в проекте отсутствует (зафиксировано); build не прогонялся — не затрагивался |
| Сплошной code review обоих компонентов + Mod-сравнение | ✅ Блоки A-D + Блок C (сравнение) — с исправленной семантикой `loop` (C-2) |
| Конкретные решения с готовым кодом в Audit.md | ✅ у каждого пункта; для внедрённых — код в `7415163`, гейты в GATES.md |
| Тесты для зафиксированных фиксов | ✅ 24 новых Python-теста + 2 node-набора (18 кейсов) |
| Оставшиеся опасения, не исправляемые автоматически | ✅ блок «Остаточные риски» (№1, №2, №5) + нерешённые хвосты A-9/B-4/C-5 |
| Допущения | ✅ этот раздел (8 пунктов) |