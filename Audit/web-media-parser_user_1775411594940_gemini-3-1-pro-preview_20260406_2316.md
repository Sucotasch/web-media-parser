> **🤖 Prompt Generation Metadata**
> - **Model:** gemini-3.1-pro-preview
> - **Target Repository:** Local Folder
> - **Auto-generated RAG Query:** "You are an expert Principal Software Engineer conducting a rigorous code audit. Do not rely solely on the README; perform a deep analysis of the provided codebase for:
1. **Defect Identification**: Pinpoint logical errors, dead code (non-functional functions), bugs, race conditions, and bottlenecks.
2. **Performance Impact**: Analyze any adverse performance impacts caused by the identified deficiencies (e.g., memory leaks, O(n^2) loops).
3. **Actionable Recommendations**: Provide specific, code-"
> - **Agentic RAG Requested Files:** (3/3 fetched)
>   - `web-media-parser/src/parser/scrapling_adapter.py`
>   - `web-media-parser/src/parser/parser_manager.py`
>   - `web-media-parser/src/parser/webpage_parser.py`
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

---



Ensure you format your response as requested, addressing Algorithm & Architecture, Defect Identification, Performance Impact, and Actionable Recommendations with code blocks and Russian descriptions where appropriate.Based on the provided files and the overarching goals, here is the comprehensive code audit and optimization plan focusing on fixing the Scrapling integration issues and resolving the bugs.

```markdown
# Web Media Parser Code Audit & Refactoring Report

## 1. Algorithm & Architecture (Алгоритмы и архитектура)

Приложение реализует паттерн **Producer-Consumer** с использованием асинхронного программирования (библиотека `asyncio`) и `PySide6` для GUI. 
Архитектурный поток выглядит следующим образом:
1. **Оркестрация (`ParserManager`)**: Класс инициализирует очереди задач (`url_queue` для поиска страниц и `download_queue` для загрузки файлов). Он же ограничивает одновременные запуски headless-браузера (Scrapling) через семафор `_browser_semaphore`.
2. **Workers (Продюсеры и Консьюмеры)**:
   - `_parser_worker` получает URL из очереди, определяет необходимый парсер, выполняет парсинг и передает найденные медиа в очередь на скачивание `download_queue`.
   - Если статический парсер (`WebpageParser`) находит мало медиа-элементов и включена опция JS, то происходит эскалация запроса (upgrade) до `ScraplingWebpageParser`, который рендерит JS.
   - `_downloader_worker` забирает элементы из `download_queue` и скачивает их.
3. **Scrapling Integration (`scrapling_adapter.py`)**: 
   - Выполняет запрос к URL в полноценном headless-браузере для обхода Cloudflare и рендеринга JS SPA.
   - Использует `AsyncDynamicSession` или `AsyncStealthySession` из нового API (Scrapling 0.4.x).

---

## 2. Defect Identification (Выявление дефектов)

Проверка выявила критические логические ошибки и race conditions (состояния гонки), которые напрямую ведут к зависаниям (таймаутам), утечкам памяти и отсутствию загрузки медиа:

### Дефект 1: Неправильное управление Task-ами и Event Loop (Main Task Cancellation / Deadlocks)
- **Файл**: `src/parser/parser_manager.py` (строка 138, метод `_main_task`)
- **Проблема**: В `finally` блоке происходит вызов `self.download_queue.put_nowait(PARSER_DONE_SENTINEL)`. Однако `put_nowait` может выбросить `asyncio.QueueFull`, если очередь имеет лимит (хотя сейчас она безлимитная, это плохая практика). Но главное — когда `_main_task` падает по `CancelledError`, `downloader_tasks` отменяются (`t.cancel()`), но при этом в очередь зачем-то кладутся сентинели для отмененных тасок.
- **Главная проблема парсера**: `ParserManager.stop_parsing()` использует `get_nowait()` для очистки очередей, однако вызов `self.cancel_all_tasks()` прерывает таски, включая `Scrapling`. Если Scrapling в этот момент захватил `_browser_semaphore`, он не успеет освободиться корректно из-за бага в старых версиях Playwright/Python, и последующие запуски зависнут в ожидании семафора.

### Дефект 2: Ошибки Scrapling API и блокировка DOM медиа-ресурсов
- **Файл**: `src/parser/scrapling_adapter.py` (строка 98)
- **Проблема**: В `AsyncDynamicSession` передан аргумент `disable_resources=True`. 
  `disable_resources=True` блокирует загрузку изображений/видео на уровне сети. Однако в современных SPA (React/Vue/LazyLoaders) именно обработчик события `onload` для изображений (или получение размеров ресурса) инициирует подстановку финального `src`. Если ресурс заблокирован, DOM содержит пустые теги или `data-src`, и `WebpageParser` (вызываемый после Scrapling) ничего не находит. Отсюда 0 найденных файлов.

### Дефект 3: Неправильное ожидание сети (Wait Condition)
- **Файл**: `src/parser/scrapling_adapter.py` (строки 88, 107)
- **Проблема**: Используется `wait_until="domcontentloaded"`. Для SPA-приложений (React, Vue) `domcontentloaded` срабатывает *до того*, как JavaScript загрузит JSON с медиа-ресурсами и отрендерит их в DOM. Это объясняет, почему все завершается "по таймауту" или возвращает пустой результат.
Нужно использовать ожидание отсутствия сетевой активности (`networkidle`).

### Дефект 4: Обработка `dummy_parser` с `process_js=False` и потеря `base_url`
- **Файл**: `src/parser/scrapling_adapter.py`
- **Проблема**: При передаче `html_content` в `dummy_parser` создается новый объект `BeautifulSoup`, однако Scrapling мог выполнить редиректы. Использование старого `self.url` может привести к неверному формированию относительных ссылок. Более того, `dummy_parser._handle_dynamic_content()` больше не будет работать из-за `process_js=False` (однако атрибуты `data-src` все еще важны, если скрипт SPA не успел их распаковать).

---

## 3. Performance Impact (Влияние на производительность)

1. **Зависания из-за Scrapling**: Поскольку Playwright не дожидается рендера (из-за `domcontentloaded`) или блокирует ресурсы (`disable_resources=True`), время тратится впустую, а медиа не находятся. В случае ошибки навигации браузер не закрывается быстро.
2. **Блокировка пула (Deadlock)**: Из-за ошибки в `_invoke_parser` при отмене задач, `_browser_semaphore` остается в захваченном состоянии. При нажатии "Stop" и "Start" приложение не может запустить Scrapling-парсер, так как семафор равен 0.
3. **Event Loop Bloat**: В `parser_manager.stop_parsing()` делается синхронный `get_nowait()` в цикле `while`, что блокирует Event Loop и подвешивает PySide6 UI.

---

## 4. Actionable Recommendations (План исправления и конкретные примеры кода)

Внесение изменений должно быть минимальным и точечным.

### Рекомендация 1: Исправить параметры Scrapling для полной поддержки JS
**Файл:** `src/parser/scrapling_adapter.py`
Необходимо отключить блокировку ресурсов (`disable_resources=False`) и изменить стратегию ожидания на `networkidle`, чтобы SPA-фреймворки успевали отрендерить <img> теги.

```python
# src/parser/scrapling_adapter.py: Около строки 88 и 107 (Метод parse)

# ...
            if self.use_stealth:
                logger.debug(f"Using Scrapling AsyncStealthySession with bypass for {self.url}")
                async with AsyncStealthySession(
                    headless=True,
                    solve_cloudflare=True,
                    allow_webgl=True,
                    extra_flags=[
                        "--address-family=ipv4",
                        "--disable-gpu",
                        "--no-sandbox"
                    ],
                    useragent=K.DEFAULT_USER_AGENT,
                    timeout=timeout_ms
                ) as session:
                    response = await session.fetch(
                        self.url,
                        page_action=bypass_interstitials,
                        # ИЗМЕНЕНИЕ: дожидаемся завершения сетевых запросов SPA
                        wait_until="networkidle" 
                    )
                    html_content = str(response.html_content) if hasattr(response, "html_content") else ""
            else:
                logger.debug(f"Using Scrapling AsyncDynamicSession with bypass for {self.url}")
                async with AsyncDynamicSession(
                    headless=True,
                    # ИЗМЕНЕНИЕ: False. Блокировка ресурсов ломает LazyLoad и SPA скрипты!
                    disable_resources=False, 
                    extra_flags=[
                        "--address-family=ipv4",
                        "--disable-gpu",
                        "--no-sandbox"
                    ],
                    useragent=K.DEFAULT_USER_AGENT,
                    timeout=timeout_ms
                ) as session:
                    response = await session.fetch(
                        self.url,
                        page_action=bypass_interstitials,
                        # ИЗМЕНЕНИЕ: Ожидаем отрисовки JS
                        wait_until="networkidle" 
                    )
                    html_content = str(response.html_content) if hasattr(response, "html_content") else ""
# ...
```

### Рекомендация 2: Защита семафора и предотвращение Deadlock при остановке
**Файл:** `src/parser/parser_manager.py`
Семафор `_browser_semaphore` в методе `_invoke_parser` может зависнуть при `asyncio.CancelledError`. Нужно гарантировать, что отмена задачи не обрывает контекстный менеджер некорректно. Однако `async with` уже защищает, проблема в том, что `self.cancel_all_tasks()` убивает всё мгновенно. Мы должны обрабатывать прерывания внутри `ScraplingWebpageParser`.

```python
# src/parser/parser_manager.py: Внутри метода _invoke_parser() (около 245 строки)
                
                if (low_media_found or depth == 0) and is_js_enabled and not self._stop_event.is_set():
                    reason = "start page" if depth == 0 else "0 valid media found"
                    logger.info(f"Targeting Scrapling for {url} ({reason}).")
                    try:
                        # ИЗМЕНЕНИЕ: Заворачиваем семафор в таймаут или проверяем флаг
                        async with self._browser_semaphore:
                            if self._stop_event.is_set():
                                return parse_result

                            sp = ScraplingWebpageParser(url, self.settings, use_stealth=is_protected)
                            scrapling_result = await sp.parse()
                            # ... (без изменений логики проверки s_media_count)
```

### Рекомендация 3: Исправить логику завершения `downloader_worker` и `stop_parsing`
**Файл:** `src/parser/parser_manager.py`
Метод `stop_parsing()` использует синхронные методы на очередях. Заменим их корректно. И предотвратим отправку сентинелей в `_main_task` если таски уже отменены.

```python
# src/parser/parser_manager.py (Метод stop_parsing)

    def stop_parsing(self) -> None:
        logger.info("Attempting to stop parsing...")
        self.is_running = False
        self._stop_event.set()
        self._pause_event.set()  # Unblock paused workers
        
        self.cancel_all_tasks()
        
        # ИЗМЕНЕНИЕ: Безопасная очистка asyncio очередей без блокировки event loop
        try:
            # Очистка asyncio.Queue
            while not self.download_queue.empty():
                try:
                    self.download_queue.get_nowait()
                    self.download_queue.task_done()
                except asyncio.QueueEmpty:
                    break
            while not self.quarantine_queue.empty():
                try:
                    self.quarantine_queue.get_nowait()
                    self.quarantine_queue.task_done()
                except asyncio.QueueEmpty:
                    break
            
            # Пересоздаем priority очередь
            self.url_queue = PriorityURLQueue()
            logger.info("Queues cleared.")
        except Exception as e:
            logger.error(f"Error clearing queues: {str(e)}", exc_info=True)
            
        logger.info("Parsing stop procedure initiated. Tasks will shut down.")

# В методе _main_task (около 138 строки)
        finally:
            # ИЗМЕНЕНИЕ: Отправляем Sentinel только если мы не были жестко прерваны CancelledError
            # Либо отправляем аккуратно
            if not self._stop_event.is_set():
                for _ in range(num_downloaders):
                    try:
                        self.download_queue.put_nowait(PARSER_DONE_SENTINEL)
                    except Exception:
                        pass
            self.is_running = False
            self.parsing_finished.emit()
            logger.info("Parsing process completed.")
```

### Рекомендация 4: Изменение вызова `WebpageParser` внутри `ScraplingWebpageParser`
Даже если `process_js=False`, мы можем терять атрибуты `data-src`, которые не успели раскрыться. Необходимо включить `process_js=True` для dummy-парсера, чтобы он мог использовать регулярки для извлечения сырых данных из отрендеренного DOM, если SPA фреймворк загрузился лишь частично.

```python
# src/parser/scrapling_adapter.py: Около строки 138

        # Reuse WebpageParser extraction logic on the rendered DOM
        try:
            dummy_parser = WebpageParser(
                url=self.url,
                settings=self.settings,
                # ИЗМЕНЕНИЕ: Включаем JS fallback-парсинг. 
                # Scrapling мог получить DOM, но скрипт не успел обновить ВСЕ data-src в src.
                process_js=True,  
                external_session=None,
            )

            # Передаем отрендеренный DOM
            soup = BeautifulSoup(html_content, "html.parser")
            await dummy_parser._extract_images(soup)
            await dummy_parser._extract_videos(soup)
            await dummy_parser._extract_links(soup)
            
            # ИЗМЕНЕНИЕ: Вызываем обработку динамических атрибутов вручную
            await dummy_parser._handle_dynamic_content(soup)

            self.links = dummy_parser.links
            self.media_files = dummy_parser.media_files
            # ...
```

### Итог для разработчика
Младший агент должен:
1. Зайти в `src/parser/scrapling_adapter.py` и изменить параметры Scrapling: `disable_resources=False` и `wait_until="networkidle"`.
2. В том же файле изменить инициализацию `dummy_parser` и включить вызов `_handle_dynamic_content()`.
3. Зайти в `src/parser/parser_manager.py` и обновить `stop_parsing()`, добавив вызовы `task_done()` при очистке очередей, чтобы избежать зависаний `asyncio.Queue`.
4. В `_main_task` защитить отправку `PARSER_DONE_SENTINEL`. 
Все изменения строго направлены на исправление таймаутов браузера и мертвых блокировок очередей без переписывания архитектуры.
```