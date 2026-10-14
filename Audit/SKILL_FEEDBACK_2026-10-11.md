# Отчёт по скиллу `ext-dev-loop` (находки из прогона расширения, 2026-10-11)

Кому: агенту, который занимается скиллом и `<skill-dir>/scripts/probe.mjs` (здесь `<skill-dir>` —
каталог скилла `ext-dev-loop`, не этот репозиторий).
Откуда: проверка расширения Chrome MV3 (`extension/` репозитория web-media-parser) — 7 запусков
probe на CfT 137 (headless, scratch-профили, порты 9820–9830, Git Bash/Windows, node v24.19.0).
Все находки воспроизводимы; для каждой указано, где лежит доказательство.

## 1. Что сработало ровно как задокументировано (не жалобы, а подтверждения)

- **Драйвер-попап вместо `--msg`.** LOCAL.md предупреждает: у расширения без `options_page` `--msg`
  открывает `chrome-error` и всё падает на `chrome.runtime` — так и есть; `{"target":"ext"}` с
  `popup/popup.html` работает: из попапа `chrome.runtime.sendMessage` доходит до воркера, а клики по
  реальным кнопкам продукта (`Scan This Page`, `Save (Chrome)`) запускают штатный сценарий.
- **`--script` со шагами `page|sw|ext` + `delay`/`wait`/`waitTimeout`** — основной инструмент; на нём
  построен весь E2E (2 сценария, 10 шагов каждый).
- **Редирект загрузок в `.ext-dev-loop/downloads`** (SKILL.md §690-697) — подтверждено живьём:
  `chrome.downloads` реально складывает файлы туда (8 файлов, размеры совпали с источником).
- **Переиспользование `--profile` как «доски» между запусками** дало измерение, которое иначе не
  сделать: переживает ли `chrome.alarms` перезапуск браузера. Техника: первый запуск пишет
  `scheduledTime` в `chrome.storage.local`, второй (тот же профиль, браузер между запусками закрыт)
  читает и сравнивает. Ответ: **не переживает** (новый alarm создан за 0.78 с до чтения). Это стоит
  внести в доки как рецепт «как измерять persistence-семантику воркера».
- **`--grant-timeout 0` больше не нужен** — см. F4.

## 2. Находки

### F1 · MEDIUM (стоимость: 30 с + неверная гипотеза) · «предикат, который не компилируется, выглядит как “просто не сработало”»

**Симптом.** Шаг с `wait`, чьё выражение синтаксически неверно, молча крутится до `waitTimeout` и
печатает `wait: TIMED OUT after 30265ms` **без причины**; затем `eval` шага всё равно выполняется
(и успешно), поэтому поломка выглядит как «флакающая сеть/состояние».

**Доказательство.**
- Мой прогон: `.ext-dev-loop/e2e_run.txt`, шаг 8 — предикат
  `(await chrome.downloads.search({})).length > 0` (top-level `await` в выражении = SyntaxError) →
  `wait: TIMED OUT after 30265ms`, а следующий `eval` показал, что загрузки уже были.
- Код: `<skill-dir>/scripts/probe.mjs`, `pollOnce()` (около строки 1221) считает «мёртвым» только протокольный
  `r.error`; ошибка компиляции выражения приходит как `result.exceptionDetails` при
  `result.value === undefined` → поллер видит «ещё не true» и продолжает до таймаута.
  Сообщение на строке 1249 печатает только `TIMED OUT after …ms`.

**Предлагаемое изменение.** В `pollOnce` трактовать `res.result.exceptionDetails` так же жёстко, как
`r.error`: распечатать `exceptionDetails.exception?.description` и **сразу** провалить шаг (предикат,
который не компилируется, не может стать истинным — ждать бессмысленно). В сообщении уместна подсказка
вида «заверните в `(async () => …)()`», потому что `await` в предикате — самая частая причина.

**Скетч правки (предложение, не применено):**

```js
// probe.mjs — pollOnce() ЦЕЛИКОМ, с добавленной проверкой (предложение, не применено):
// три строки в середине — новое, остальное — как сейчас.
const pollOnce = async (session, expr, contextId) => {
  const r = await session.send("Runtime.evaluate", {
    expression: expr, ...(contextId ? { contextId } : {}), returnByValue: true, awaitPromise: true,
  });
  if (r && r.error) return { dead: true, why: r.error.message || String(r.error.code) };
  // НОВОЕ: выражение не скомпилировалось — ждать бессмысленно, это не «ещё не true»
  const ex = r?.result?.exceptionDetails;
  if (ex) return { dead: true, why: `predicate threw: ${ex.exception?.description || ex.text}` };
  return { value: r?.result?.value };
};
```

и в `pollUntil`, если `res.dead` пришёл с непустым `why` от исключения — печатать `why` и выходить
сразу, а не крутить до таймаута (в сообщении уместна подсказка про `(async () => …)()` /
недопустимый `await` вне async).

**Как проверить фикс:** один шаг со `wait: "(await 1) > 0"` — ожидаем мгновенный FAIL с текстом
`SyntaxError`, а не таймаут.

### F2 · MEDIUM · «`instances.mjs` видит утечку только на первых четырёх портах»

**Симптом.** Скилл сам предписывает после `--script` проверять `instances.mjs`, потому что браузер
может пережить прогон (SKILL.md, hazard #10/#11; LOCAL.md — про hard timeout). Но проверяются ровно
4 порта: `reserved-ports` + 0..3, плюс порты из реестра `--keep`. Прогон на любом другом порту
резервированного диапазона (у меня 9820–9830 из 9793–9831) утечку не покажет.

**Доказательство.**
- Код: `<skill-dir>/scripts/instances.mjs` — `const PORTS = [defaultPort(0), defaultPort(1), defaultPort(2), defaultPort(3)];`
  (плюс `known` из реестра).
- Вывод инструмента во всех моих проверках: `port 9793..9796: no instance (ECONNREFUSED)` — про
  фактически использованные порты 9820–9830 ни слова.

**Предлагаемое изменение.** Сканировать весь диапазон `reserved-ports` (параллельно, с коротким
таймаутом 700–1000 мс на порт), либо — точнее — записывать в реестр **каждый** запуск, включая
`--script` (сейчас туда попадают только `--keep`), тогда множество портов для проверки точное.

**Прототип фикса (код целиком, самодостаточно):** сканирует весь диапазон параллельно
(39 портов х 900 мс таймаут = один быстрый прогон), границы читает прямо из `LOCAL.md`
(`reserved-ports:`). Замер в этом сеансе: `scanned 9793-9831: 0 ALIVE of 39` — время работы
практически не отличается от проверки четырёх портов, а утечек не осталось.

```js
// ports_scan.mjs — замена/дополнение к scripts/instances.mjs; node >= 22 (fetch, AbortSignal.timeout)
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

let first = Number(process.argv[2]) || 0, last = Number(process.argv[3]) || 0;
if (!first) {   // границы резерва — факт машины, читаем из LOCAL.md, как это уже делает instances.mjs
  try {
    const m = /^reserved-ports:\s*(\d+)-(\d+)$/m.exec(
      fs.readFileSync(path.join(os.homedir(), ".ext-dev-loop", "LOCAL.md"), "utf8"));
    if (m) { first = Number(m[1]); last = Number(m[2]); }
  } catch {}
}
if (!first) { first = 9793; last = 9831; }

const ports = [];
for (let p = first; p <= last; p++) ports.push(p);

const results = await Promise.all(ports.map(async (p) => {
  try {
    const r = await fetch(`http://127.0.0.1:${p}/json/version`, { signal: AbortSignal.timeout(900) });
    const v = await r.json();
    return { p, alive: true, browser: v.Browser || "?" };
  } catch (e) {
    return { p, alive: false };
  }
}));

const alive = results.filter((r) => r.alive);
console.log(`scanned ${first}-${last}: ${alive.length} ALIVE of ${ports.length}`);
for (const r of alive) console.log(`  ALIVE port ${r.p}: ${r.browser}`);
```

(В моём репозитории этот файл лежит в скретче `.ext-dev-loop/ports_scan.mjs` — он не коммитится;
поэтому код приведён прямо здесь.)

**Как проверить фикс:** запустить probe `--script` с умышленным «hard timeout» на порту 9828 и
убедиться, что `instances.mjs` его называет (раньше — молчание).

### F3 · LOW–MEDIUM · «свежий профиль: стартовая работа расширения гоняется с первыми шагами драйвера»

**Симптом.** Шаг, который пишет в `chrome.storage.local` и сразу читает обратно, получает **чужое**
значение: расширение в этот момент ещё выполняет своё `onInstalled`/cold-start инициализацию и пишет
поверх. Симптом выглядит как «мой тест не сработал», хотя тест писал правильно.

**Доказательство.** Гейт R-E-8, прогон 1: шаг записал 2 правила, прочитал — вернулся встроенный набор
823 правила; worker-console этого шага показал три строки `Loaded 823 sieve rules into storage …`,
пришедшие **во время** шага (то есть это были стартовые записи, а не моя). После добавления
settle-шага (`await loadSieveRules(); await sleep(1500)` первым делом) три последующих прогона —
зелёные и детерминированные: `.ext-dev-loop/e2e_run3.txt`, `Audit/evidence_2026-10-11/extension_gate_2026-10-11.txt`.

**Предлагаемое изменение (доки, LOCAL.md/SKILL.md).** «На свежем профиле первые ~2 секунды расширение
само пишет в storage: перед проверками состояния вставляйте settle-шаг; он же делает вывод
worker-console однозначным».
**Смежное (косметика харнесса).** Консоль воркера печатается порциями «на шаг», а про повторы есть
только пометка `(N replayed line(s) suppressed)` — поэтому стартовая запись, попавшая в окно шага,
читается как часть шага. Полезно либо помечать «startup replay» явно, либо печатать строки с
относительным временем (`+1234ms`).

### F4 · LOW (доки устарели, фикс уже есть) · warning в LOCAL.md про `--grant-timeout` больше не воспроизводится

**Симптом/факт.** LOCAL.md: «The userScripts grant gate fires for extensions that never declare
`userScripts` … spurious exit 1; `--grant-timeout 0` skips the wait». Проверил специально **без**
флага: прогон завершился `exit 0`, печатая
`userScripts not declared in the manifest - the grant gate does not apply;`
(`.ext-dev-loop/grant_check_run.txt`, шаг 1 отдал `{"version":"1.3.1","userScriptsDeclared":false}`).

**Предлагаемое изменение.** Обновить LOCAL.md: гейт больше не ждёт и не портит код возврата для
расширений без `userScripts`; `--grant-timeout 0` остаётся только как страховка. (Побочно этот прогон
подтвердил, что загруженное из релизного zip расширение действительно 1.3.1.)

## 3. Что стоило бы добавить в доки (не баг, а рецепт) — «E2E со скачиванием без интернета»

Всё, что нужно, в скилле есть, но рецепта нет; ниже — то, что у меня заработало, с двумя ловушками:

1. Локальная фикстура: `python -m http.server 9811` в каталоге с `index.html` и картинками; страница
   открывается шагом `{"target":"page","url":"http://127.0.0.1:9811/index.html","delay":2500}`.
2. **Ловушка №1: картинки меньше 50 px продукт отфильтрует** (backstop против иконок) — фикстура из
   1×1-заглушек даёт пустой скан и «доказывает» ложный дефект. Делайте реальные ≥50 px (у меня — 800×600).
3. **Ловушка №2: попап, открытый пробой как вкладка, — не попап над страницей.** Продукт ищет
   «активную вкладку» (`chrome.tabs.query({active:true,currentWindow:true})`) и, если активна вкладка
   самого попапа, считает результат устаревшим и удаляет его. Перед кликом по кнопкам попапа надо
   активировать целевую вкладку: шаг `target:"sw"` →
   `(async () => { const t = (await chrome.tabs.query({})).find(x => x.url.includes("127.0.0.1:9811")); await chrome.tabs.update(t.id, {active:true}); })()`.
4. Скачивание: шаг `target:"ext"` → `document.getElementById("chrome-download-btn").click()`, затем
   проверка на диске в `.ext-dev-loop/downloads` (это надёжнее, чем `chrome.downloads.search`, и не
   требует `wait` вовсе).
5. Разбор результата: шаг `target:"sw"` читает `chrome.storage.local.get("scanResult")` — то же,
   что видит попап.

## 4. Чего я НЕ проверял (границы честности отчёта)

Firefox-бэкенд (`--browser firefox`), `--keep`/`--attach`, handover-режимы (`ask`/`--ask-keep`),
`--in isolated` для чужих world'ов, поведение после настоящего hard timeout (не воспроизводил),
порт-конфликты (`EADDRINUSE`) не моделировал.
