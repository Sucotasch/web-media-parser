# Chrome Web Store Publication Package & Guide

This document contains everything needed to submit the **Web Media Parser** extension to the Google Chrome Web Store.

---

## 1. Store Listing Information

### Extension Name
`Web Media Parser`

### Summary / Short Description (<= 132 chars)
> Scan pages for media, upgrade thumbnails to original full-size images with Imagus Sieve, and save via Chrome or companion desktop app.

*(Length: 130 characters)*

---

### Detailed Description (English)

```text
Web Media Parser is a powerful media extraction tool designed to help you discover, filter, and save original high-resolution images, videos, and media files from any webpage.

KEY FEATURES:
• Comprehensive DOM Scanner: Extracts pictures, img tags, srcset variants (picking highest resolution by device pixel ratio or width), background images, video sources, and direct media links.
• Built-in Imagus Sieve Engine: Ships with 800+ proven sieve rules to automatically resolve tiny thumbnail previews into their full-size original URLs on popular image hosts and social platforms.
• Built-in Chrome Downloader ("Save (Chrome)"): Download found media directly through your browser with adjustable parallel download concurrency to prevent server rate-limiting.
• Companion Desktop Integration ("Send to App"): Seamlessly send queues of media links directly to the Web Media Parser desktop crawler (running locally on your PC) for multi-threaded downloading and deep recursive crawling.
• Smart Filters: Easily filter discovered media by source type (Full-size vs. Thumbnails) or by domain.
• Privacy-Centric: Runs locally on your machine. Does not collect, track, or upload your browsing history or personal data to remote servers.

COMPANION DESKTOP APP:
While the extension can download media files directly using Chrome's built-in download manager, advanced features like recursive crawling, Deno JS sandbox execution, and deep page parsing are powered by our open-source companion desktop application:
https://github.com/Sucotasch/web-media-parser
```

---

### Detailed Description (Russian / Для русскоязычной локали)

```text
Web Media Parser — инструмент для поиска, фильтрации и скачивания медиафайлов (изображений и видео) оригинального разрешения с любых веб-страниц.

ОСНОВНЫЕ ВОЗМОЖНОСТИ:
• Глубокое сканирование страницы: извлечение img, тегов picture, вариантов srcset (автоматический выбор наибольшего разрешения), фоновых картинок CSS, видео и прямых ссылок на файлы.
• Встроенный движок Imagus Sieve: более 800 готовых правил для автоматической замены мелких превью на полноразмерные оригиналы на популярных фотохостингах и порталах.
• Скачивание прямо в браузере ("Save (Chrome)"): автономная загрузка файлов через менеджер загрузок Chrome с настраиваемым ограничением параллельных потоков.
• Интеграция с десктопным приложением ("Send to App"): отправка ссылок в очередь десктопного клиента Web Media Parser для пакетного скачивания и рекурсивного обхода страниц.
• Удобная фильтрация: мгновенный отбор только полноразмерных оригиналов или фильтрация по доменам.
• Конфиденциальность: работает локально на вашем компьютере, не собирает и не передаёт историю просмотров на сторонние серверы.

ДЕСКТОПНОЕ ПРИЛОЖЕНИЕ-КОМПАНЬОН:
Расширение работает автономно для скачивания через Chrome. Расширенные возможности (рекурсивный краулинг, многопоточная докачка и JS-движок Deno) доступны в связке с десктопным приложением:
https://github.com/Sucotasch/web-media-parser
```

---

## 2. Permission Justifications (for Developer Console form)

When submitting, Google requires a justification for each sensitive permission. Copy and paste these exact explanations:

### `<all_urls>` (Host Permission)
> The extension is a general-purpose media scanner and download manager that works across any user-visited webpage. Host permissions are required for the background service worker to perform cross-origin HEAD and GET requests (with appropriate Referer headers) to discover full-size media files on CDNs and external image hosts, and to communicate with the user's local companion application on http://127.0.0.1:19876.

### `cookies`
> The extension accesses the session cookies of the current active tab solely to forward them to the user's local companion desktop application running on 127.0.0.1 upon the user explicitly clicking the "Send to App" button. This allows the user's desktop crawler to download media files from private or authenticated user galleries without requiring re-authentication. Cookies are never transmitted to third parties or remote servers.

### `downloads`
> Used by the "Save (Chrome)" feature to save user-selected media files directly to the user's computer via Chrome's download manager with configurable parallel concurrency.

### `activeTab`
> Used to access the DOM of the active tab to extract media elements when the user opens the extension popup or presses the scanning keyboard shortcut.

### `storage`
> Used to persist user preferences locally (such as parallel download concurrency) and cache Imagus Sieve rules for offline use.

### `alarms`
> Used to schedule periodic background checks (once weekly) for updated Imagus Sieve pattern definitions from the public GitHub mirror.

---

## 3. Privacy Policy & User Data FAQ

* **Privacy Policy URL**:
  `https://github.com/Sucotasch/web-media-parser/blob/main/PRIVACY_POLICY.md`
* **Single Purpose Statement**:
  `Extract, upgrade, and download media files from webpages via browser downloads or a companion desktop application.`
* **Data Usage Declarations**:
  - *Do you collect personal data?* **No.**
  - *Do you transmit data to third parties?* **No.**
  - *Does the extension use remote code execution?* **No.** Sieve rules are declarative static JSON regex pattern tables parsed with `JSON.parse()`.

---

## 4. Visual Assets Checklist

Google Web Store requires:
1. **Icons**:
   - `icons/icon16.png` (16x16) — Ready in `extension/icons/`
   - `icons/icon48.png` (48x48) — Ready in `extension/icons/`
   - `icons/icon128.png` (128x128) — Ready in `extension/icons/`
2. **Screenshots** (Mandatory, at least 1):
   - Resolution: Exactly **1280x800** or **640x400** pixels (PNG or JPEG, no transparency).
   - Content: Extension popup open on an image gallery page showing detected media items.
3. **Small Promo Tile** (Optional):
   - 440x280 pixels.

---

## 5. How to Build the ZIP for Submission

From the project root in PowerShell:
```powershell
Compress-Archive -Path extension\* -DestinationPath WebMediaParser_extension_v1.4.1.zip -Force
```
Upload `WebMediaParser_extension_v1.4.1.zip` to the [Chrome Developer Dashboard](https://chrome.google.com/webstore/devconsole).
