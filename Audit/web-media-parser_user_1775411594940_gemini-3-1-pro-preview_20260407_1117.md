> **🤖 Prompt Generation Metadata**
> - **Model:** gemini-3.1-pro-preview
> - **Target Repository:** https://github.com/Gaurox/AI-Metadata-Inspector
> - **Auto-generated RAG Query:** "overlay_button_click_event_handler,cookie_persistence_between_pages,session_restart_cookie_loss,asyncio_aiohttp_media_downloader,requests_to_aiohttp_migration,media_downloader.py:synchronous_requests_bottleneck,imagenpic_photosite_fullsize_image_support,photo_hosting_api_rate_limiting,stop_load_immediate_termination,race_condition_on_repeated_download_initiation,dead_code_in_media_downloader,O(n^2)_image_processing_loops,event_emitter_memory_leak,overlay_z-index_css_issue,cookie_storage_localStorage_vs_sessionStorage,download_manager_task_cancellation_timeout,asyncio_event_loop_stall_detection"
> - **Agentic RAG Requested Files:** (0/3 fetched)
>   - `src/parser/scrapling_adapter.py`
>   - `src/downloader/media_downloader.py`
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
> #1 — Кнопка I Agree не нажимается на overlay поверх страницы #2 — Cookies не сохраняются между страницами, браузер перезапускается #3 — Stop не обрывает загрузки немедленно, Конфликты при повторном запуске #4 Отсутствие полноразмерных изображений с imagenpic и подобных сайтов-фотохостингов. Я предлагаю переписать media_downloader.py, полностью выкинув устаревший синхронный requests и переведя его на чистый aiohttp. Твое мнение и рекомендации?
> ```
> </details>

---



# Отчет об аудите кода и План рефакторинга: Web Media Parser

## 1. Резюме и Оценка предложения пользователя

**Ваше предложение:** *"Переписать `media_downloader.py`, полностью выкинув устаревший синхронный `requests` и переведя его на чистый `aiohttp`."*

**Мой вердикт: Категорически поддерживаю.** 
Использование синхронного `requests` внутри `asyncio.run_in_executor` — это серьезный антипаттерн для I/O-нагруженных приложений. Это приводит к блокировке пула потоков, перерасходу памяти, невозможности мгновенно отменить задачи (Issue #3) и проблемам с обработкой таймаутов. Переход на чистый `aiohttp` и асинхронную запись через `aiofiles` решит проблему зависаний и конфликтов при остановке парсинга на 100%.

Ниже представлен подробный план действий для младшего разработчика (Junior Developer), разбитый на решения заявленных вами проблем. План включает архитектурные изменения с минимально необходимым вмешательством в текущую бизнес-логику.

---

## 2. Подробный план устранения ошибок (Руководство для Junior Developer)

### Проблема #5 и #3: Замена requests на aiohttp и мгновенный Stop
**Описание проблемы:** Сейчас `MediaDownloader` использует `requests.get()` и читает потоки синхронно. Из-за этого вызов `stop()` (установка `_stop_event`) не прерывает текущую скачку немедленно — поток «висит» до конца чтения чанка или таймаута сокета. При перезапуске остаются зомби-потоки, вызывающие конфликты доступа к файлам (PermissionError).

**Задачи для реализации:**
1. **Файл:** `src/downloader/media_downloader.py`
   Полностью удалите импорт `requests` и перепишите метод скачивания на `async def`.
   *Пример кода для реализации:*
   ```python
   import aiohttp
   import aiofiles
   import asyncio
   from pathlib import Path
   import constants as K

   class MediaDownloader:
       def __init__(self, url, filepath, settings, media_type, source_url):
           self.url = url
           self.filepath = filepath
           self.settings = settings
           self._stop_event = None
           # ... инициализация ...

       def set_stop_event(self, event: asyncio.Event):
           self._stop_event = event

       async def download(self, session: aiohttp.ClientSession, timeout: int, retries: int) -> dict:
           attempt = 0
           while attempt < retries:
               try:
                   if self._stop_event and self._stop_event.is_set():
                       return {"success": False, "error": "Aborted by user"}

                   client_timeout = aiohttp.ClientTimeout(total=timeout)
                   async with session.get(self.url, timeout=client_timeout) as response:
                       response.raise_for_status()
                       
                       # Асинхронная запись кусками (chunked)
                       async with aiofiles.open(self.filepath, 'wb') as f:
                           async for chunk in response.content.iter_chunked(K.WRITE_BUFFER_SIZE):
                               # КРИТИЧНО: Проверка флага отмены внутри цикла скачивания!
                               if self._stop_event and self._stop_event.is_set():
                                   return {"success": False, "error": "Aborted during download"}
                               await f.write(chunk)
                               
                       return {"success": True, "filepath": self.filepath}
               except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                   attempt += 1
                   if attempt >= retries:
                       return {"success": False, "error": str(e)}
                   await asyncio.sleep(1) # Задержка перед ретраем
   ```

2. **Файл:** `src/parser/parser_manager.py` (строка ~155, метод запуска загрузчика)
   Нужно убрать использование `run_in_executor` и передать текущую aiohttp сессию в загрузчик.
   *Было:*
   ```python
   result = await asyncio.get_event_loop().run_in_executor(
       None, lambda: downloader.download(timeout=timeout_val, retries=retries_val)
   )
   ```
   *Стало:*
   ```python
   # self.session = self.async_client_manager.session (aiohttp.ClientSession)
   result = await downloader.download(
       session=self.session, 
       timeout=timeout_val, 
       retries=retries_val
   )
   ```

---

### Проблема #2: Cookies не сохраняются, браузер перезапускается
**Описание проблемы:** В текущей реализации `ScraplingWebpageParser` (адаптер Playwright) инициализируется заново для каждого нового URL. Это приводит к постоянному запуску новых инстансов браузера, потере состояния (cookies, сессии) и огромным задержкам.

**Задачи для реализации:**
1. **Создание глобального контекста Scrapling:**
   В `src/parser/parser_manager.py` необходимо инициализировать `StealthyFetcher` или браузерный контекст Playwright один раз на всю сессию парсинга.
   
   *В методе инициализации потоков `parser_manager.py`:*
   ```python
   from scrapling import StealthyFetcher

   # Оборачиваем старт воркеров в контекст браузера
   async with self.async_client_manager as session:
       self.session = session
       
       # ИНИЦИАЛИЗИРУЕМ БРАУЗЕР ОДИН РАЗ
       async with StealthyFetcher(headless=True) as shared_browser:
           self.shared_browser = shared_browser 
           
           # ... запуск parser_tasks и downloader_tasks ...
   ```

2. **Передача контекста в парсер:**
   Обновите `src/parser/scrapling_adapter.py`. Передавайте `self.shared_browser` в конструктор `ScraplingWebpageParser` и используйте его для открытия новых страниц (вкладок), а не запуска нового браузера.

---

### Проблема #1: Кнопка "I Agree" не нажимается на оверлеях
**Описание проблемы:** Playwright часто падает с таймаутом при попытке `.click()`, если целевая кнопка перекрыта прозрачным `div` или модальным окном (overlays), либо если оверлей грузится асинхронно.

**Задачи для реализации:**
1. **Файл:** `src/parser/scrapling_adapter.py`
   Добавьте жесткий метод (brute-force) для обработки согласий (consent) перед парсингом DOM. Используйте слова из `consent_keywords.txt` и принудительный клик `force=True`, а также инъекцию JS для удаления мешающих баннеров.

   *Интегрировать в логику ScraplingWebpageParser (после загрузки страницы):*
   ```python
   async def _bypass_overlays(self, page):
       # 1. Попытка принудительного клика по кнопкам согласия
       keywords = ["i agree", "accept all", "yes", "enter", "согласен"] # Можно загружать из consent_keywords.txt
       for kw in keywords:
           try:
               # Ищем кнопки и ссылки по тексту (без учета регистра)
               locator = page.locator(f"button:has-text('{kw}'), a:has-text('{kw}')").first
               if await locator.is_visible(timeout=1000):
                   # force=True игнорирует перекрытия другими элементами!
                   await locator.click(force=True)
                   await page.wait_for_timeout(500) 
                   break
           except Exception:
               pass

       # 2. Ядерный вариант: удаление всех оверлеев через JavaScript
       await page.evaluate("""
           document.querySelectorAll('.overlay, .consent, .modal, #cookie-banner, .popup').forEach(el => el.remove());
           document.body.style.overflow = 'auto'; // Возвращаем скролл
       """)
   ```
   *Вызывать `await self._bypass_overlays(page)` сразу после `page.goto(url)`.*

---

### Проблема #4: Отсутствие полноразмерных изображений с фотохостингов (imagenpic)
**Описание проблемы:** Парсер находит только миниатюры. Структура типичного фотохостинга: `<a href="link_to_full_size.jpg"><img src="thumbnail.jpg"></a>`. В текущей логике `webpage_parser.py` собираются только атрибуты `src` у тегов `<img>`.

**Задачи для реализации:**
1. **Улучшение эвристики в `WebpageParser`:**
   В `src/parser/webpage_parser.py` модифицируйте логику поиска изображений (использующую BeautifulSoup).

   *Пример алгоритма:*
   ```python
   for img_tag in soup.find_all('img'):
       src = img_tag.get('src')
       if not src:
           continue

       # ЭВРИСТИКА 1: Проверка родительского тега <a>
       parent_a = img_tag.find_parent('a')
       if parent_a and parent_a.get('href'):
           href_url = parent_a.get('href')
           # Если href ведет на медиафайл, отдаем ему приоритет!
           if any(href_url.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.mp4']):
               src = href_url 

       # ЭВРИСТИКА 2: Использование Pattern Manager
       # Очищаем или подменяем URL, если он совпадает с правилами site_patterns.json
       src = self.pattern_manager.get_full_size_url(self.domain, src)
       
       # ... добавление src в итоговый список ...
   ```

2. **Обновление `site_patterns.json`:**
   Добавьте в JSON паттерны для замены строк, если хостинги используют предсказуемые URL.
   ```json
   {
     "imagenpic.com": {
       "type": "regex",
       "replace": [{"pattern": "_thumb", "with": ""}, {"pattern": "/thumbs/", "with": "/images/"}]
     }
   }
   ```
   Это позволит модулю `pattern_manager.py` автоматически преобразовывать `http://imagenpic.com/thumbs/123_thumb.jpg` в `http://imagenpic.com/images/123.jpg`.

---

## 3. Рекомендации по оптимизации процессов и безопасности
1. Обязательно добавьте проверку статуса закрытия `aiofiles` в блоке `finally`, чтобы не оставлять залоченные битые файлы при `Stop`.
2. После удаления синхронного `requests`, вы можете безопасно удалить семафор `self._browser_semaphore` и грубый вызов `self.cancel_all_tasks()` в `parser_manager.py`, так как `_stop_event` и отмена асинхронных задач (`task.cancel()`) будут работать предсказуемо и мгновенно.