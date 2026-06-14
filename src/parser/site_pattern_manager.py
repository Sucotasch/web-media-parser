#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Site pattern manager for loading and applying site-specific patterns for media extraction
Based on structured site_patterns.json format
"""

import os
import re
import sys
import json
import logging
from typing import Dict, List, Any, Optional, Tuple, Set
from urllib.parse import urlparse
from .utils import get_domain

logger = logging.getLogger(__name__)


class SitePatternManager:
    """
    Manager for loading and applying site-specific patterns for media extraction
    Supports advanced features like CSS selectors and API integrations
    """

    def __init__(self, enable_built_in=True, custom_pattern_path=None, imagus_sieve_path=None):
        self.patterns = {}
        self.imagus_rules = {}  # Rules indexed by domain
        self.imagus_global_rules = [] # Rules without specific domain
        self.global_settings = {}
        self.loaded_files = []
        self.enable_built_in = enable_built_in
        self.custom_pattern_path = custom_pattern_path
        self.imagus_sieve_path = imagus_sieve_path
        
        # Load patterns
        self.load_patterns()
    
    def load_patterns(self):
        """
        Load patterns from built-in and custom sources
        """
        # Clear existing patterns
        self.patterns = {}
        self.imagus_rules = {}
        self.imagus_global_rules = []
        self.global_settings = {}
        self.loaded_files = []
        
        # Try loading custom patterns if specified
        if self.custom_pattern_path and os.path.exists(self.custom_pattern_path):
            success = self._load_pattern_file(self.custom_pattern_path)
            if success:
                logger.info(f"Successfully loaded custom site patterns from {self.custom_pattern_path}")
        
        # Try loading custom Imagus sieve if specified
        if self.imagus_sieve_path and os.path.exists(self.imagus_sieve_path):
            success = self._load_imagus_file(self.imagus_sieve_path)
            if success:
                logger.info(f"Successfully loaded custom Imagus sieve from {self.imagus_sieve_path}")

        # If no custom patterns or custom patterns failed to load, use built-in patterns
        search_dirs = []
        if not self.patterns and self.enable_built_in:
            # Check for patterns file in various locations
            
            # First try the actual executable directory (for standalone exe)
            if getattr(sys, 'frozen', False):
                exe_dir = os.path.dirname(sys.executable)
                exe_patterns_path = os.path.join(exe_dir, "site_patterns.json")
                if os.path.exists(exe_patterns_path):
                    built_in_path = exe_patterns_path
                    logger.info(f"Using patterns from executable directory: {built_in_path}")
                    with open(built_in_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if 'version' in data:
                            logger.info(f"Loaded patterns version: {data['version']}")
                    self._load_pattern_file(built_in_path)
                    return
            
            # If not found, use the patterns file from the application directory
            exec_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            built_in_path = os.path.join(exec_dir, "site_patterns.json")
            
            # If not found in application directory, try resources directory
            if not os.path.exists(built_in_path):
                built_in_path = os.path.join(exec_dir, "resources", "patterns", "site_patterns.json")
                
            # For PyInstaller bundle
            if not os.path.exists(built_in_path):
                # Get the PyInstaller _MEIPASS directory if available
                base_dir = getattr(sys, '_MEIPASS', exec_dir)
                built_in_path = os.path.join(base_dir, "resources", "patterns", "site_patterns.json")
                
            if os.path.exists(built_in_path):
                self._load_pattern_file(built_in_path)
                logger.info(f"Using built-in patterns from {built_in_path}")

            # Also scan for Imagus sieves in the same directories
            search_dirs = [os.path.dirname(built_in_path)]
            if getattr(sys, 'frozen', False):
                search_dirs.append(os.path.dirname(sys.executable))
            
            # Add current user provided path if it exists
            if self.custom_pattern_path:
                custom_dir = os.path.dirname(self.custom_pattern_path)
                if custom_dir not in search_dirs:
                    search_dirs.append(custom_dir)

        for s_dir in search_dirs:
            if not s_dir or not os.path.exists(s_dir): continue
            for filename in os.listdir(s_dir):
                if filename.startswith("Imagus_sieve") and filename.endswith(".json"):
                    imagus_path = os.path.join(s_dir, filename)
                    self._load_imagus_file(imagus_path)
                elif filename == "site_patterns.json":
                    native_path = os.path.join(s_dir, filename)
                    self._load_pattern_file(native_path)
    
    def _load_pattern_file(self, file_path):
        """
        Load patterns from a JSON file (Native Format)
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
            logger.info(f"Loaded {len(self.patterns)} native site patterns from {file_path}")
            return True
        except Exception as e:
            logger.error(f"Error loading native pattern file {file_path}: {str(e)}")
            return False

    def _load_imagus_file(self, file_path):
        """
        Load Imagus-style sieves from a JSON file
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            rules_count = 0
            js_converted = 0
            js_skipped = 0
            for rule_name, rule_data in data.items():
                if not isinstance(rule_data, dict): continue
                
                # Rules must have at least 'to' or 'res' to be useful
                to_rule = rule_data.get('to', '')
                res_rule = rule_data.get('res', '')
                
                # Handle JS rules (starts with :)
                if isinstance(to_rule, str) and to_rule.startswith(':'):
                    # Try to convert JS rule to Python callable
                    callable_fn = self._try_parse_imagus_js(to_rule)
                    if callable_fn:
                        rule_data['to_callable'] = callable_fn
                        js_converted += 1
                    else:
                        js_skipped += 1
                        continue  # Can't convert — skip entirely
                
                elif isinstance(to_rule, str) and to_rule:
                    # Simple regex rule — existing logic
                    rule_data['to_python'] = self._sanitize_imagus_target(to_rule)
                
                # Indexing by domain from 'link' property
                link_regex = rule_data.get('link', '')
                domain = self._extract_domain_from_regex(link_regex)
                
                # Also try to extract domain from 'img' regex if 'link' is missing
                if not domain:
                    domain = self._extract_domain_from_regex(rule_data.get('img', ''))
                
                if domain:
                    if domain not in self.imagus_rules:
                        self.imagus_rules[domain] = []
                    self.imagus_rules[domain].append(rule_data)
                else:
                    self.imagus_global_rules.append(rule_data)
                
                rules_count += 1
            
            self.loaded_files.append(file_path)
            log_parts = [f"Loaded {rules_count} Imagus sieve rules from {file_path}"]
            if js_converted:
                log_parts.append(f"JS converted: {js_converted}")
            if js_skipped:
                log_parts.append(f"JS skipped (needs DOM): {js_skipped}")
            logger.info(" | ".join(log_parts))
            return True
        except Exception as e:
            logger.error(f"Error loading Imagus sieve {file_path}: {str(e)}")
            return False

    def _sanitize_imagus_target(self, target: Any) -> Any:
        r"""Convert Imagus $1, $2 to Python \g<1>, \g<2> to avoid ambiguity with numbers"""
        if not isinstance(target, str): return target
        # Use a lambda for replacement to safely construct the \g<n> syntax
        # Limit to 1 digit ($1-$9) to avoid greedy matching with literal digits
        return re.sub(r'(?<!\\)\$(\d+)', lambda m: f'\\g<{m.group(1)}>', target)

    # --- JS rule conversion ---

    # DOM keywords that indicate the rule needs browser context
    _DOM_KEYWORDS = (
        'this.node', 'this.TRG', 'this.find', 'this.set', 'this.prepare',
        'this.getImages', 'document.', 'window.', 'location.',
        'Port.send', 'XMLHttpRequest', 'fetch(', 'addEventListener',
        'querySelector', 'getElementById', 'getElementsBy',
        'createElement', 'appendChild', 'innerHTML', 'outerHTML',
        'sessionStorage', 'localStorage',
    )

    def _needs_dom(self, js_code: str) -> bool:
        """Check if JS code requires browser DOM/context to execute."""
        return any(kw in js_code for kw in self._DOM_KEYWORDS)

    def _try_parse_imagus_js(self, js_code: str):
        """Try to convert an Imagus JS rule to a Python callable.

        Returns a callable(match_object) -> str | None, or None if conversion fails.

        Supports common patterns:
        - return $[0], $[1], etc. (regex match groups)
        - return 'prefix' + $[1] + 'suffix' (string concatenation)
        - return $[2] ? expr1 : expr2 (ternary operator)
        - $[1].replace(/pattern/, 'replacement') (regex replace)
        - return `template ${$[1]}` (template literals)
        - #ext1 ext2# expansion → multiple URLs separated by \\n
        """
        if not js_code.startswith(':'):
            return None
        js_code = js_code[1:].strip()  # Remove leading ':'

        if self._needs_dom(js_code):
            return None

        # Extract the return expression
        # Handle multi-line: find the last 'return' statement
        return_match = re.search(r'return\s+(.+?)(?:;?\s*$)', js_code, re.MULTILINE | re.DOTALL)
        if not return_match:
            return None
        expr = return_match.group(1).strip().rstrip(';').strip()

        # Try to build a Python callable from the expression
        return self._build_js_callable(expr, js_code)

    def _build_js_callable(self, expr: str, full_js: str):
        """Build a Python callable from a JS return expression."""
        import re as _re

        # Normalize $[n] → group(n) references for internal processing
        # We'll compile a function that receives a match object

        # Check for #ext# pattern (variant expansion)
        has_ext_pattern = '#ext#' in expr or bool(_re.search(r"'#[^']+#'", expr))

        # Convert JS expression to Python expression
        py_expr = self._js_expr_to_python(expr)
        if py_expr is None:
            return None

        # Build the function
        func_src = f"""
def _transform(m):
    import re as _re
    g = lambda n: m.group(n) if m.lastindex and n <= m.lastindex else ''
    g0 = m.group(0)
    try:
        result = {py_expr}
        if result is None or result is False:
            return None
        result = str(result)
        # Expand #ext# patterns: '#jpg png#' → 'jpg', 'png'
        ext_match = _re.search(r"'(#[^']+#)'", result)
        if ext_match:
            exts = ext_match.group(1).strip('#').split()
            result = result[:ext_match.start(1)] + exts[0] + result[ext_match.end(1):]
            variants = [result]
            for ext in exts[1:]:
                v = result[:ext_match.start(1)] + ext + result[ext_match.end(1):]
                variants.append(v)
            return '\\n'.join(variants)
        return result
    except Exception:
        return None
"""
        try:
            namespace = {}
            exec(func_src, namespace)
            return namespace['_transform']
        except Exception:
            return None

    def _js_expr_to_python(self, expr: str) -> str | None:
        """Convert a JS expression to a Python expression string.

        Handles: $[n] refs, string concat, ternary, .replace(), template literals.
        Returns None if the expression is too complex to convert.
        """
        import re as _re

        # Template literal: `prefix${expr}suffix`
        tl_match = _re.match(r'`(.+)`$', expr, _re.DOTALL)
        if tl_match:
            template = tl_match.group(1)
            # Convert ${$[n]} → f'...{g(n)}...'
            py_template = _re.sub(r'\$\{(\$?\[(\d+)\])\}', lambda m: '{g(' + m.group(2) + ')}', template)
            py_template = _re.sub(r'\$\{([^}]+)\}', lambda m: '{' + m.group(1).replace('$[', 'g(').replace(']', ')') + '}', py_template)
            return "f'" + py_template.replace("'", "\\'") + "'"

        # Ternary: $[2] ? expr1 : expr2
        ternary_match = _re.match(r'(.+?)\s*\?\s*(.+?)\s*:\s*(.+)$', expr)
        if ternary_match:
            cond, if_true, if_false = ternary_match.groups()
            py_cond = self._js_expr_to_python(cond.strip())
            py_true = self._js_expr_to_python(if_true.strip())
            py_false = self._js_expr_to_python(if_false.strip())
            if py_cond and py_true and py_false:
                return f"({py_true} if {py_cond} else {py_false})"

        # String concatenation: pieces joined with +
        # Split by + but not inside strings or $[n]
        parts = self._split_js_concat(expr)
        if parts and len(parts) > 1:
            py_parts = []
            for part in parts:
                py_part = self._js_expr_to_python_single(part.strip())
                if py_part is None:
                    return None
                py_parts.append(py_part)
            return ' + '.join(py_parts)

        # Single expression
        return self._js_expr_to_python_single(expr)

    def _split_js_concat(self, expr: str) -> list[str]:
        """Split a JS concatenation expression by +, respecting strings and $[n]."""
        parts = []
        current = ''
        depth = 0
        in_string = None
        i = 0
        while i < len(expr):
            ch = expr[i]
            if in_string:
                current += ch
                if ch == in_string and expr[i-1:i] != '\\':
                    in_string = None
                i += 1
                continue
            if ch in ('"', "'"):
                in_string = ch
                current += ch
                i += 1
                continue
            if ch == '[':
                depth += 1
                current += ch
                i += 1
                continue
            if ch == ']':
                depth -= 1
                current += ch
                i += 1
                continue
            if ch == '+' and depth == 0:
                parts.append(current)
                current = ''
                i += 1
                continue
            current += ch
            i += 1
        if current.strip():
            parts.append(current)
        return parts

    def _js_expr_to_python_single(self, expr: str) -> str | None:
        """Convert a single JS expression (no concat, no ternary) to Python."""
        import re as _re

        expr = expr.strip()

        # $[n] → g(n)
        if _re.fullmatch(r'\$\[(\d+)\]', expr):
            n = _re.search(r'\d+', expr).group()
            return f'g({n})'

        # String literal
        if (expr.startswith('"') and expr.endswith('"')) or \
           (expr.startswith("'") and expr.endswith("'")):
            inner = expr[1:-1]
            return "'" + inner.replace("'", "\\'") + "'"

        # .replace(/pattern/, 'replacement')
        replace_match = _re.match(r'(.+?)\.replace\s*\(\s*/(.+?)/([gimsuy]*)\s*,\s*(.+?)\s*\)\s*$', expr)
        if replace_match:
            target, pattern, flags, replacement = replace_match.groups()
            py_target = self._js_expr_to_python_single(target)
            py_repl = self._js_expr_to_python_single(replacement)
            if py_target and py_repl:
                flag_str = 're.IGNORECASE' if 'i' in flags else '0'
                return f"_re.sub(r'{pattern}', {py_repl}, {py_target}, flags={flag_str})"

        # Math operations: Math.ceil(...), Math.random()
        if 'Math.' in expr:
            # Convert Math.ceil(x) → int(math.ceil(x)), etc.
            py_expr = expr
            py_expr = _re.sub(r'Math\.ceil\((.+?)\)', r'int(__import__("math").ceil(\1))', py_expr)
            py_expr = _re.sub(r'Math\.random\(\)', '__import__("math").random()', py_expr)
            py_expr = _re.sub(r'Math\.floor\((.+?)\)', r'int(__import__("math").floor(\1))', py_expr)
            if py_expr != expr:
                return py_expr

        # atob(...) → base64.b64decode(...).decode()
        atob_match = _re.match(r'atob\((.+?)\)', expr)
        if atob_match:
            inner = atob_match.group(1)
            py_inner = self._js_expr_to_python_single(inner)
            if py_inner:
                return f"__import__('base64').b64decode({py_inner}).decode()"

        # decodeURIComponent(...)
        dec_match = _re.match(r'decodeURIComponent\((.+?)\)', expr)
        if dec_match:
            inner = dec_match.group(1)
            py_inner = self._js_expr_to_python_single(inner)
            if py_inner:
                return f"__import__('urllib.parse').unquote({py_inner})"

        # Fallback: return None (can't convert)
        return None

    def _extract_domain_from_regex(self, regex_str: str) -> Optional[str]:
        """Heuristically extract a plain domain from a regex like '^(media\\.admagazine\\.ru/'"""
        if not regex_str: return None
        # Handle optional ^ and optional ( at the start, and optional www\.
        # Updated regex to handle multiple subdomains (multiple dots)
        match = re.search(r'\^?\s*\(?\s*(?:www\.)?([a-z0-9-]+(?:\\[.][a-z0-9-]+)+)', regex_str, re.I)
        if match:
            # Normalize: remove escaping and force lowercase
            return match.group(1).replace(r'\.', '.').lower()
        return None

    def _expand_variants(self, text: str) -> List[str]:
        """Expand Imagus syntax like 'image.#jpg png#' into multiple strings.

        Recursively handles multiple #...# blocks to generate all combinations,
        e.g. '/#ext1 ext2#/#jpg png#/' → 4 variants.
        """
        if not text: return []

        lines = text.split('\n')
        all_variants = []

        def expand_line(l: str) -> List[str]:
            match = re.search(r'#([^#]+)#', l)
            if not match:
                return [l]
            prefix = l[:match.start()]
            options = match.group(1).split()
            suffix = l[match.end():]
            res = []
            for opt in options:
                res.extend(expand_line(f"{prefix}{opt}{suffix}"))
            return res

        for line in lines:
            line = line.strip()
            if line:
                all_variants.extend(expand_line(line))

        return all_variants
    
    def get_patterns_for_url(self, url: str) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Get applicable patterns for a URL
        Returns a list of (pattern_name, pattern_data) tuples
        """
        applicable_patterns = []
        
        # Parse URL
        try:
            parsed_url = urlparse(url)
            domain = parsed_url.netloc.lower()
            path = parsed_url.path.lower()
            full_url = url.lower()
        except Exception:
            return []
        
        # Find patterns for this domain/URL
        for pattern_name, pattern_data in self.patterns.items():
            try:
                # First check domains
                pattern_domains = pattern_data.get('domains', [])
                domain_match = False
                
                for pattern_domain in pattern_domains:
                    if pattern_domain.lower() in domain:
                        domain_match = True
                        break
                
                # If domain doesn't match, check URL patterns
                if not domain_match and 'url_patterns' in pattern_data:
                    url_patterns = pattern_data['url_patterns']
                    for url_pattern in url_patterns:
                        try:
                            if re.search(url_pattern, full_url, re.IGNORECASE):
                                domain_match = True
                                break
                        except Exception as e:
                            logger.debug(f"Error matching URL pattern {url_pattern}: {str(e)}")
                
                # If we have a match, add to applicable patterns
                if domain_match:
                    applicable_patterns.append((pattern_name, pattern_data))
            except Exception as e:
                logger.debug(f"Error processing pattern {pattern_name}: {str(e)}")
        
        return applicable_patterns
    
    def transform_image_url(self, url: str, source_url: str) -> List[str]:
        """
        Apply patterns to transform thumbnail URLs to fullsize image URLs
        Returns a list of potential candidates (variants)
        """
        results = [url]
        transformed = False
        
        # 1. Native Site-Specific Patterns
        patterns = self.get_patterns_for_url(url) or self.get_patterns_for_url(source_url)
        if patterns:
            for pattern_name, pattern_data in patterns:
                try:
                    # Native image_transformations
                    if 'image_transformations' in pattern_data:
                        transform_data = pattern_data['image_transformations']
                        if 'replace_patterns' in transform_data:
                            for replace_pattern in transform_data['replace_patterns']:
                                source, target = replace_pattern.get('source'), replace_pattern.get('target')
                                if source and target:
                                    new_url = re.sub(source, target, results[0], flags=re.IGNORECASE)
                                    if new_url != results[0]:
                                        results[0] = new_url
                                        transformed = True
                    
                    # Native imagus_patterns section
                    elif 'imagus_patterns' in pattern_data:
                        imagus_data = pattern_data['imagus_patterns']
                        for transform_type in ['photo_transform', 'media', 'image']:
                            if transform_type in imagus_data:
                                for transform in imagus_data[transform_type]:
                                    source, target = transform.get('source'), transform.get('target')
                                    if source and target:
                                        new_url = re.sub(source, target, results[0], flags=re.IGNORECASE)
                                        if new_url != results[0]:
                                            results[0] = new_url
                                            transformed = True
                except Exception as e: logger.debug(f"Error applying pattern {pattern_name}: {e}")

        # 2. Imagus Sieves (Domain-indexed & Global)
        source_domain = get_domain(source_url)
        img_domain = get_domain(url)
        
        # Check rules for both source page domain and image domain
        imagus_candidates = []
        if source_domain:
            imagus_candidates.extend(self.imagus_rules.get(source_domain, []))
            # Also check base domain if applicable (e.g. www.site.com -> site.com)
            if source_domain.startswith('www.'):
                base = source_domain[4:]
                imagus_candidates.extend(self.imagus_rules.get(base, []))
                
        if img_domain and img_domain != source_domain:
            imagus_candidates.extend(self.imagus_rules.get(img_domain, []))
            if img_domain.startswith('www.'):
                base = img_domain[4:]
                imagus_candidates.extend(self.imagus_rules.get(base, []))
                
        imagus_candidates.extend(self.imagus_global_rules)
        
        sieve_results = []
        # Test variations of the URL to match Imagus regex markers like ^
        url_variations = [results[0]]
        if '://' in results[0]:
            url_variations.append(results[0].split('://', 1)[1])
            
        logger.debug(f"Checking {len(imagus_candidates)} Imagus candidates for {url}")
        
        for rule in imagus_candidates:
            try:
                img_regex = rule.get('img', '')
                if not img_regex: continue
                
                for v_url in url_variations:
                    match = re.search(img_regex, v_url, re.I)
                    if match:
                        # Check for JS-converted callable first
                        to_callable = rule.get('to_callable')
                        if to_callable:
                            try:
                                substituted = to_callable(match)
                                if substituted and substituted != v_url:
                                    # Handle newline-separated variants
                                    for variant in substituted.split('\n'):
                                        variant = variant.strip()
                                        if variant and '://' not in variant:
                                            scheme = results[0].split('://', 1)[0]
                                            variant = f"{scheme}://{variant}"
                                        if variant and variant != v_url:
                                            sieve_results.append(variant)
                                    transformed = True
                                break
                            except Exception as e:
                                logger.debug(f"Imagus callable failed: {e}")
                                break

                        # Fallback: regex substitution
                        to_pattern = rule.get('to_python', '')
                        if not to_pattern: continue
                        
                        # Apply substitution on the variation that matched
                        try:
                            substituted = re.sub(img_regex, lambda m, pat=to_pattern: m.expand(pat), v_url, flags=re.IGNORECASE)
                        except Exception as e:
                            logger.debug(f"re.sub failed with pattern {to_pattern}: {e}")
                            substituted = re.sub(img_regex, to_pattern, v_url, flags=re.IGNORECASE)
                        
                        if v_url != results[0] and '://' not in substituted:
                            scheme = results[0].split('://', 1)[0]
                            substituted = f"{scheme}://{substituted}"
                            
                        if substituted != results[0]:
                            logger.debug(f"Imagus match found: {img_regex} -> {substituted}")
                            variants = self._expand_variants(substituted)
                            sieve_results.extend(variants)
                            transformed = True
                        break
            except Exception as e: 
                logger.debug(f"Error in Imagus rule processing: {e}")
                pass

        if sieve_results:
            results.extend(sieve_results)

        # 3. Global Transformations (if nothing else worked)
        if not transformed:
            global_transformed = self._apply_global_transformations(url)
            if global_transformed != url:
                results = [global_transformed]

        # Final deduplication while preserving order
        seen = set()
        final_list = []
        for u in results:
            if u not in seen:
                final_list.append(u)
                seen.add(u)
                
        return final_list
    
    def _apply_global_transformations(self, url: str) -> str:
        """
        Apply global thumbnail transformations to a URL
        """
        if not self.global_settings or 'common_image_patterns' not in self.global_settings:
            return url
            
        original_url = url
        transformed = False
        
        # Get thumbnail transformations
        common_patterns = self.global_settings['common_image_patterns']
        if 'thumbnail_transform' in common_patterns:
            for transform in common_patterns['thumbnail_transform']:
                source = transform.get('source')
                target = transform.get('target')
                
                if source and target:
                    try:
                        new_url = re.sub(source, target, url, flags=re.IGNORECASE)
                        if new_url != url:
                            url = new_url
                            transformed = True
                            logger.debug(f"Transformed image URL using global pattern: {original_url} -> {url}")
                    except Exception as e:
                        logger.debug(f"Error applying global pattern {source}: {str(e)}")
                        
        return url
    
    def get_loaded_files(self) -> List[str]:
        """
        Get list of loaded pattern files
        """
        return self.loaded_files
    
    def get_pattern_count(self) -> int:
        """
        Get number of loaded patterns
        """
        return len(self.patterns)