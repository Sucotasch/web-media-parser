# Errors.txt #3: попап после переоткрытия игнорировал фильтр (2026-10-12)

Расширение, `extension/popup/popup.js` (одна правка на две строки). Десктоп/Python не тронуты.

## Симптом (из Errors.txt)

> Если после сканирования попап закрывается, после повторного открытия в нём сбой: всё ещё
> выбрано «full size», но в загрузку попадают все изображения (до закрытия Save 141, после —
> Save 429). Возврат к правильным значениям, если поменять full size → all sources → full size.

## Причина

`popup.html` держит `<option value="fullsize" selected>`, а `popup.js` фильтрует по своим
переменным `activeDomainFilter` / `activeSourceFilter`, которые **инициализировались пустой
строкой** («все источники»). Заполнялись они только в трёх местах: обработчик `change` у селектов,
обработчик клика Scan и фолбэк `applyScanResult` («нет ни одного fullsize-источника»).

Повторно открытый попап идёт путём `restoreScanState()` → `applyScanResult()` → `renderMediaList()`
и `updateCount()`, то есть **до** любого `change`; переменная ещё `""`, а селект уже «Full size».
Список и счётчик «N / N selected» показывали все источники, пока пользователь не дёрнет селект —
его `change` наконец копировал видимое значение.

## Правка

```js
// Errors.txt #3 (popup closed and re-opened): ... the DOM select is what the user sees —
// start from it, and `change` keeps the pair in sync afterwards.
activeDomainFilter = domainFilter.value;
activeSourceFilter = sourceFilter.value;
```

DOM-селект — источник правды для стартового состояния; `change` синхронизирует пару дальше.

## Гейт (Chrome for Testing 137, headless, `probe.mjs`)

Фикстур `.ext-dev-loop/fixture/popup-reopen.html`: 12 обычных `<img>` (НЕ fullsize) + 3 прямые
ссылки на файл (`a-link`, fullsize) = 15 всего, 3 в Full size. Шаги —
`.ext-dev-loop/review_2026-10-12/popup_reopen_steps.json`, лог — `…/logs/pr_fixed_run2.txt`.

| Момент | Селект | Строк | Счётчик |
|---|---|---|---|
| После скана | `fullsize` | **3** | `3 / 3 selected` |
| **После закрытия+открытия** | `fullsize` | **3** | `3 / 3 selected` |
| Переключить на All sources и назад | `fullsize` | 15 → **3** | `15 / 15` → `3 / 3` |

«Закрытие+открытие» — реальная перезагрузка документа попапа: вкладка-попап называет свой id сама
(`chrome.tabs.getCurrent()`), SW перезагружает её (`chrome.tabs.reload`), а ожидание сделано
**edge-triggered** — метка `window.__preopenMark`, выставленная до перезагрузки, обязана исчезнуть
(`freshDocument: true`, контекст исполнения сменился 1 → 2).

## Доказательство, что гейт кусается

`.ext-dev-loop/review_2026-10-12/mutate_popup_filter.py` — удаляет обе строки правки, гоняет те же
шаги, возвращает файл и сверяет sha256 до/после:

```
sha256 before = d852cc00e7ef028a7603f9bfadb6e1e5eb1b4df16ccd9847e746d58ff826df00
sha256 after  = d852cc00e7ef028a7603f9bfadb6e1e5eb1b4df16ccd9847e746d58ff826df00
RESTORED OK (byte-identical)
--- mutant run, REOPEN-8:
{"phase":"after-reopen","freshDocument":true,"select":"fullsize","rows":15,
 "count":"15 / 15 selected","chromeCount":"15","storageAll":15,"storageFullsize":3, "urls":[...15...]}
mutant run exit code: 0
```

То есть без правки гейт показывает ровно описанный баг (метка «full size», а в выборке все 15 при
3 полноразмерных в storage), а прогон всё равно завершается кодом 0 — это, кстати, тот же
недостаток харнесса, что описан в `Audit/SKILL_FEEDBACK_2026-10-12.md` §7 (H1: нет шага-утверждения).

Сьют расширения после правки: **216/216 ok, rc=0** (12 файлов).

## Что нашлось по дороге (для того, кто будет писать такие гейты)

1. `chrome.tabs.query({})` из SW **не отдаёт URL extension-страниц** (нет `tabs`-permission): у
   вкладки попапа `url: null`. Первая версия гейта по этой причине ничего не перезагружала, а шаг
   «дождаться результатов» проходил за 1 мс по СТАРОМУ DOM. Лечится `chrome.tabs.getCurrent()` из
   самого попапа (id вкладки не требует разрешений) и edge-triggered ожиданием.
2. «Дождаться терминального состояния» в браузерных гейтах небезопасно: состояние прошлого прогона
   уже терминальное. Ожидание должно ждать **исчезновения** старого маркера/начала нового состояния,
   иначе гейт зелёный, ничего не измерив.

## Заодно исправлены #1 и #2 из того же Errors.txt (по решению владельца)

**#1 «Save (Chrome) всегда серая → кажется неактивной» → сделана зелёной.**
Замерено до правки: кнопка **включена** (`disabled: false`), но покрашена `rgb(66,66,66)`
(`.secondary`), рядом `Download` — `rgb(25,118,210)` (`.primary`), а выключенное состояние —
`#333/#777`, то есть включённая и выключенная серые почти неразличимы. Теперь у неё свой класс
`save-green`; замерено после: `cls: "save-green"`, `bg: rgb(46,125,50)`, `color: #fff`,
`disabled: false`; `Download` остался синим primary (иерархия сохранена).

**#2 «Кнопки прыгают, два левых элемента исчезают» → строка больше не перестраивается.**
Замерено до правки (подставлял те же строки фаз, что были в отчёте владельца):

| Подпись Save | Высота строки | Кнопки | `Page only` | `Parallel:` |
|---|---|---|---|---|
| `Save (Chrome) 3` | 41 | left 230 | left 14 | left 109 |
| `Save (Chrome) 141` | 41 | left 201 | left 14 | left 94 |
| `Saving 12/141 (3 ok)...` | **66 — перенос** | **left 14 (вторая строка)** | left 14 | left **371** |

Причина (измеренная, а не предположенная): у строки **ровно нулевой запас** — подписи 163 px +
кнопки 265 px + отступы 24 px = 452 px = ширина строки на попапе 480 px. Как только временная
подпись кнопки становится длиннее постоянной, `flex-wrap` перекидывает **кнопки** на вторую строку
(левый край 230 → 14), а `space-between` отправляет `Parallel:` к правому краю (94 → 371) — ровно
то, что владелец описал как «два элемента исчезают, кнопки встают на их место».

Правка — с двух сторон:

1. `popup.css`: `.actions` больше не переносит (`flex-wrap: nowrap`, колоночный отступ 12→8 px),
   `.actions label` держит свою ширину (`flex-shrink: 0; white-space: nowrap`),
   `.actions-buttons` прижат вправо и не сжимается (`margin-inline-start: auto; flex-shrink: 0`).
2. `popup.js`: **подпись кнопки Save теперь постоянна**, её никогда не переписывают во время
   загрузки. Состояние несут цвет (`data-state` → `saving` #b26a00, `done` #43a047) и строка
   статуса; числа прогресса — в ней же и в бейдже.

Замерено после (`logs/pa_run2.txt`, реальный Chrome-даунлоад 3 файла):
`geometryChanges: 0` — за весь даунлоад ни одна координата не изменилась (ни кнопки, ни обе
подписи, ни строка, ни `body.scrollWidth`), `finalGeomSameAsBefore: true`, состояние при этом
живое (`stateChanges: 3`, статус «✓ Saved 3» → «Disconnected»).

Состояния замерены отдельно (`logs/savest_run.txt`), подпись кнопки во всех — `Save (Chrome) 3`:

| Момент | `data-state` | Цвет кнопки | Статус |
|---|---|---|---|
| до клика | — | `rgb(46,125,50)` | `Disconnected` |
| сразу после клика (до первого `await`) | `saving` | `rgb(178,106,0)` | `Saving 3 file(s)…` |
| после загрузки | `done` | `rgb(67,160,71)` | `✓ Saved 3 of 3` |

## Артефакты

Продукт: `extension/popup/popup.js`, `popup.css`, `popup.html`.
Гейт #3: `.ext-dev-loop/fixture/popup-reopen.html`,
`.ext-dev-loop/review_2026-10-12/popup_reopen_steps.json` + `logs/pr_final_run.txt` (зелёный на
финальных байтах), `logs/pr_mutant_run.txt`, `…/mutate_popup_filter.py` (мутация + sha256-возврат).
Гейт #1/#2: `…/popup_actions_steps.json` + `logs/pa_run.txt` (до), `logs/pa_run2.txt` (после),
`…/actions_states_steps.json` + `logs/lst_run3.txt` (состояния подписей),
`…/save_state_steps.json` + `logs/savest_run.txt`, снимок попапа — `logs/popup_green.png`.
Сьют расширения: 216/216 ok на финальных файлах.

## Ещё одна находка по харнессу

`--shot` на командной строке **не сочетается с `--script`**: прогон шагов молча отменяется, а
снимок снимается с `about:blank` (`[after --shot]`, exit 0). Снимок живого попапа нужно делать
**шагом** внутри сценария (`{ "target": "ext", "shot": "<path>" }`), а не флагом.
