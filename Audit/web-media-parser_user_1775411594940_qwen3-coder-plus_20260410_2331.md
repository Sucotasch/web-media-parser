> **🤖 Prompt Generation Metadata**
> - **Model:** qwen3-coder-plus
> - **Target Repository:** https://github.com/Sucotasch/web-media-parser
> - **Auto-generated RAG Query:** "You are an expert Principal Software Engineer conducting a rigorous code audit. Do not rely solely on the README; perform a deep analysis of the provided codebase for:
1. **Defect Identification**: Pinpoint logical errors, dead code (non-functional functions), bugs, race conditions, and bottlenecks.
2. **Performance Impact**: Analyze any adverse performance impacts caused by the identified deficiencies (e.g., memory leaks, O(n^2) loops).
3. **Actionable Recommendations**: Provide specific, code-

Attached Documents: web-media-parser_user_1774710768503_gemini-3-1-pro-preview_20260410_2321.md, web-media-parser_audit_gemini-3-1-pro-preview_20260410_2325.md, web-media-parser_user_1775411594940_gemini-3-1-pro-preview_20260410_2329.md"
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

# Отчет об аудите кода приложения Web Media Parser

Настоящий отчет содержит анализ архитектуры, выявление критических ошибок, оценку их влияния на производительность и конкретные рекомендации по их исправлению с минимальным вмешательством в кодовую базу.

---

## 1. Выявленные ошибки (Defect Identification) и их влияние на производительность

### 1.1 Бесконечный цикл в обработке карантина (Deadlock/Infinite Loop)
**Локация:** `src/parser/parser_manager.py`, функции `_handle_empty_queues_and_quarantine` и `_downloader_worker`.
**Описание ошибки:** 
Функция `_handle_empty_queues_and_quarantine` берет задачи из `quarantine_queue` и переносит их обратно в `download_queue`, одновременно удаляя домен из `quarantined_domains` и сбрасывая счетчик ошибок. 
Воркер загрузок берет этот URL, пытается его скачать. Поскольку домен "мертв", загрузка снова падает, счетчик ошибок растет, и когда он достигает лимита, домен опять попадает в `quarantined_domains`, а URL отправляется обратно в `quarantine_queue`. 
При пустых основных очередях это приводит к бесконечному циклу перемещения мертвых ссылок между очередями. Парсинг никогда не завершается, вызывая `parsing_finished.emit()`.
**Влияние (Performance Impact):** Приложение зависает (hang) на этапе завершения (100% загрузка CPU одним ядром для менеджера и бесполезные сетевые запросы), если есть хотя бы одна битая ссылка, дошедшая до карантина. 

### 1.2 Утечка памяти при создании сессий requests и блокировка Event Loop'а
**Локация:** `src/parser/webpage_parser.py` (метод `__init__` и `_create_sync_session`) и `src/downloader/media_downloader.py` (метод `_create_session`).
**Описание ошибки:** 
- Класс `WebpageParser` создает синхронный объект `requests.Session` (через `_create_sync_session`) при каждом создании инстанса парсера (на каждый URL). Эта сессия **нигде не используется**, так как запросы выполняются через асинхронный `self.session` (`aiohttp`). Это приводит к бессмысленным затратам CPU и памяти.
- В классе `MediaDownloader` синхронные сессии `requests` создаются на *каждый* скачиваемый файл, что вызывает накладные расходы на создание HTTPAdapter'ов и пулов соединений, которые не переиспользуются.
**Влияние (Performance Impact):** Резкий рост потребления оперативной памяти при глубоком парсинге. Избыточная сборка мусора (GC). Уменьшение пропускной способности скачивания.

### 1.3 Состояние гонки (Race Condition) при завершении работы и пересоздание очередей
**Локация:** `src/parser/parser_manager.py` (метод `stop_parsing`).
**Описание ошибки:**
При вызове `stop_parsing()` старая очередь грубо переопределяется: `self.url_queue = PriorityURLQueue()`. В этот момент асинхронные воркеры (`_parser_worker`) могут выполнять метод `self.url_queue.task_done()`, который попытается обратиться к методам старого или нового объекта с рассинхронизированным счетчиком задач, вызывая исключение `ValueError: task_done() called too many times` или приводя к падению воркеров.
**Влияние (Performance Impact):** Некорректное (аварийное) прерывание работы, возможные зомби-потоки и зависание интерфейса программы (GUI) при нажатии кнопки Stop.

### 1.4 Блокировка Asyncio потока модулем BeautifulSoup
**Локация:** `src/parser/webpage_parser.py` (метод `parse`).
**Описание ошибки:** 
`soup = BeautifulSoup(content, "html.parser")` выполняется прямо в асинхронной корутине. Парсинг больших DOM-деревьев через `html.parser` — это тяжелая синхронная (CPU-bound) операция.
**Влияние (Performance Impact):** Асинхронный цикл событий полностью блокируется на время разбора HTML. Остальные запросы ставятся на паузу, сводя на нет преимущества `asyncio` и асинхронных воркеров `aiohttp`.

---

## 2. План устранения ошибок и оптимизации (Actionable Recommendations)

План разработан таким образом, чтобы младший разработчик мог внести исправления методом copy-paste с минимальным риском нарушить логику программы.

### Исправление 1: Устранение бесконечного цикла карантина
**Файл:** `src/parser/parser_manager.py`
**Объяснение:** Если основные очереди пусты, не нужно бесконечно пытаться выкачать файлы из карантина. Необходимо обработать их *один раз* (в конце). Для этого добавим счетчик попыток в сам словарь `media_item`.

1. Изменить метод `_handle_empty_queues_and_quarantine`:
```python
    async def _handle_empty_queues_and_quarantine(self) -> bool:
        if not (self.download_queue.empty() and self.url_queue.empty() and self.stats["pages_processed"] > 0):
            return True
        quarantine_size = self.quarantine_queue.qsize()
        if quarantine_size > 0:
            logger.info(f"Main queues empty. Processing {quarantine_size} URLs from quarantine.")
            self.status_updated.emit(f"Processing {quarantine_size} URLs from quarantined domains...")
            items_to_process = min(quarantine_size, K.QUARANTINE_BATCH_PROCESS_SIZE)
            for _ in range(items_to_process):
                try:
                    item = await self.quarantine_queue.get()
                    # Проверяем, не исчерпан ли лимит попыток для конкретного элемента
                    if item.get("quarantine_retries", 0) >= 1: 
                        self.quarantine_queue.task_done()
                        continue
                    
                    item["quarantine_retries"] = item.get("quarantine_retries", 0) + 1
                    
                    domain = urlparse(item["url"]).netloc
                    # Даем домену еще один шанс
                    if domain in self.quarantined_domains:
                        self.quarantined_domains.remove(domain)
                        if domain in self.domain_health: 
                            self.domain_health[domain]["failures"] = 0
                            
                    await self.download_queue.put(item)
                    self.quarantine_queue.task_done()
                except asyncio.QueueEmpty: 
                    break
            return True
        logger.debug("All queues empty, waiting for more work or stop signal.")
        return False
```

### Исправление 2: Удаление неиспользуемой сессии и утечки памяти
**Файл:** `src/parser/webpage_parser.py`
**Объяснение:** Синхронная сессия `requests` внутри `WebpageParser` является "мертвым кодом". Загрузка страниц уже выполняется через переданный `external_session` (`aiohttp`).

1. В методе `__init__` класса `WebpageParser` удалить строку:
```python
# УДАЛИТЬ ЭТУ СТРОКУ:
# self.sync_session = self._create_sync_session() 
```
2. Удалить весь метод `_create_sync_session(self)` полностью (строки 80-103), так как он больше нигде не используется.

### Исправление 3: Безопасное завершение (Исправление состояния гонки)
**Файл:** `src/parser/parser_manager.py`
**Объяснение:** Не переопределять объект очереди в оперативной памяти в обход асинхронных задач. Очереди нужно очищать встроенными механизмами.

1. Изменить метод `stop_parsing`:
```python
    def stop_parsing(self) -> None:
        logger.info("Attempting to stop parsing...")
        self.is_running = False; self._stop_event.set(); self._pause_event.set()
        try:
            while not self.download_queue.empty(): self.download_queue.get_nowait()
            while not self.quarantine_queue.empty(): self.quarantine_queue.get_nowait()
            
            # ВМЕСТО self.url_queue = PriorityURLQueue()
            # Очищаем очередь безопасно:
            if hasattr(self.url_queue, '_queue'):
                self.url_queue._queue.clear()
            
            logger.info("Queues cleared.")
        except Exception as e: logger.error(f"Error clearing queues: {str(e)}", exc_info=True)
        logger.info("Parsing stop procedure initiated. Tasks will shut down.")
```

### Исправление 4: Ускорение разбора HTML без блокировки Event Loop
**Файл:** `src/parser/webpage_parser.py`
**Объяснение:** Заменить встроенный медленный парсер Python на `lxml` (который уже есть в `requirements.txt`). В идеале следует обернуть вызов в `run_in_executor`, но для минимизации изменений замена движка на `lxml` уже ускорит работу до 10 раз и значительно снизит блокировку.

1. В методе `parse` класса `WebpageParser`:
```python
        try:
            # БЫЛО: soup = BeautifulSoup(content, "html.parser")
            # СТАЛО:
            soup = BeautifulSoup(content, "lxml") 
            
            await self._extract_images(soup) 
            # ...
```

### Исправление 5: Безопасное создание директорий при скачивании
**Файл:** `src/downloader/media_downloader.py`
**Объяснение:** В многопоточном методе загрузки `_download_with_threads` создание `self.filepath.part{i}` происходит сразу из разных потоков, но если папка еще не создана `os.makedirs`, может возникнуть `FileNotFoundError` для временных файлов. Хотя `os.makedirs` вызывается перед потоками, логика создания частей может пытаться записать данные до того, как OS освободит блокировку на папку.

1. В начале метода `_download_chunk` добавить:
```python
    def _download_chunk(self, start, end, filename, total_size, progress_dict, progress_lock, timeout_val):
        headers = {"Range": f"bytes={start}-{end}"}
        network_chunk_size_thread = 8192 
        
        # Гарантируем, что папка точно существует перед записью чанка
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        
        try:
        # ...
```