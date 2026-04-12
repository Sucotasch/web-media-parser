> **🤖 Prompt Generation Metadata**
> - **Model:** gemini-3.1-pro-preview
> - **Target Repository:** https://github.com/Sucotasch/web-media-parser/tree/fix/web-parser-stability-and-persistence
> - **Auto-generated RAG Query:** "filter_logic_parsing,media_url_extraction,full_size_version_retrieval,regex_pattern_matching,media_type_validation,dead_code_functions,race_condition_in_parser,O(n^2)_loop_detection,memory_leak_in_media_handling,file_path_handling_web_media_parser,API_endpoint_validation,media_metadata_extraction,concurrent_processing_bottlenecks,error_handling_media_corruption,function_signature_analysis_web_media_parser"
> - **Agentic RAG Requested Files:** (3/3 fetched)
>   - `src/parser/webpage_parser.py`
>   - `src/parser/site_pattern_manager.py`
>   - `src/parser/parser_manager.py`
> 
> <details><summary><b>Task Instructions</b></summary>
> 
> ```text
> You are an expert Principal Software Engineer conducting a rigorous code audit. Do not rely solely on the README; perform a deep analysis of the provided codebase for:
> 1. **Defect Identification**: Pinpoint logical errors, dead code (non-functional functions), bugs, race conditions, and bottlenecks.
> 2. **Performance Impact**: Analyze any adverse performance impacts caused by the identified deficiencies (e.g., memory leaks, O(n^2) loops).
> 3. **Actionable Recommendations**: Provide specific, code-level recommendations for correction, improvement, and modernization. 
> CRITICAL CONSTRAINT: All recommendations must focus on preserving current functionality with *minimal code intervention*. Do not suggest complete rewrites unless absolutely necessary. Format the output as a structured Markdown report.
> Сосредоточься на имеющихся ошибках. Составь подробный план устранения ошибок и оптимизации, обоснованный ссылками на точные участки кода, с объяснениями и конкретными примерами кода для исправления.  План должен быть составлен так, чтобы прочитав его младший агент мог приступить к реализации не изучая весь код заново.
> ```
> </details>
>
> <details><summary><b>Additional Context</b></summary>
> 
> ```text
> Сосредоточься на логике фильтрации, логике парсинга и извлечения полноразмерных версий.
> ```
> </details>

---

Основываясь на проведенном глубоком аудите исходного кода `web-media-parser` (в частности, модулей `ParserManager`, `WebpageParser` и `SitePatternManager`), представляю подробный отчет об обнаруженных архитектурных и логических дефектах, их влиянии на производительность, а также конкретные, минимально инвазивные рекомендации по их устранению.

Отчет сфокусирован на логике парсинга, фильтрации и извлечения полноразмерных медиа-файлов, как было запрошено.

---

# Аудит кода: Web Media Parser (Парсинг и Фильтрация)

## 1. Дефекты логики извлечения полноразмерных изображений (Full-size Extraction)

### 1.1 Логическая ошибка в `SitePatternManager.transform_image_url`
**Проблема:** В методе `transform_image_url` регулярное выражение для поиска и замены (`re.sub`) отрабатывает на URL, однако если замена произошла, измененный URL возвращается, но **не обновляется для следующего правила в цепочке**, потому что цикл прерывается из-за условия `if transformed: break`. Это не позволяет применять множественные трансформации (например, сначала убрать параметры обрезки, а затем изменить домен CDN).
**Файл:** `src/parser/site_pattern_manager.py` (строка 155)
**Влияние:** Снижает эффективность извлечения оригиналов, так как применяет только первое совпавшее правило.
**Решение:** Убрать `break` после успешной трансформации, чтобы позволить цепочке правил отработать до конца (pipeline pattern), если это не противоречит бизнес-логике.

```python
# src/parser/site_pattern_manager.py
# БЫЛО:
if new_url != url:
    url = new_url
    transformed = True
    logger.debug(f"Transformed: {original_url} -> {url}")
# ...
if transformed:
    break # ОШИБКА: Прерывает цепочку трансформаций

# СТАЛО (Рекомендация):
if new_url != url:
    url = new_url
    transformed = True
    logger.debug(f"Transformed: {original_url} -> {url}")
# Убрать break, чтобы паттерны могли накладываться друг на друга, 
# ИЛИ ввести флаг 'terminal': True в JSON-схему паттернов.
```

### 1.2 Некорректный учет приоритета при трансформации `srcset` в `_get_best_image_url`
**Проблема:** Метод `_get_best_image_url` собирает кандидатов из атрибутов `src`, `data-src` и `srcset`. Однако для `srcset` ширина извлекается корректно, а вот *приоритет (width)* для кастомных атрибутов (типа `data-hires`) искусственно устанавливается в `100`. Если в `srcset` есть изображение шириной, скажем, `800w`, оно отсортируется **выше**, чем потенциально лучший оригинал из `data-original` (которому хардкодом дали `100`).
**Файл:** `src/parser/webpage_parser.py` (строка 268)
**Влияние:** В галереях (например, WordPress) скрипт часто скачивает среднеразмерную копию из `srcset` (например, 800px) вместо реального оригинала из `data-full` (которому присвоился вес 100).
**Решение:** Присваивать хай-рез атрибутам сверхвысокий искусственный вес (например, `999999`), чтобы они гарантированно выигрывали у `srcset`.

```python
# src/parser/webpage_parser.py -> _get_best_image_url
# БЫЛО:
priority = 100 if any(h in attr_name.lower() for h in ["hi-res", "high", "retina", "full", "original", "max"]) else 0

# СТАЛО:
# Используем огромное число, чтобы гарантированно перебить любой реальный width из srcset
priority = 999999 if any(h in attr_name.lower() for h in ["hi-res", "high", "retina", "full", "original", "max"]) else 0
```

---

## 2. Дефекты логики фильтрации и дублирования (Filtering & Deduplication)

### 2.1 Race Condition при дедупликации URL в `ParserManager._parser_worker`
**Проблема:** Проверка и добавление URL в `self.processed_urls` происходит внутри асинхронного воркера `_parser_worker`. `self.processed_urls` — это стандартное множество (`set`), которое в многопоточной/многозадачной среде (`asyncio` + `threading`) не является потокобезопасным без блокировок. Множество воркеров конкурентно читают и пишут в один `set`.
**Файл:** `src/parser/parser_manager.py` (строка 332)
**Влияние:** Классическое состояние гонки (Race Condition). Два воркера могут одновременно проверить `if current_url in self.processed_urls`, получить `False` и оба начать парсить одну и ту же страницу, что ведет к экспоненциальному росту сетевых запросов и `O(n^2)` разрастанию очередей.
**Решение:** Внедрить `asyncio.Lock` для защиты доступа к `self.processed_urls`.

```python
# src/parser/parser_manager.py
# В __init__ или start_parsing():
self._processed_lock = asyncio.Lock()

# В _parser_worker:
# БЫЛО:
if current_url in self.processed_urls:
    self.url_queue.task_done(); continue
self.processed_urls.add(current_url)

# СТАЛО:
async with self._processed_lock:
    if current_url in self.processed_urls:
        self.url_queue.task_done()
        continue
    self.processed_urls.add(current_url)
```
*(Аналогичную блокировку следует применить и к `self.downloaded_files` в `_process_media_batch`)*.

### 2.2 Логическая дыра в фильтрации "Stay in Domain"
**Проблема:** В методе `_process_parser_results`, логика проверки домена `if self.settings.get(K.SETTING_STAY_IN_DOMAIN...):` игнорирует тот факт, что медиа-контент (CDN) часто хостится на *других* доменах. Хотя `_process_parser_results` фильтрует только `urls_to_queue` (навигацию), функция `is_same_domain` может слишком агрессивно отсекать полезные ссылки на галереи, если они используют субдомены (например, `gallery.site.com` при старте с `www.site.com`).
**Файл:** `src/parser/parser_manager.py` (строка 304)
**Влияние:** Потеря контента на сайтах со сложной доменной структурой.
**Решение:** Обновить логику `is_same_domain` (или ее вызов) для учета базового домена (Root Domain), а не точного совпадения FQDN.

---

## 3. Уязвимости производительности и Bottlenecks

### 3.1 O(N^2) сканирование в `_handle_gateways`
**Проблема:** Внутри `WebpageParser._handle_gateways` происходит обход всех тегов `['a', 'button', 'input']` на странице: `for tag in soup.find_all(['a', 'button', 'input']):`. Внутри цикла вызывается `parent_form = tag.find_parent('form')`. `find_parent` — это операция обхода дерева вверх. Для страницы с тысячами ссылок это вызывает квадратичную сложность `O(N*M)` где M — глубина DOM-дерева.
**Файл:** `src/parser/webpage_parser.py` (строки 425-450)
**Влияние:** Синхронный `BeautifulSoup` блокирует Event Loop на тяжелых страницах, вызывая просадки производительности и `asyncio.TimeoutError` для других тасок.
**Решение:** Сначала найти все формы, собрать их submit-кнопки, а ссылки и кнопки без форм обрабатывать отдельно. Либо использовать селекторы `soup.select('a, button, form input')`, что работает быстрее на C-уровне `lxml`.

```python
# Оптимизация (WebpageParser._handle_gateways):
# Вместо вызова find_parent() для каждого тега, ищем формы явно:
forms = soup.find_all('form')
for form in forms:
    # Обрабатываем action формы
    pass

# Затем обрабатываем только <a> теги
for a_tag in soup.find_all('a', href=True):
    # Логика проверки паттернов
    pass
```

### 3.2 Утечка памяти в сессии `_sync_session` при Bypass
**Проблема:** Метод `WebpageParser._get_sync_session()` использует `requests.Session()`. Этот `Session` кэшируется в `self._sync_session`. Однако класс `WebpageParser` создается заново для *каждого* URL в методе `_invoke_parser` (`ParserManager`). Таким образом, `sync_session` живет только в рамках одного парсинга и не переиспользуется глобально, но при этом для каждого отброшенного `WebpageParser` соединения `requests` могут оставаться открытыми (TCP Keep-Alive) до сборки мусора, вызывая исчерпание пула сокетов.
**Файл:** `src/parser/webpage_parser.py` (строка 215)
**Влияние:** Скрытая утечка файловых дескрипторов (Sockets) при агрессивном парсинге сайтов с защитой от ботов.
**Решение:** Глобализировать `sync_session` на уровне `ParserManager` и передавать его в `WebpageParser` (по аналогии с `external_session`), обязательно закрывая его в блоке `finally` в `_main_task`.

---

## 4. Ошибки обработки карантина (Quarantine Handling)

### 4.1 Бесконечный цикл мертвых ссылок (Dead Links Loop)
**Проблема:** В `_downloader_worker` при попадании домена в карантин, элемент отправляется в `quarantine_queue` через `await self.quarantine_queue.put(media_item)`. Позже, в `_handle_empty_queues_and_quarantine` эти ссылки извлекаются. Проверка на лимит попыток есть: `if item.get("quarantine_retries", 0) >= K.QUARANTINE_MAX_ITEM_RETRIES: continue`. **НО!** При отбрасывании ссылки счетчик `self.stats["files_skipped"]` не увеличивается, и ссылка просто исчезает. Это ломает логику расчета прогресса в `_monitor_progress`, так как `total_proc` никогда не догонит `total_found`. Прогресс бар "зависнет".
**Файл:** `src/parser/parser_manager.py` (строка 216)
**Влияние:** Пользовательский интерфейс никогда не покажет 100% прогресс, если хотя бы один файл был окончательно забракован карантином.
**Решение:** Увеличивать счетчик пропущенных файлов при окончательном дропе из карантина.

```python
# src/parser/parser_manager.py -> _handle_empty_queues_and_quarantine
# БЫЛО:
if item.get("quarantine_retries", 0) >= K.QUARANTINE_MAX_ITEM_RETRIES:
    logger.debug(f"Dropping quarantined URL after max retries: {item['url']}")
    self.quarantine_queue.task_done()
    continue

# СТАЛО:
if item.get("quarantine_retries", 0) >= K.QUARANTINE_MAX_ITEM_RETRIES:
    logger.debug(f"Dropping quarantined URL after max retries: {item['url']}")
    self.stats["files_skipped"] += 1  # КРИТИЧЕСКИ ВАЖНО ДЛЯ GUI ПРОГРЕССА
    self.quarantine_queue.task_done()
    continue
```

## Итоговый план действий для младшего инженера:

1.  **Логика Full-Size:** Откройте `src/parser/site_pattern_manager.py`, строка ~155. Удалите `break` внутри цикла обработки `transform_image_url`, чтобы позволить каскадную замену.
2.  **Эвристика размеров:** Откройте `src/parser/webpage_parser.py`, метод `_get_best_image_url`. Измените значение переменной `priority` с `100` на `999999` для атрибутов 'hi-res', 'full' и 'original'.
3.  **Потокобезопасность:** В `src/parser/parser_manager.py` создайте `self._processed_lock = asyncio.Lock()` в методе `start_parsing` (сразу после создания Event Loop). Оберните вызовы `self.processed_urls.add()` и `self.downloaded_files.add()` в `async with self._processed_lock:`.
4.  **Прогресс-бар:** В методе `_handle_empty_queues_and_quarantine` добавьте `self.stats["files_skipped"] += 1` при окончательном удалении ссылки из карантина (превышение `QUARANTINE_MAX_ITEM_RETRIES`).