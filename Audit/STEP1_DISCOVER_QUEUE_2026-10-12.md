# Шаг 1: снятие контентного капа и приоритетная очередь по origin (2026-10-12)

Расширение, discovery-путь. Десктоп не тронут, Python не тронут.

## Что изменено

| Файл | Было | Стало |
|---|---|---|
| `extension/shared.js` | `const LINKS_CAP = 50;` | удалён; добавлен `DISCOVER_QUARANTINE_MS = 15000` с замером в доккомментарии |
| `extension/background.js` (handleStartScan) | `progress.total = Math.min(links.length, LINKS_CAP)` и `discoverFullsize(response.links.slice(0, LINKS_CAP), …)` | `progress.total = links.length`, в discovery уходит **весь** список |
| `extension/background.js` (commandScanAndProcess) | тот же slice | весь список |
| `extension/background.js` (`discoverFullsize`) | цикл «кусками по 5 в порядке списка, break по дедлайну 45 s» | одна очередь с приоритетом по здоровью **origin**: ссылки не провалившихся host'ов идут первыми, провалившийся origin **откладывается** (не блокируется) в хвост и обрабатывается последним под `DISCOVER_QUARANTINE_MS` |
| `extension/background.js` (`processLink`) | `if (!resp.ok) return [];` и `catch → []` — все сбои неразличимы | abort/таймаут → признак host'а; `429`/`5xx` → признак host'а; `404` и «страница без медиа» → только эта ссылка. Ключ — `new URL(u).origin` (не `hostname`: теряется порт) |
| `extension/content_script.js`, `extension/popup/popup.js`, `extension/tests/page_image_classification.test.mjs` | комментарии про «обрезку до LINKS_CAP» | переписаны: порядок важен, обрезки нет |
| `extension/tests/discover_queue.test.mjs` | — | новый: 15 проверок очереди, классификации сбоев и ключа с портом |

Ограничители, которые **не** менялись и остаются единственными: дедлайн страницы 45 s,
таймаут одного зонда 8 s, конкурентность 5.

## Гейты (все прогоны — Chrome for Testing 137, headless, `probe.mjs`)

| Гейт | Метрика | Результат |
|---|---|---|
| Юнит-тесты расширения | `node extension/tests/*.test.mjs` | **12/12 ALL PASS** (было 11) |
| Новый тест умеет падать (мутации) | `m404` — 404 тоже демотирует origin; `mhost` — ключ `hostname` вместо `origin` | **2 и 7 проверок падают** соответственно; на исправленном коде — зелёный |
| 120 медиа-ссылок (`.ext-dev-loop/cap120`) | было 50/120 | **120/120**, `liveMissing: 0`, скан 621 ms (локально) |
| 60 висящих + 120 живых (`.ext-dev-loop/cap180`, TCP-тарпит) | было **0/120** за 48 s | **120/120**, все результаты за **8.7 s**, скан закрыт на **16.8 s** (`50 link(s) on postponed origins left unprobed`) |
| Полный E2E продукта (`.ext-dev-loop/e2e_steps.json`) | база: 8 элементов, fullsize 5/5, 8 загрузок `complete` | **8 элементов, fullsize 5/5, all sources 8/8, 8 загрузок `complete`**, попап без ошибок |
| Живая галерея e-hentai `/g/4249104/719e4d495b/` | база: Full size 21/21, `thumbsInFullsize ≤ 1` | **21/21 selected**, `fullsizeCount: 21`, `thumbsInFullsize: 1` (ротирующийся баннер), скан 4081 ms (база 3433–4358 ms) |

Логи: `.ext-dev-loop/review_2026-10-12/gate2_cap120.txt`, `gate3_ordering.txt`, `gate4_e2e.txt`,
`gate5_live.txt`.

## Что изменилось как следствие (не регресс)

На живой галерее «All sources» выросло с 309 до **317**, а бакет Thumbnails с 288 до **296**:
кап раньше отрезал 10 ссылок хвоста, теперь они зондируются, и их картинки (страницы-сетки)
попадают в Thumbnails, как и задумано. Бакет Full size не изменился — 21.

## Осознанный компромисс и что осталось

1. **Объём запросов теперь ограничен только дедлайном**, не количеством: страница с сотнями
   медиа-ссылок за один клик отработает до 45 s. Так уже ведёт себя десктоп
   (`constants.py`: «media lookups are never content-capped … guards are RESOURCE-based only»),
   а per-origin конкурентность и backoff на 429 — это шаг 2.
2. Не тронуто (известные остатки): финальный прогресс всё ещё пишется как
   `{scanned: 1, total: 1}` (`background.js`, строка с `scanProgress: { status: "done" … }`),
   поэтому после завершения счётчик «x/y страниц» неинформативен; кэша URL→результат между
   сканами нет; в Full size остаётся один ротирующийся баннер сайта (свойство эвристики
   «одна картинка на странице = оригинал»).
3. Строка в `PLAN.md` («hard-capped at LINKS_CAP=50 … to bound the 45s network budget») теперь
   неверна — исторический документ оставлен как есть, актуальное состояние здесь.
4. Не запускалось: `pytest` (Python не тронут), десктопные пути.

## Воспроизведение

```
python .ext-dev-loop/cap120/make.py && python .ext-dev-loop/cap180/make.py
python -m http.server 9829 --directory .ext-dev-loop/cap120     # гейт «120 ссылок»
python .ext-dev-loop/cap180/tarpit.py                           # гейт «60 висящих + 120 живых»
node extension/tests/discover_queue.test.mjs
python .ext-dev-loop/mutate_and_check.py                        # доказательство, что тест падает
node <probe.mjs> --ext extension --port 9826 --headless --script .ext-dev-loop/cap180_steps.json
```
