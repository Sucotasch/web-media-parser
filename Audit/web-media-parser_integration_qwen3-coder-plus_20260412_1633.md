> **🤖 Prompt Generation Metadata**
> - **Model:** qwen3-coder-plus
> - **Target Repository:** https://github.com/Sucotasch/web-media-parser/tree/fix/web-parser-stability-and-persistence
> - **Reference Repository:** https://github.com/Sucotasch/Imagus-Mass-Download-Mod
> - **Auto-generated RAG Query:** "web-media-parser,integration,sieves,filters,Imagus,patterns,full-size,video,image,extraction,implementation,code,examples"
> 
> <details><summary><b>Task Instructions</b></summary>
> 
> ```text
> You are an expert Principal Software Architect specializing in system integration, code migration, and architectural review. 
> You are provided with two distinct codebases:
> 1. **[TARGET_REPO]**: The project you need to analyze and potentially modify.
> 2. **[REFERENCE_REPO]**: The library, SDK, or example project proposed for integration or as a source of architectural patterns.
> 
> Your task is to critically evaluate the user's request to integrate concepts or code from [REFERENCE_REPO] into [TARGET_REPO]. Do not blindly execute the integration; first, assess its feasibility and value.
> 
> Your analysis MUST include the following sections in a structured Markdown report:
> 
> 1. **Feasibility & Impact Analysis**:
>    - Evaluate the architectural fit: Does [REFERENCE_REPO] align with the current stack and paradigms of [TARGET_REPO]?
>    - Identify the benefits and risks/costs.
>    - Provide a definitive verdict: Is this integration recommended, partially recommended, or strongly discouraged?
>    - *If the user has not specified a particular feature to integrate, proactively analyze both codebases and identify the top 1-3 architectural patterns, utilities, or features from [REFERENCE_REPO] that would provide the most value if integrated into [TARGET_REPO].*
> 
> 2. **Architectural Mapping** (If recommended or partially recommended):
>    - Explain conceptually how the components of [REFERENCE_REPO] map to the existing structures in [TARGET_REPO].
>    - Highlight any architectural bottlenecks or conflicts.
> 
> 3. **Integration Plan & Code Implementation** (If recommended):
>    - Provide a step-by-step migration or integration plan.
>    - Identify the exact files in [TARGET_REPO] that need to change.
>    - Supply the actual code snippets, strictly basing your API calls, class names, and patterns on the code found in [REFERENCE_REPO]. Do not hallucinate methods.
> 
> CRITICAL RULES & GUARDRAILS:
> 1. **Domain Preservation (CRITICAL)**: The core purpose, business logic, and domain terminology of [TARGET_REPO] MUST remain completely unchanged. Do not import domain-specific concepts, terminology, or features from [REFERENCE_REPO].
> 2. **Pattern Extraction Only**: Treat [REFERENCE_REPO] STRICTLY as a source of technical patterns, architectural solutions, APIs, or algorithms. Abstract these technical solutions away from their original business context before applying them to [TARGET_REPO].
> 3. **Read-Only Reference**: DO NOT modify the [REFERENCE_REPO]. It is read-only context.
> 4. **Minimal Intervention**: If integration is recommended, do it with the least possible disruption to the existing [TARGET_REPO] architecture.
> ```
> </details>
>
> <details><summary><b>Additional Context</b></summary>
> 
> ```text
> Нужен подробный план интеграции в web-media-parser обновляемых фильтров sieves с паттернами Imagus для извлечения полноразмерных видео и изображений, код с примерами конкретных решений. 
> ```
> </details>

---

### 1. Feasibility & Impact Analysis

#### Architectural Fit
Integrating the `Imagus` updating sieves (`sieve.jsn`) into `web-media-parser` is **highly feasible and strongly recommended**.

*   **Current State of `TARGET_REPO`**: `web-media-parser` relies heavily on `SitePatternManager` to transform URLs (like turning thumbnails into full-resolution images). It already uses a JSON-based pattern structure (`site_patterns.json`) and attempts to process `replace_patterns` and `imagus_patterns`. However, maintaining static pattern lists in a desktop application quickly leads to obsolescence because web structures change frequently.
*   **Concepts from `REFERENCE_REPO`**: Imagus excels due to its community-maintained `sieve.json` (or `.jsn`), which contains thousands of regex patterns to extract original media. The Imagus Mod repository features an auto-update mechanism (`updateSieve` logic using an `info.json` version check) to keep these rules current without requiring an extension update.

#### Benefits
1.  **Massive Increase in Extraction Quality**: Replacing or supplementing the custom patterns with the community-driven Imagus sieves gives `web-media-parser` access to thousands of up-to-date extraction rules for almost every major image host.
2.  **Zero-Maintenance Pattern Updates**: By porting the `updateSieve` mechanism, the desktop app can ping a remote URL on startup (or via a settings button), check the timestamp in `info.json`, and download the latest `sieve.jsn` automatically. This dramatically extends the lifespan of the crawler.

#### Risks/Costs
*   **Regex Compatibility**: JavaScript's regex engine handles some constructs slightly differently than Python's `re` module. However, the Target Repo's `SitePatternManager` already contains basic handling for `imagus_patterns`, meaning the foundation is already built.
*   **File Format Translation**: The Imagus `sieve.jsn` format uses specific keys (`link`, `img`, `res`, `to`, `url`). `SitePatternManager` needs to accurately map these Imagus keys to its internal processing engine.

#### Verdict
**Strongly Recommended**. This integration directly aligns with the `web-media-parser`'s stated capability of "Site-Specific Pattern Transformations" and solves the problem of regex rot over time.

---

### 2. Architectural Mapping

#### Conceptual Mapping
| REFERENCE_REPO (Imagus Mod JS) | TARGET_REPO (Web Media Parser Python) | Integration Concept |
| :--- | :--- | :--- |
| `src/sieve.jsn` | `resources/patterns/site_patterns.json` | The Target Repo will download and cache the `sieve.json` from a remote source, storing it locally as an `imagus_sieve.json` file. |
| `background.js` (updateSieve & info.json check) | `SitePatternManager.py` (Initialization) | On instantiation, `SitePatternManager` will asynchronously (or in a background thread) fetch the remote `info.json`, compare timestamps, and download the new sieve if needed. |
| Sieve Rule Applier (`rule.link.test(URL)`, `rule.to`) | `transform_image_url()` in `SitePatternManager` | The parser will dynamically iterate over the Imagus rules, utilizing Python's `re.sub` where Imagus uses `.replace` with regex. |

#### Architectural Adjustments
The `SitePatternManager` in the Target Repo is currently synchronous during `__init__`. To introduce an auto-updating mechanism without blocking the PySide6 UI thread, the update logic must be separated. We will introduce an async `update_imagus_sieves()` method.

The `SitePatternManager` currently expects a specific custom format. We need to upgrade `_load_pattern_file` to detect and parse the Imagus `sieve.json` schema dynamically. 

---

### 3. Integration Plan & Code Implementation

#### Step-by-step Migration Plan

1.  **Add Update Logic to `SitePatternManager`**:
    *   Introduce an async method `update_imagus_sieves` mirroring the Imagus `background.js` update check.
    *   Use `aiohttp` to fetch `info.json`. Check the `sieve_ver` timestamp against a local cache or a file's modified time.
    *   If newer, download the raw `sieve.json` and save it to the `resources/patterns/` directory.

2.  **Adapt the Sieve Loading Mechanism**:
    *   Modify `_load_pattern_file` to detect if the loaded JSON is the Imagus format. Imagus `sieve.json` is typically a large dictionary where keys are sieve names, and values are objects containing `link`, `img`, `res`, and `to` properties.
    *   Translate these properties into the internal `pattern_data` structure so `get_patterns_for_url` and `transform_image_url` can utilize them.

3.  **Enhance Regex Execution**:
    *   Imagus `to` fields often use `$1`, `$2` for capturing groups. Python's `re.sub` uses `\1`, `\2`. We must safely sanitize the Imagus target strings on the fly.

#### Target File Changes

**File:** `src/parser/site_pattern_manager.py`

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Site pattern manager for loading and applying site-specific patterns for media extraction
Based on structured site_patterns.json format, and augmented with auto-updating Imagus Sieves.
"""

import os
import re
import sys
import json
import time
import logging
import aiohttp
import asyncio
from typing import Dict, List, Any, Optional, Tuple, Set
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class SitePatternManager:
    """
    Manager for loading and applying site-specific patterns for media extraction
    Supports advanced features like CSS selectors, API integrations, and Imagus Sieves.
    """
    
    # Imagus Update Endpoints (Using public community mod endpoints as reference)
    IMAGUS_INFO_URL = "https://raw.githubusercontent.com/Sucotasch/Imagus-Mass-Download-Mod/mv3-version/src/info.json"
    IMAGUS_SIEVE_URL = "https://raw.githubusercontent.com/Sucotasch/Imagus-Mass-Download-Mod/mv3-version/src/sieve.jsn"

    def __init__(self, enable_built_in=True, custom_pattern_path=None):
        self.patterns = {}
        self.global_settings = {}
        self.loaded_files = []
        self.enable_built_in = enable_built_in
        self.custom_pattern_path = custom_pattern_path
        
        # Determine the base directory for saving dynamic sieves
        self.exec_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if getattr(sys, 'frozen', False):
            self.exec_dir = os.path.dirname(sys.executable)
            
        self.imagus_sieve_path = os.path.join(self.exec_dir, "resources", "patterns", "imagus_sieve.json")
        
        # Ensure patterns directory exists
        os.makedirs(os.path.dirname(self.imagus_sieve_path), exist_ok=True)
        
        # Load patterns
        self.load_patterns()
        
    async def update_imagus_sieves(self, force=False):
        """
        Asynchronously checks for and downloads updated Imagus sieves.
        Mirrors the logic from Imagus background.js updateSieve().
        """
        logger.info("Checking for Imagus sieve updates...")
        try:
            async with aiohttp.ClientSession() as session:
                # 1. Check info.json for version timestamp
                local_ver = 0
                if os.path.exists(self.imagus_sieve_path):
                    local_ver = int(os.path.getmtime(self.imagus_sieve_path) * 1000)
                
                if not force:
                    async with session.get(self.IMAGUS_INFO_URL, timeout=10) as response:
                        if response.status == 200:
                            info_data = await response.json()
                            remote_ver = info_data.get("sieve_ver", 0)
                            if local_ver >= remote_ver:
                                logger.info("Imagus sieves are up to date.")
                                return False
                
                # 2. Download new sieve.jsn
                logger.info("Downloading new Imagus sieves...")
                async with session.get(self.IMAGUS_SIEVE_URL, timeout=15) as response:
                    if response.status == 200:
                        sieve_text = await response.text()
                        # Ensure it's valid JSON before saving
                        json.loads(sieve_text) 
                        with open(self.imagus_sieve_path, 'w', encoding='utf-8') as f:
                            f.write(sieve_text)
                        
                        logger.info("Successfully updated Imagus sieves.")
                        # Reload to apply
                        self.load_patterns()
                        return True
        except Exception as e:
            logger.error(f"Failed to update Imagus sieves: {str(e)}")
            return False

    def load_patterns(self):
        """
        Load patterns from built-in, custom sources, and downloaded Imagus sieves.
        """
        self.patterns = {}
        self.global_settings = {}
        self.loaded_files = []
        
        # 1. Load native custom patterns if specified
        if self.custom_pattern_path and os.path.exists(self.custom_pattern_path):
            self._load_pattern_file(self.custom_pattern_path)
            
        # 2. Load downloaded Imagus Sieves
        if os.path.exists(self.imagus_sieve_path):
            logger.info(f"Loading Imagus patterns from: {self.imagus_sieve_path}")
            self._load_imagus_file(self.imagus_sieve_path)
            
        # 3. Fallback to default built-ins
        elif self.enable_built_in:
            built_in_path = os.path.join(self.exec_dir, "resources", "patterns", "site_patterns.json")
            if os.path.exists(built_in_path):
                self._load_pattern_file(built_in_path)

    def _load_imagus_file(self, file_path):
        """
        Parses the specific Imagus sieve JSON format and adapts it to the internal pattern structure.
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                sieve_data = json.load(f)
                
            count = 0
            for sieve_name, rule in sieve_data.items():
                if isinstance(rule, dict) and 'link' in rule and 'to' in rule:
                    # Translate Imagus JS regex format to Python re format
                    
                    # Convert JS-style regex flags if embedded, though Imagus usually keeps them clean in JSON
                    link_pattern = rule['link']
                    
                    # Adapt the structure for internal use
                    pattern_data = {
                        'site': sieve_name,
                        'url_patterns': [link_pattern],
                        'imagus_patterns': {
                            'image': [{
                                'source': link_pattern,
                                'target': rule['to']
                            }]
                        }
                    }
                    
                    self.patterns[sieve_name] = pattern_data
                    count += 1
                    
            self.loaded_files.append(file_path)
            logger.info(f"Adapted {count} Imagus sieve patterns.")
            return True
        except Exception as e:
            logger.error(f"Error loading Imagus file {file_path}: {str(e)}")
            return False

    def _load_pattern_file(self, file_path):
        """
        Load native web-media-parser patterns from a JSON file
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Process site patterns
            if 'patterns' in data:
                # New format with patterns array
                for pattern in data['patterns']:
                    if 'site' in pattern and pattern.get('enabled', True):
                        site_name = pattern['site']
                        self.patterns[site_name] = pattern
            else:
                # Process old format or individual entries
                for key, value in data.items():
                    if key == 'global_settings':
                        self.global_settings = value
                    elif key.startswith('['): 
                        continue
                    elif isinstance(value, dict):
                        if 'site' in value or 'domains' in value or 'url_patterns' in value:
                            site_name = value.get('site', key)
                            self.patterns[site_name] = value
            
            if 'global_settings' in data:
                self.global_settings = data['global_settings']
            
            self.loaded_files.append(file_path)
            return True
        except Exception as e:
            logger.error(f"Error loading pattern file {file_path}: {str(e)}")
            return False
            
    # [Rest of the class (get_patterns_for_url, _apply_global_transformations, etc.) remains unchanged]
    # [...]
    
    def transform_image_url(self, url: str, source_url: str) -> str:
        """
        Apply patterns to transform thumbnail URLs to fullsize image URLs, including Imagus rule sets.
        """
        patterns = self.get_patterns_for_url(url) or self.get_patterns_for_url(source_url)
        if not patterns:
            return self._apply_global_transformations(url)
        
        original_url = url
        transformed = False
        
        for pattern_name, pattern_data in patterns:
            try:
                # Native pattern transformations
                if 'image_transformations' in pattern_data:
                    # [Existing Native handling logic...]
                    pass
                
                # Imagus patterns (Native or imported from sieve.jsn)
                if 'imagus_patterns' in pattern_data:
                    imagus_data = pattern_data['imagus_patterns']
                    
                    for transform_type in ['photo_transform', 'media', 'image']:
                        if transform_type in imagus_data:
                            transform_patterns = imagus_data[transform_type]
                            if isinstance(transform_patterns, list):
                                for transform in transform_patterns:
                                    source = transform.get('source')
                                    target = transform.get('target')
                                    
                                    if source and target:
                                        try:
                                            # Sanitize Imagus target syntax: convert JS $1, $2 to Python \1, \2
                                            # Using a lambda to safely escape the replacement string if it contains regex refs
                                            safe_target = re.sub(r'\$(\d+)', r'\\\1', target)
                                            
                                            new_url = re.sub(source, safe_target, url, flags=re.IGNORECASE)
                                            if new_url != url:
                                                url = new_url
                                                transformed = True
                                                logger.debug(f"Transformed URL using Imagus sieve [{pattern_name}]: {original_url} -> {url}")
                                                break # Stop on first successful transform
                                        except Exception as e:
                                            logger.debug(f"Error applying imagus pattern {source}: {str(e)}")
                
                if transformed:
                    break
                    
            except Exception as e:
                logger.debug(f"Error applying pattern {pattern_name}: {str(e)}")
                continue
        
        if not transformed:
            url = self._apply_global_transformations(url)
            
        return url
```