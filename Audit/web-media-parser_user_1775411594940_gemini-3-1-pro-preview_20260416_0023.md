> **🤖 Prompt Generation Metadata**
> - **Model:** gemini-3.1-pro-preview
> - **Target Repository:** Local Folder
> - **Auto-generated RAG Query:** "JobQueue, TaskStatus, pauseParsing, resumeParsing, statePersistence, checkpointing, serialization, processedFiles, offset, cursor, idempotency, restoreState, workerThread, eventLoop, storageAdapter"
> - **Agentic RAG Requested Files:** (3/3 fetched)
>   - `web-media-parser/src/gui/main_window.py`
>   - `web-media-parser/src/parser/parser_manager.py`
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
>
> <details><summary><b>Additional Context</b></summary>
> 
> ```text
> Сосредоточься на очереди заданий, паузе и возобновлении парсинга. Восстановление состояния при resume, почему при resume парсинг начинается заново и загружаются те же файлы.
> ```
> </details>

---



# Аудит кода: Web Media Parser (Очередь, Pause/Resume, Управление состояниями)

## 1. Идентификация дефектов и ответ на главный вопрос

**Главный вопрос:** *Почему при resume парсинг начинается заново и загружаются те же файлы?*

Проведенный аудит выявил цепочку критических логических ошибок в архитектуре взаимодействия `MainWindow` и `ParserManager`. Состояние сессии **никогда не восстанавливается** из-за "мертвого" `asyncio` Event Loop в главном потоке и агрессивного затирания данных при старте.

### 🔴 Дефект 1: "Мертвый" Event Loop и молчаливый сбой `load_state`
В файле `src/gui/main_window.py` (метод `_launch_parser_for_task`) вызывается код:
```python
if not self.loop.is_running():
    asyncio.run_coroutine_threadsafe(self.parser_manager.load_state(state_path), self.loop)
```
**Причина сбоя:** Цикл `self.loop` инициализирован в GUI-потоке (`asyncio.new_event_loop()`), но **никогда не запускается** через `run_forever()`. Метод `run_coroutine_threadsafe` просто помещает задачу в очередь остановленного цикла. Корутина `load_state` не выполняется, ошибки не логируются.
**Следствие:** Парсер всегда стартует с пустыми списками `processed_urls` и `downloaded_files`.

### 🔴 Дефект 2: Затирание восстановленных очередей
Даже если бы `load_state` сработал, вызов `start_parsing()` в QThread (в файле `src/parser/parser_manager.py`) безусловно пересоздает все очереди с нуля:
```python
self.url_queue = PriorityURLQueue(settings=self.settings)
self.download_queue = asyncio.Queue()
```
Это полностью удаляет любые восстановленные данные.

### 🔴 Дефект 3: Безусловный старт с корня сайта
В методе `_main_task` (`parser_manager.py`) есть код:
```python
await self.url_queue.put(self.start_url, 0, self.start_url, ...)
```
Он выполняется всегда. Так как `processed_urls` пуст (из-за Дефекта 1), парсер снова начинает обработку с корневого URL и обходит весь сайт заново, ставя в очередь загрузки те же файлы (которые `MediaDownloader` затем скачивает и переименовывает, добавляя `_1`, `_2`).

### 🔴 Дефект 4: Race Condition при сохранении состояния (Pause)
При нажатии на "Паузу" (`toggle_pause`) программа сначала вызывает `save_state()`, а только потом `stop_parsing()`. Это означает, что состояние сохраняется "на лету", пока воркеры активно изменяют очереди (`_queue.pop()`, `_queue.put()`). Это приводит к потере данных и ошибкам `RuntimeError` из-за изменения размера словаря/очереди во время итерации.

### 🔴 Дефект 5: Зависание при закрытии окна (Dead Code)
В `closeEvent` используется `QEventLoop` и `asyncio.ensure_future(save_and_stop())`. Так как нативный цикл asyncio не работает, приложение либо не сохраняет состояние, либо зависает в памяти.

---

## 2. Влияние на производительность (Performance Impact)
- **Утечка пропускной способности (Bandwidth Waste):** Многократное скачивание одних и тех же гигабайтов данных при каждом возобновлении задачи.
- **Пустая трата CPU:** Повторный рендеринг HTML, поиск по регулярным выражениям и обход защит для уже обработанных страниц.
- **Утечка дискового пространства:** Оставленные `.part` файлы от прерванных потоков загрузки (из-за использования `stop_parsing` без предварительного `pause_parsing`).

---

## 3. Actionable Recommendations: План устранения для Junior-агента

План составлен по принципу **минимального вмешательства**. Архитектура не меняется, исправляется только логика синхронизации и жизненного цикла.

### Шаг 1: Перенос `load_state` внутрь Event Loop парсера
*Файл: `src/gui/main_window.py`*

Найди метод `_launch_parser_for_task` и **УДАЛИ** этот неработающий блок:
```python
# УДАЛИТЬ СЛЕДУЮЩИЕ СТРОКИ:
# state_path = self.task_queue.get_state_file_path(task.id)
# if os.path.exists(state_path):
#     self.log_handler.info(f"Loading state from {state_path}")
#     if not self.loop.is_running():
#         asyncio.run_coroutine_threadsafe(
#             self.parser_manager.load_state(state_path), self.loop
#         )
```

### Шаг 2: Безопасная инициализация и восстановление состояния
*Файл: `src/parser/parser_manager.py`*

Найди метод `_main_task` и измени начало метода, чтобы он сам загружал состояние (теперь он находится внутри правильного, запущенного Event Loop) и добавлял стартовый URL **только если это новая задача**:

```python
    async def _main_task(self):
        # 1. Reset PriorityURLQueue's asyncio primitives
        self.url_queue.reset_async_primitives()

        # 2. ЗАГРУЖАЕМ СОСТОЯНИЕ ИЗНУТРИ EVENT LOOP
        await self.load_state(self.download_path)

        # 3. ДОБАВЛЯЕМ СТАРТОВЫЙ URL ТОЛЬКО ЕСЛИ СОСТОЯНИЕ НЕ ВОССТАНОВЛЕНО
        # Если processed_urls пуст - значит это абсолютно новая сессия
        if len(self.processed_urls) == 0:
            await self.url_queue.put(
                self.start_url, 0, self.start_url,
                {"is_start_url": True, "start_url": self.start_url}
            )

        # Create the shared requests.Session... (оставь остальной код без изменений)
        shared_dl_session = create_shared_downloader_session(self.settings)
        # ...
```

### Шаг 3: Исправление Race Condition при паузе (Сначала пауза, потом сейв)
*Файл: `src/gui/main_window.py`*

В методах `toggle_pause` и `_pause_current_for_switch` нужно сначала дать команду рабочим потокам "замереть" (поставить на паузу), дождаться их остановки и только потом сохранять состояние.

Внеси изменения в `toggle_pause`:
```python
    def toggle_pause(self):
        # ... (начало метода без изменений до блока "1. Save State")
        
        self.log_handler.info(f"Pausing task {active.id}...")
        self.status_bar.showMessage("Saving state before pausing...")

        state_path = self.task_queue.get_state_file_path(active.id)

        # ИСПРАВЛЕНИЕ: Сначала мягкая пауза воркеров (останавливает движение данных в очередях)
        self.parser_manager.pause_parsing()

        # ИСПРАВЛЕНИЕ: Сохраняем состояние замороженных очередей
        try:
            if self.parser_manager.loop and not self.parser_manager.loop.is_closed():
                future = asyncio.run_coroutine_threadsafe(
                    self.parser_manager.save_state(state_path), self.parser_manager.loop
                )
                future.result(timeout=15) # Внутри save_state парсер сам подождет пока in-flight задачи завершатся (до 5 сек)
                self.log_handler.info(f"State saved for {active.id}")
        except Exception as e:
            self.log_handler.error(f"Error saving state on pause: {e}")

        # ИСПРАВЛЕНИЕ: Теперь жестко останавливаем потоки
        self.parser_manager.stop_parsing()
        
        # ... (конец метода без изменений)
```
*(Примени точно такую же логику порядка действий в методе `_pause_current_for_switch`)*.

### Шаг 4: Починка зависания при закрытии приложения (CloseEvent)
*Файл: `src/gui/main_window.py`*

Метод `closeEvent` полностью сломан, так как зависит от неработающего локального цикла. Перепиши его полностью, чтобы он работал синхронно и безопасно:

```python
    def closeEvent(self, event):
        """Handle window close event — save queue and active task state."""
        queue_path = os.path.join(self.download_dir, "task_queue.json")
        try:
            self.task_queue.save(queue_path)
        except Exception as e:
            self.log_handler.error(f"Error saving queue state: {e}")

        pm = self.parser_manager
        if pm and pm.is_running:
            if not pm.is_paused:
                self.status_bar.showMessage("Stopping task and saving state…")
                # 1. Замораживаем парсер
                pm.pause_parsing()
                # 2. Сохраняем стейт активной таски
                try:
                    active = self.task_queue.active_task
                    if active:
                        state_path = self.task_queue.get_state_file_path(active.id)
                        if pm.loop and not pm.loop.is_closed():
                            future = asyncio.run_coroutine_threadsafe(
                                pm.save_state(state_path), pm.loop
                            )
                            future.result(timeout=10)
                except Exception as e:
                    pass
                # 3. Убиваем потоки
                pm.stop_parsing()
                if self.parser_thread and self.parser_thread.isRunning():
                    self.parser_thread.quit()
                    self.parser_thread.wait()
            else:
                # Если уже на паузе, стейт уже сохранен, просто убиваем
                pm.stop_parsing()
                if self.parser_thread and self.parser_thread.isRunning():
                    self.parser_thread.quit()
                    self.parser_thread.wait()
                    
        event.accept()
```
*Также удали мертвый метод `_save_session_async(self)` в самом низу класса `MainWindow`, он больше не используется и содержит устаревшую логику сохранения в общий файл.*