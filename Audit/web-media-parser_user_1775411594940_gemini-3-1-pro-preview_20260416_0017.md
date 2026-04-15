> **🤖 Prompt Generation Metadata**
> - **Model:** gemini-3.1-pro-preview
> - **Target Repository:** Local Folder
> - **Auto-generated RAG Query:** "logical_error,dead_code,race_condition,bottleneck,O(n^2)_loops,memory_leak,thread_safety,concurrency_issues,function_signature_analysis,non_functional_functions,code_coverage_gaps,performance_regression,resource_leak,lock_contention,unreachable_code,function_deprecation,API_debt,file_path_analysis:/web-media-parser,function_call_graph,static_analysis_warnings,race_condition_prone_code,blocking_operations,unoptimized_data_structures,web_media_parser_dependencies,gemini-3-1-pro-preview_integration_points"
> - **Agentic RAG Requested Files:** (3/3 fetched)
>   - `web-media-parser/src/parser/parser_manager.py`
>   - `web-media-parser/src/parser/webpage_parser.py`
>   - `web-media-parser/src/downloader/media_downloader.py`
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



# Отчет об аудите кода: Web Media Parser

## Введение (Executive Summary)

Проведен глубокий технический аудит исходного кода парсера, включая архитектуру управления потоками, механизмы сетевого взаимодействия и сериализацию состояний. Проект имеет продвинутую архитектуру и высокий уровень обработки граничных случаев, однако выявлен ряд критических дефектов: состояния гонки (race conditions) при записи файлов, нарушения потокобезопасности при работе с `asyncio` из GUI-потока, утечки памяти при инстанцировании синхронных сессий и блокировка асинхронного цикла событий (Event Loop).

Ниже приведен подробный план устранения ошибок с сохранением текущей логики приложения и минимальными изменениями (minimal code intervention).

---

## 1. Критические ошибки и Дефекты логики

### 1.1. Нарушение потокобезопасности (Thread Safety Crash) при остановке парсинга
**Локация:** `src/parser/parser_manager.py` -> метод `stop_parsing()`

**Проблема:** Метод `stop_parsing()` вызывается из основного (GUI) потока по кнопке «Стоп». Внутри него происходит прямой вызов методов `get_nowait()`, `task_done()` для очередей `asyncio.Queue` и `_queue.clear()`. Объекты `asyncio` жестко привязаны к своему Event Loop. Модификация этих очередей из стороннего потока является классическим нарушением потокобезопасности (Not Thread-Safe), что периодически приводит к исключениям `RuntimeError` и падению/зависанию приложения при остановке.

**Влияние:** Случайные сбои программы при агрессивной остановке процесса пользователем.

**Решение:** Делегировать очистку очередей внутрь потока Event Loop с помощью `call_soon_threadsafe`.

**Код для исправления:**
```python
# В src/parser/parser_manager.py

# 1. Добавить новый метод для безопасной очистки в контексте Event Loop
def _drain_queues(self):
    try:
        while not self.download_queue.empty():
            self.download_queue.get_nowait()
            self.download_queue.task_done()
        while not self.quarantine_queue.empty():
            self.quarantine_queue.get_nowait()
            self.quarantine_queue.task_done()
        if hasattr(self.url_queue, '_queue'):
            self.url_queue._queue.clear()
        if hasattr(self.url_queue, '_not_empty'):
            self.url_queue._not_empty.set()
    except Exception as e:
        logger.error(f"Error draining queues: {e}")

# 2. Обновить метод stop_parsing
def stop_parsing(self) -> None:
    logger.info("Attempting to stop parsing...")
    self.is_running = False
    
    if self.loop and not self.loop.is_closed():
        self.loop.call_soon_threadsafe(self._stop_event.set)
        self.loop.call_soon_threadsafe(self._pause_event.set)
        # Вызываем безопасную очистку
        self.loop.call_soon_threadsafe(self._drain_queues) 
    else:
        if self._stop_event: self._stop_event.set()
        if self._pause_event: self._pause_event.set()
        self._drain_queues()
        
    logger.info("Parsing stop procedure initiated.")
```

### 1.2. Ошибка сериализации (Data Loss) при сохранении состояния
**Локация:** `src/parser/parser_manager.py` -> метод `save_state()`

**Проблема:** Код пытается сохранить ссылки из `PriorityURLQueue` перебирая элементы напрямую через `_queue`. Поскольку очередь приоритетная (`asyncio.PriorityQueue`), внутри она использует структуру `heapq`, где элементы хранятся в виде кортежа `(priority, count, real_item)`. Условие `elif isinstance(item, tuple) and len(item) >= 4:` не выполняется, так как длина кортежа равна 3. Как следствие, очередь ссылок игнорируется и не сохраняется — при перезапуске данные теряются.

**Влияние:** Необратимая потеря невыполненных задач при постановке на паузу или выходе.

**Решение:** Корректно распаковать элемент из приоритетной очереди.

**Код для исправления:**
```python
# В методе save_state:
        if hasattr(self.url_queue, '_queue'):
            for q_item in self.url_queue._queue:
                # Извлекаем реальные данные из кортежа приоритетной очереди
                item = q_item[-1] if isinstance(q_item, tuple) and len(q_item) == 3 else q_item
                
                if hasattr(item, 'url'):
                    url_queue_items.append((item.url, item.depth, item.source_url, item.context))
                elif isinstance(item, tuple) and len(item) >= 4:
                    url_queue_items.append(item)
```

### 1.3. Состояние гонки (Race Condition) при записи файлов с одинаковыми именами
**Локация:** `src/downloader/media_downloader.py` -> метод `_ensure_unique_filepath_at_destination`

**Проблема:** Проверка уникальности имени файла (`os.path.exists`) происходит параллельно множеством экземпляров `MediaDownloader` в разных потоках пула `run_in_executor`. Если на странице есть медиафайлы с одинаковым исходным именем (например `image.jpg`), несколько потоков могут одновременно получить `False` на проверке `exists()` и начать скачивание кусков данных в один и тот же файл, затирая данные друг друга (Data Corruption).

**Влияние:** Поврежденные медиафайлы («битые» картинки/видео) при параллельном скачивании.

**Решение:** Использовать глобальную блокировку (Lock) и предсоздание файла («touch»), чтобы зарезервировать уникальное имя за конкретным потоком.

**Код для исправления:**
```python
# В начале файла src/downloader/media_downloader.py
import threading
_filename_lock = threading.Lock() # Глобальный лок для имен файлов

# Внутри класса MediaDownloader
    def _ensure_unique_filepath_at_destination(self, current_filepath: str) -> str:
        with _filename_lock: # Защищаем процесс проверки и резервирования
            if not os.path.exists(current_filepath):
                open(current_filepath, 'a').close() # Резервируем имя файла (touch)
                return current_filepath
            
            dir_path, original_basename = os.path.split(current_filepath)
            base_name, ext = os.path.splitext(original_basename)
            counter = 1
            unique_filepath = os.path.join(dir_path, f"{base_name}_{counter}{ext}")
            
            while os.path.exists(unique_filepath):
                counter += 1
                unique_filepath = os.path.join(dir_path, f"{base_name}_{counter}{ext}")
                
            open(unique_filepath, 'a').close() # Резервируем уникальное имя
            return unique_filepath
```

---

## 2. Проблемы производительности и утечки ресурсов

### 2.1. Блокировка Event Loop'а при парсинге HTML (Bottleneck)
**Локация:** `src/parser/webpage_parser.py` -> метод `parse()`

**Проблема:** Метод `BeautifulSoup(content, "lxml")` работает полностью синхронно и потребляет много процессорного времени (CPU-bound) на больших страницах. Так как он вызывается прямо внутри асинхронной функции `parse()` в основном Event Loop'е, он замораживает цикл. Во время парсинга DOM ни один другой `aiohttp` запрос или асинхронная задача не могут выполняться.

**Влияние:** Падение пропускной способности приложения (снижение количества скачиваемых файлов в секунду) и задержки в обновлении UI.

**Решение:** Перенести выполнение `BeautifulSoup` в стандартный ThreadPoolExecutor.

**Код для исправления:**
```python
# В методе parse() заменить строку:
# soup = BeautifulSoup(content, "lxml")

# На следующий код:
loop = asyncio.get_running_loop()
soup = await loop.run_in_executor(None, BeautifulSoup, content, "lxml")
```

### 2.2. Утечка памяти (Memory / Socket Leak) синхронных сессий
**Локация:** `src/parser/webpage_parser.py` -> метод `_get_sync_session()`

**Проблема:** Если сайт отклоняет `aiohttp` (срабатывает защита TLS/DDoS), парсер инициализирует новый объект `requests.Session()` для выполнения fallback-запроса или обхода gateway. Эти объекты сессий никогда не закрываются (`.close()` не вызывается ни в одном месте). В результате накапливаются незакрытые TCP-соединения и происходит утечка оперативной памяти.

**Влияние:** Исчерпание файловых дескрипторов ОС («Too many open files») и разрастание потребления ОЗУ в процессе долгого сканирования.

**Решение:** Явно закрывать локальную сессию перед возвратом данных из метода `parse()`.

**Код для исправления:**
```python
# В конце метода parse() перед return (там где собираются cookies):
            cookies = None
            if self._sync_session:
                cookies = self._sync_session.cookies.get_dict()
                self._sync_session.close() # Устранение утечки ресурсов: закрываем сокеты
                self._sync_session = None

            return self.links, self.media_files, K.PARSER_SUCCESS, "Successfully parsed.", http_status_code, cookies
```

### 2.3. Загрузка ядра CPU (Busy-Waiting) в состоянии паузы
**Локация:** `src/parser/parser_manager.py` -> `_parser_worker` и `_downloader_worker`

**Проблема:** В главном цикле воркеров используется конструкция:
```python
            if self.is_paused:
                await self._pause_event.wait()
                if self._stop_event.is_set(): break
                continue
```
В функции `pause_parsing()` очистка события происходит с задержкой через `call_soon_threadsafe`. Если `self.is_paused = True` уже установлено, а событие еще не очищено, `wait()` вернет управление немедленно. Сработает `continue`, и воркер войдет в бесконечный цикл проверок (Busy-waiting), сжигая 100% мощности ядра процессора, пока очередь задач Event Loop'а не дойдет до выполнения `_pause_event.clear()`.

**Влияние:** Временное «зависание» (CPU spike) и трата ресурсов батареи при нажатии на паузу.

**Решение:** Интегрировать короткий тайм-аут сна (`asyncio.sleep`), если событие все еще активно.

**Код для исправления:**
```python
# В методах _parser_worker и _downloader_worker изменить блок паузы на:
            if self.is_paused:
                if self._pause_event.is_set():
                    await asyncio.sleep(0.1) # Защита от busy-waiting
                else:
                    await self._pause_event.wait()
                if self._stop_event.is_set(): break
                continue
```

---

**Резюме для Junior Engineer:** План содержит 6 атомарных точечных правок, которые не требуют переписывания архитектуры. Действуй строго по предоставленным фрагментам кода (копировать-вставить), чтобы предотвратить побочные эффекты. Это стабилизирует паузы/сохранения, уберет битые файлы при скачивании и радикально снизит потребление памяти.