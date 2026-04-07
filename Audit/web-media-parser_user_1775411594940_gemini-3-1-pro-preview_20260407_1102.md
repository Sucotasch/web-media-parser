> **🤖 Prompt Generation Metadata**
> - **Model:** gemini-3.1-pro-preview
> - **Target Repository:** https://github.com/Gaurox/AI-Metadata-Inspector
> - **Auto-generated RAG Query:** "logical_error,dead_code,race_condition,mutex_lock,O(n^2)_complexity,heap_memory_leak,stack_overflow,thread_safety_violation,SQL_injection_vulnerability,unreachable_code,blocking_I/O,CPU_bottleneck,function_signature_deviation,unused_import,circular_dependency,concurrency_bug,buffer_overflow,time_complexity_analysis,deadlock,resource_leak,function_coverage_gap,API_deprecation,lock_contention,garbage_collection_pressure,async_await_misuse,static_analysis_warning,code_duplication,function_parameter_mismatch,file_path_analysis,test_coverage_missing,logging_gap,exception_handling_leak,loop_invariant_violation"
> - **Agentic RAG Requested Files:** (0/3 fetched)
>   - `src/parser/parser_manager.py`
>   - `src/downloader/media_downloader.py`
>   - `src/parser/scrapling_adapter.py`
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



# ОТЧЕТ ПО РЕЗУЛЬТАТАМ АУДИТА КОДА: Web Media Parser

## 1. Введение и общая оценка
Был проведен глубокий анализ предоставленного исходного кода приложения **Web Media Parser**. Выявлены критические архитектурные недостатки, связанные с управлением асинхронными задачами, состояниями гонки (race conditions) при использовании Playwright, ограничениями пула потоков (bottlenecks) и потенциальными утечками памяти. Ниже представлен подробный план устранения ошибок и оптимизации. 

Главное правило данного аудита — минимальное вмешательство (minimal code intervention). Полное переписывание компонентов не требуется.

---

## 2. Выявленные дефекты и план их устранения

### Дефект 1: Неполная отмена асинхронных задач (Зомби-корутины)
**Локация:** `src/parser/parser_manager.py` (Part 3)
**Описание:** При возникновении исключения отмены (`except asyncio.CancelledError:`) отменяются только задачи загрузки (`downloader_tasks`), в то время как задачи парсинга (`parser_tasks`) остаются активными. Если парсер заблокирован на ожидании семафора или элемента из пустой очереди, задача навсегда останется в памяти, порождая "зомби-корутины".
**Влияние на производительность:** Утечка памяти и ресурсов процессора, зависание приложения при остановке парсинга (Graceful Shutdown Failure).
**Рекомендация:** Добавить явную отмену `parser_tasks` в блок обработки отмены.

*Пример исправления:*
```python
        except asyncio.CancelledError:
            logger.info("Main parsing task cancelled.")
            # ОТМЕНЯЕМ И ЗАГРУЗЧИКИ, И ПАРСЕРЫ
            for t in downloader_tasks:
                if not t.done():
                    t.cancel()
            for t in parser_tasks:
                if not t.done():
                    t.cancel()
            raise
```

### Дефект 2: Состояние гонки (Race Condition) блокировки профиля Playwright/Scrapling
**Локация:** `src/parser/scrapling_adapter.py` (Part 7)
**Описание:** Контекст браузера инициализируется с жестко заданным путем профиля `user_data_dir=_BROWSER_PROFILE_DIR`. При `num_parsers > 1`, несколько конкурентных корутин попытаются запустить экземпляры Chromium с одной и той же директорией профиля. Chromium использует файл блокировки (lock-файл). Первый парсер захватит директорию, а остальные упадут с ошибкой или зависнут.
**Влияние на производительность:** Полная неработоспособность многопоточного JS-рендеринга.
**Рекомендация:** Сделать директории профилей уникальными для каждой сессии или эфемерными. Минимальным вмешательством будет добавление уникального суффикса/UUID к директории.

*Пример исправления:*
```python
            import uuid
            # Создаем временный суффикс для изоляции параллельных контекстов
            session_profile_dir = f"{_BROWSER_PROFILE_DIR}_{uuid.uuid4().hex[:8]}"
            
            if self.use_stealth:
                profile_flags = [
                    f"--user-data-dir={session_profile_dir}",
                    # ... остальные флаги
                ]
                # ...
            else:
                async with async_playwright() as p:
                    context = await p.chromium.launch_persistent_context(
                        user_data_dir=session_profile_dir,
                        headless=debug_headless,
                        # ...
                    )
```

### Дефект 3: Ограничение пула потоков (Thread Pool Exhaustion) при скачивании
**Локация:** `src/parser/parser_manager.py` (Part 5)
**Описание:** Метод `downloader.download` работает синхронно и запускается через `asyncio.get_event_loop().run_in_executor(None, ...)`. Использование дефолтного пула потоков (`None`) жестко ограничивает максимальное количество потоков значением `min(32, os.cpu_count() + 4)`. Если пользователь в настройках задаст количество потоков загрузки (например, 50 или 100), параллельная загрузка всё равно будет ограничена дефолтным пулом.
**Влияние на производительность:** "Бутылочное горлышко" (Bottleneck). Задачи скачивания будут блокировать друг друга в очереди к пулу потоков.
**Рекомендация:** Использовать выделенный `ThreadPoolExecutor` с размером, соответствующим настройкам пользователя (`num_downloaders`).

*Пример исправления:*
```python
        # Инициализация в начале (в run_workers или __init__):
        from concurrent.futures import ThreadPoolExecutor
        num_downloaders = self.settings.get(K.SETTING_DOWNLOADER_THREADS, K.DEFAULT_DOWNLOADER_THREADS)
        self._download_executor = ThreadPoolExecutor(max_workers=num_downloaders)

        # Вызов с кастомным executor'ом вместо None:
        result = await asyncio.get_event_loop().run_in_executor(
            self._download_executor, lambda: downloader.download(timeout=timeout_val, retries=retries_val)
        )
```

### Дефект 4: Утечка памяти (Memory Leak) активных сессий скачивания
**Локация:** `src/parser/parser_manager.py` (Part 5)
**Описание:** Каждый экземпляр `MediaDownloader` добавляется в список: `self._active_downloader_sessions.append(downloader)`. Однако очистка этого объекта из списка после завершения задачи нигде не осуществляется. При парсинге сайтов с тысячами медиафайлов этот список будет бесконечно расти.
**Влияние на производительность:** Постепенное увеличение потребления RAM вплоть до OutOfMemory.
**Рекомендация:** Использовать блок `finally` для очистки сессии из списка.

*Пример исправления:*
```python
        self._active_downloads += 1
        self._active_downloader_sessions.append(downloader)
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                self._download_executor, lambda: downloader.download(timeout=timeout_val, retries=retries_val)
            )
            # ... обработка ...
        finally:
            # Освобождение памяти и декремент счетчика
            self._active_downloads -= 1
            if downloader in self._active_downloader_sessions:
                self._active_downloader_sessions.remove(downloader)
```

### Дефект 5: Состояние гонки в GUI при перезапуске сессии
**Локация:** `src/gui/main_window.py` (Part 3)
**Описание:** При нажатии кнопки начала парсинга, код останавливает старый экземпляр через `self.parser_manager.stop_parsing()`, после чего мгновенно инициализирует и запускает новый (`self.parser_manager.start_parsing()`). Асинхронная отмена занимает время; старые потоки могут продолжать писать файлы и логи, пока новый менеджер пытается получить к ним доступ.
**Влияние на производительность:** Ошибки записи/доступа к файлам (PermissionError), дублирование логов, нестабильность UI.
**Рекомендация:** Дать event loop время завершить старые корутины перед инициализацией нового инстанса.

*Пример исправления:*
```python
        if self.parser_manager:
            self.log_handler.info("Releasing resources from previous session...")
            self.parser_manager.stop_parsing()
            # Запускаем отложенную инициализацию, чтобы дать таскам завершиться
            asyncio.create_task(self._start_new_manager_deferred(url, download_path, settings))
        else:
            self._start_new_manager(url, download_path, settings)

    async def _start_new_manager_deferred(self, url, download_path, settings):
        await asyncio.sleep(0.5) # Даем EventLoop 500мс на graceful shutdown
        self._start_new_manager(url, download_path, settings)

    def _start_new_manager(self, url, download_path, settings):
        self.parser_manager = ParserManager(...)
        # Подключение сигналов и старт ...
        self.parser_manager.start_parsing()
```

---

## 3. Резюме-задача для младшего инженера (Actionable Checklist)
Для внедрения данных изменений младшему инженеру необходимо выполнить следующие шаги:

1. **`src/parser/parser_manager.py`**:
   - Найти обработчик `except asyncio.CancelledError:`. Добавить цикл отмены для коллекции `parser_tasks`.
   - Импортировать `ThreadPoolExecutor`. Создать свой executor (`max_workers=num_downloaders`) и передавать его аргументом в `run_in_executor()` вместо `None`.
   - Найти место вызова `downloader.download` через `run_in_executor`. Поместить вызов в `try/finally` и обеспечить удаление экземпляра `downloader` из массива `self._active_downloader_sessions`.
2. **`src/parser/scrapling_adapter.py`**:
   - Подключить пакет `uuid`. Для переменной `_BROWSER_PROFILE_DIR` создать уникальный суффикс (`session_profile_dir = f"{_BROWSER_PROFILE_DIR}_{uuid.uuid4().hex[:8]}"`). Передавать именно её в параметры Playwright/Scrapling, чтобы решить проблему с лок-файлами.
3. **`src/gui/main_window.py`**:
   - Вынести код инициализации и запуска (`ParserManager(...)`, подключения сигналов и `start_parsing()`) в отдельный метод. Если старая сессия существует и останавливается, использовать `await asyncio.sleep(0.5)` внутри созданного таска перед вызовом этого нового метода.