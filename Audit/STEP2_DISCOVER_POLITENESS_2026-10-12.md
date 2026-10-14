# Шаг 2: кэш результатов между сканами + вежливость к хостам (2026-10-12)

Расширение, discovery-путь (`extension/background.js`, `extension/shared.js`).
Десктоп не тронут, Python не тронут.

Продолжение [`STEP1_DISCOVER_QUEUE_2026-10-12.md`](STEP1_DISCOVER_QUEUE_2026-10-12.md):
шаг 1 убрал контентный кап и научил очередь отличать сбой host'а от «на странице нет медиа»;
шаг 2 отвечает на два оставшихся вопроса — **сколько запросов одновременно уходит одному хосту**
и **зачем сканировать одну и ту же страницу дважды**.

## Что изменено

| Файл | Было | Стало |
|---|---|---|
| `extension/shared.js` | только `DISCOVER_QUARANTINE_MS` | + `DISCOVER_PER_ORIGIN = 2`, `DISCOVER_BACKOFF_MS = 2000` / `DISCOVER_BACKOFF_MAX_MS = 30000`, `DISCOVER_CACHE_TTL_MS = 900000` / `DISCOVER_CACHE_MAX = 300` / `DISCOVER_CACHE_KEY` |
| `extension/background.js` | — | `loadDiscoverCache` / `saveDiscoverCache` (все обращения к storage в `try`, при любой ошибке кэш просто пуст) |
| `extension/background.js` (`discoverFullsize`) | один список `seen`, зонды «кусками» по глобальной конкурентности | кэш-проход первым (попадание = ноль запросов, ноль слота вежливости), затем очередь с `eligible()`: **не больше `DISCOVER_PER_ORIGIN` зондов к одному origin в одном куске**, пауза `backoffUntil` и экспоненциальный `strikes` на последовательных сбоях host'а; при пустом куске скан **ждёт** ближайший backoff, а не долбит |
| `extension/background.js` (`processLink`) | `processLink(linkUrl)` | `processLink(linkUrl, report)` — report на зонд: `hostFailed` / `throttled` / `cacheable`. В кэш кладётся **только HTTP 200**; таймаут, 429 и 5xx не запоминаются как «здесь нет медиа» |
| `extension/tests/discover_queue.test.mjs` | 15 проверок (шаг 1) | 34 проверки: сверка реальных значений констант из `shared.js`, лимит на origin, глобальный кап через границы origin, ожидание backoff, кэш (TTL, cap, «сбой не кэшируется», битый кэш деградирует в зондирование) |
| `extension/tests/discover_referer.test.mjs` | — | **починка теста**, который сломал шаг 2: он вырезал `async function processLink(linkUrl) {` точной строкой, а функция получила второй параметр. Якорь заменён на `/async function processLink\([^)]*\) \{/`, `report` инжектируется явно |

Границы, которые **не** менялись: дедлайн страницы 45 s, таймаут зонда 8 s,
`DISCOVER_CONCURRENCY` (глобальный cap), `DISCOVER_QUARANTINE_MS` из шага 1.

## Гейты (все прогоны — Chrome for Testing 137, headless, `probe.mjs`)

| Гейт | Метрика | Результат |
|---|---|---|
| Юнит-тесты расширения | `node extension/tests/*.test.mjs` | **216/216 ok, 12 файлов, rc=0** (в `discover_queue` — 34 проверки, было 15) |
| Новый тест умеет падать (мутации) | `.ext-dev-loop/mutate_and_check.py` | **6/6 ловятся**: `m404`, `mhost`, `morder`, `mnocache`, `mperorigin`, `mbackoff` — каждый даёт exit=1 с падением именно своей проверки |
| Починенный тест всё ещё кусается | `.ext-dev-loop/review_2026-10-12/mutate_referer.py` | с удалённым `Referer` — rc=1, на исходнике — ALL PASS |
| **Вежливость и кэш на проводе** (фикстур `.ext-dev-loop/review_2026-10-12/s2cache`: 120 медиа-ссылок, **один** origin, счётчики на самом сервере) | 1-й скан, холодный кэш | **120/120** `linked-img`, сервер: `maxConcurrent = 2` (= `DISCOVER_PER_ORIGIN`, было бы 5), `view` = 120 запросов, 120 записей кэша, 634 ms |
| то же, 2-й скан той же страницы | после прогрева кэша | **120/120** те же результаты, серверные счётчики **пусты** (`counts {}`, `maxConcurrent 0`) — **ноль запросов**, 312 ms |
| Регрессия порядка (`.ext-dev-loop/cap180`: 60 висящих + 120 живых, TCP-тарпит) | было на шаге 1: 120/120 за 8.7 s, скан закрыт на 16.8 s | **120/120**, все результаты за **8.7 s**, скан закрыт на **12.2 s**, с висящего host'а 0 результатов, сервер: `view` = 120, `maxConcurrent = 2` |
| Полный E2E продукта (`e2e_steps.json`) | база: 8 элементов, fullsize 5/5, 8 загрузок `complete` | **8 элементов, fullsize 5/5, all sources 8/8, 8 загрузок `complete`**, попап без ошибок |
| Живая галерея e-hentai `/g/4249104/719e4d495b/` | база: Full size 21/21 | **21/21 selected**, `fullsizeCount: 21`, `bySource` = `linked-img 21 / page-img 294`, скан **7164 ms** |

Логи: `.ext-dev-loop/review_2026-10-12/logs/s2_cache_run.txt`, `s2_order_run.txt`,
`s2_e2e_run.txt`, `s2_eh_run.txt`.

Попадание в кэш видно и в юнит-слое (`a second scan of the same page costs no network`), и на
проводе: во втором скане счётчики сервера пусты, то есть экономия настоящая, а не «внутри
мока».

## Осознанные компромиссы и что осталось

1. **Кэш живёт в `chrome.storage.session`** — это переживает перезапуск service worker'а, но не
   браузера. Так и задумано: между сессиями правило «страница изменилась» неизвестно, а TTL
   в 15 минут не спасает от вчерашней страницы.
2. **Второй скан мог устареть**: если страница изменилась в пределах TTL, «Scan again» отдаст
   старые результаты. Это цена того, что повторный скан бесплатен; сбрасывается закрытием
   браузера, истечением TTL или вытеснением по `DISCOVER_CACHE_MAX`.
3. **Висящий origin по-прежнему съедает до 15 s** (`DISCOVER_QUARANTINE_MS`), и 58 ссылок
   остаются непрозондированными — это механика шага 1, а не регресс шага 2.
4. В Full size остаётся **один ротирующийся баннер сайта** (`https://ehgt.org/b/2026-10/13.webp`):
   он приходит как `linked-img` с одной из зондированных страниц (эвристика «одна картинка на
   странице = оригинал»). Было ровно так же до шага 2 (`gate5_live.txt`, `thumbsInFullsize: 1`).
5. **Финальный прогресс всё ещё пишется как `{scanned: 1, total: 1}`** (`background.js`, ветка
   `status: "done"`) — остаток шага 1, шаг 2 его не касается. Во время скана счётчик честный
   (`scanned`/`total` = ссылки), но по завершении сбрасывается в 1/1.
6. Рабочее дерево содержит **не относящуюся к шагу 2** правку Firefox (`manifest.json`:
   `background.scripts` + `browser_specific_settings.gecko`, `background.js`: `importScripts`
   под `typeof`-guard) — она из другого пункта той же сессии.
7. Не запускалось: `pytest` (Python не тронут), десктопные пути.

## Воспроизведение

```
python .ext-dev-loop/review_2026-10-12/make_s2cache.py      # фикстур: 120 ссылок одного origin
python .ext-dev-loop/cap180/conc_server.py 9829 .ext-dev-loop/review_2026-10-12/s2cache
node <probe.mjs> --ext extension --profile .ext-dev-loop/s2/profile --headless \
     --script .ext-dev-loop/review_2026-10-12/s2_cache_steps.json

python .ext-dev-loop/cap180/tarpit.py                        # регрессия порядка
python .ext-dev-loop/cap180/conc_server.py 9829 .ext-dev-loop/cap180
node <probe.mjs> --ext extension --profile .ext-dev-loop/s2b/profile --headless \
     --script .ext-dev-loop/cap180_steps.json

python -m http.server 9811 --directory .ext-dev-loop/fixture   # полный E2E продукта
node <probe.mjs> --ext extension --profile .ext-dev-loop/s2c/profile --headless \
     --script .ext-dev-loop/e2e_steps.json

for f in extension/tests/*.test.mjs; do node "$f" || echo "FAILED $f"; done
python .ext-dev-loop/mutate_and_check.py
python .ext-dev-loop/review_2026-10-12/mutate_referer.py
```
