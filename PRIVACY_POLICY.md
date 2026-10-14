# Privacy Policy for Web Media Parser (Chrome Extension)

*Last updated: October 14, 2026*

This Privacy Policy explains how the **Web Media Parser** browser extension ("the Extension") handles user information.

---

### 1. Core Principle: Privacy by Design
The Extension is designed to operate locally on your computer. **We do not collect, store, track, sell, or transmit your personal data or browsing activity to any external server or third party.**

---

### 2. Information Handled by the Extension

#### A. Web Page Content and Media URLs
* When you open the Extension popup or invoke a scan, the Extension analyzes the Document Object Model (DOM) of your current active tab to detect media links (images, videos, and full-resolution image variants).
* This analysis is performed **entirely locally in your browser memory**. None of your page visits or browsing history are logged or sent to any remote server.

#### B. Communication with Local Desktop Application (127.0.0.1)
* The Extension is a companion tool for the open-source **Web Media Parser desktop application**.
* When you explicitly click **"Send to App"**, the Extension sends your selected media URLs and the active tab's session cookies over local HTTP to `http://127.0.0.1:19876` (localhost).
* **This transmission never leaves your local machine.** Session cookies and URLs are passed strictly to your own locally running companion process to allow user-authorized media downloading from private or authenticated galleries.

#### C. Local File Downloads
* When you click **"Save (Chrome)"**, files are downloaded directly through Chrome's standard Download Manager (`chrome.downloads` API) to your computer's designated downloads folder. No third-party servers or intermediaries are involved.

#### D. Sieve Rule Updates
* The Extension includes built-in Imagus Sieve rules (static JSON pattern tables used to discover high-resolution images).
* When rules are updated (automatically or via the "Update now" button), the Extension fetches a static `sieve.json` file from public CDNs (GitHub / jsDelivr). No personal data, identifiers, or tracking tokens are sent during this update.

---

### 3. Permissions Used and Why

| Permission | Purpose |
|---|---|
| `activeTab` | Access the current active webpage to extract media when you open the popup or press a shortcut. |
| `downloads` | Save selected media files to your computer via Chrome's download manager. |
| `storage` | Store your local extension settings (concurrent download limits) and cached sieve rules locally. |
| `alarms` | Periodically check for updated sieve rule files in the background. |
| `cookies` | Export the current tab's session cookies to your local desktop companion app (`127.0.0.1`) only upon explicit user request. |
| `host_permissions: ["<all_urls>"]` | Fetch image headers and resolve full-size image links across various content delivery networks (CDNs) and image hosts, and communicate with the local companion app on `http://127.0.0.1:19876`. |

---

### 4. Third-Party Services and Analytics
The Extension **does not** contain any advertising, tracking scripts, telemetry, Google Analytics, or third-party tracking SDKs.

---

### 5. Changes to This Policy
Any changes to this policy will be documented in the extension's open-source GitHub repository.

---

### 6. Contact and Source Code
The Extension is open-source. For questions, bug reports, or to review the source code, visit:
[https://github.com/Sucotasch/web-media-parser](https://github.com/Sucotasch/web-media-parser)
