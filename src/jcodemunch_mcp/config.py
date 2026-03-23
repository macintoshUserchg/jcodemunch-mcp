"""Centralized JSONC config for jcodemunch-mcp."""

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Global config storage
_GLOBAL_CONFIG: dict[str, Any] = {}
_PROJECT_CONFIGS: dict[str, dict[str, Any]] = {}  # repo -> merged config
_PROJECT_CONFIG_HASHES: dict[str, str] = {}  # repo -> content hash of .jcodemunch.jsonc
_DEPRECATED_ENV_VARS_LOGGED: set[str] = set()  # Track warned vars

ENV_VAR_MAPPING = {
    "JCODEMUNCH_USE_AI_SUMMARIES": "use_ai_summaries",
    "JCODEMUNCH_MAX_FOLDER_FILES": "max_folder_files",
    "JCODEMUNCH_MAX_INDEX_FILES": "max_index_files",
    "JCODEMUNCH_STALENESS_DAYS": "staleness_days",
    "JCODEMUNCH_MAX_RESULTS": "max_results",
    "JCODEMUNCH_EXTRA_IGNORE_PATTERNS": "extra_ignore_patterns",
    "JCODEMUNCH_EXTRA_EXTENSIONS": "extra_extensions",
    "JCODEMUNCH_CONTEXT_PROVIDERS": "context_providers",
    "JCODEMUNCH_REDACT_SOURCE_ROOT": "redact_source_root",
    "JCODEMUNCH_STATS_FILE_INTERVAL": "stats_file_interval",
    "JCODEMUNCH_SHARE_SAVINGS": "share_savings",
    "JCODEMUNCH_SUMMARIZER_CONCURRENCY": "summarizer_concurrency",
    "JCODEMUNCH_ALLOW_REMOTE_SUMMARIZER": "allow_remote_summarizer",
    "JCODEMUNCH_RATE_LIMIT": "rate_limit",
    "JCODEMUNCH_TRANSPORT": "transport",
    "JCODEMUNCH_HOST": "host",
    "JCODEMUNCH_PORT": "port",
    "JCODEMUNCH_WATCH": "watch",
    "JCODEMUNCH_WATCH_DEBOUNCE_MS": "watch_debounce_ms",
    "JCODEMUNCH_FRESHNESS_MODE": "freshness_mode",
    "JCODEMUNCH_CLAUDE_POLL_INTERVAL": "claude_poll_interval",
    "JCODEMUNCH_LOG_LEVEL": "log_level",
    "JCODEMUNCH_LOG_FILE": "log_file",
}

DEFAULTS = {
    "use_ai_summaries": True,
    "max_folder_files": 2000,
    "max_index_files": 10000,
    "staleness_days": 7,
    "max_results": 500,
    "extra_ignore_patterns": [],
    "extra_extensions": {},
    "context_providers": True,
    "meta_fields": None,  # None = all fields
    "languages": None,  # None = all languages
    "disabled_tools": [],
    "descriptions": {},
    "transport": "stdio",
    "host": "127.0.0.1",
    "port": 8901,
    "rate_limit": 0,
    "watch": False,
    "watch_debounce_ms": 2000,
    "freshness_mode": "relaxed",
    "claude_poll_interval": 5.0,
    "log_level": "WARNING",
    "log_file": None,
    "redact_source_root": False,
    "stats_file_interval": 3,
    "share_savings": True,
    "summarizer_concurrency": 4,
    "allow_remote_summarizer": False,
}

CONFIG_TYPES = {
    "use_ai_summaries": bool,
    "max_folder_files": int,
    "max_index_files": int,
    "staleness_days": int,
    "max_results": int,
    "extra_ignore_patterns": list,
    "extra_extensions": dict,
    "context_providers": bool,
    "meta_fields": (list, type(None)),
    "languages": (list, type(None)),
    "disabled_tools": list,
    "descriptions": dict,
    "transport": str,
    "host": str,
    "port": int,
    "rate_limit": int,
    "watch": bool,
    "watch_debounce_ms": int,
    "freshness_mode": str,
    "claude_poll_interval": float,
    "log_level": str,
    "log_file": (str, type(None)),
    "redact_source_root": bool,
    "stats_file_interval": int,
    "share_savings": bool,
    "summarizer_concurrency": int,
    "allow_remote_summarizer": bool,
}


def _strip_jsonc(text: str) -> str:
    """Strip // and /* */ comments from JSONC, respecting quoted strings.
    
    Also strips trailing commas (common in JSONC but invalid in JSON).
    """
    result, i, n = [], 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            result.append(ch)
            if ch == '\\' and i + 1 < n:
                result.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
        elif ch == '"':
            in_str = True
            result.append(ch)
            i += 1
        elif ch == '/' and i + 1 < n and text[i + 1] == '/':
            # Line comment — strip trailing comma and spaces from previous content
            if result and result[-1] == ',':
                result.pop()
                while result and result[-1] in (' ', '\t'):
                    result.pop()
            end = text.find('\n', i)
            i = n if end == -1 else end
        elif ch == '/' and i + 1 < n and text[i + 1] == '*':
            # Block comment — skip to */
            end = text.find('*/', i + 2)
            if end == -1:
                i = n
            else:
                end_i = end + 2
                if end_i < n and text[end_i] == ',':
                    # Comma immediately after */ — strip it
                    i = end_i + 1
                elif end_i < n and text[end_i] == '\n':
                    # Newline after */ — strip trailing comma only
                    # Walk back to find the last non-whitespace character
                    j = len(result) - 1
                    while j >= 0 and result[j] in (' ', '\t'):
                        j -= 1
                    if j >= 0 and result[j] == ',':
                        result.pop()  # pop comma only
                    i = end_i
                else:
                    i = end_i
        else:
            result.append(ch)
            i += 1
    
    # Post-process: strip trailing commas before } and ]
    # This handles JSONC's allowance of trailing commas
    output = ''.join(result)
    # Use a simple state machine to strip trailing commas
    final = []
    j = 0
    m = len(output)
    while j < m:
        ch = output[j]
        if ch == '"' and (j == 0 or output[j-1] != '\\'):
            # Find end of string
            final.append(ch)
            j += 1
            while j < m:
                final.append(output[j])
                if output[j] == '"' and output[j-1] != '\\':
                    j += 1
                    break
                j += 1
        elif ch in ('}', ']'):
            # Strip trailing whitespace and comma before this
            while final and final[-1] in (' ', '\t', '\n', '\r'):
                final.pop()
            if final and final[-1] == ',':
                final.pop()
            final.append(ch)
            j += 1
        else:
            final.append(ch)
            j += 1
    
    return ''.join(final)


def _validate_type(key: str, value: Any, expected_type: type | tuple) -> bool:
    """Validate value against expected type."""
    if isinstance(expected_type, tuple):
        return isinstance(value, expected_type)
    return isinstance(value, expected_type)


def load_config(storage_path: str | None = None) -> None:
    """Load global config.jsonc. Called once from main()."""
    global _GLOBAL_CONFIG

    # Determine config path
    if storage_path:
        config_path = Path(storage_path) / "config.jsonc"
    else:
        # Respect CODE_INDEX_PATH env var for config file location
        index_path = os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index"))
        config_path = Path(index_path) / "config.jsonc"

    # Auto-create default config if missing
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        template = generate_template()
        config_path.write_text(template, encoding="utf-8")
        logger.info("Created default config at %s", config_path)

    # Load config
    _explicit_keys: set[str] = set()  # Track keys explicitly set in config file
    if config_path.exists():
        try:
            content = config_path.read_text(encoding="utf-8-sig")  # utf-8-sig handles BOM
            stripped = _strip_jsonc(content)
            loaded = json.loads(stripped)

            # Start with defaults, then overlay valid config values
            _GLOBAL_CONFIG = DEFAULTS.copy()
            for key, value in loaded.items():
                if key in CONFIG_TYPES:
                    if _validate_type(key, value, CONFIG_TYPES[key]):
                        # Special validation for languages list
                        if key == "languages" and isinstance(value, list):
                            from .parser.languages import LANGUAGE_REGISTRY
                            valid_langs = []
                            for lang in value:
                                if lang in LANGUAGE_REGISTRY:
                                    valid_langs.append(lang)
                                else:
                                    logger.warning(
                                        "Config key 'languages' contains unknown language '%s'. "
                                        "Known languages: %s...",
                                        lang, list(LANGUAGE_REGISTRY.keys())[:5]
                                    )
                            _GLOBAL_CONFIG[key] = valid_langs
                        else:
                            _GLOBAL_CONFIG[key] = value
                        _explicit_keys.add(key)  # Track explicitly set keys
                    else:
                        logger.warning(
                            f"Config key '{key}' has invalid type. "
                            f"Expected {CONFIG_TYPES[key]}, got {type(value).__name__}. Using default."
                        )
                # Ignore unknown keys silently
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse config.jsonc: {e}")
            _GLOBAL_CONFIG = DEFAULTS.copy()
        except Exception as e:
            logger.error(f"Failed to load config.jsonc: {e}")
            _GLOBAL_CONFIG = DEFAULTS.copy()
    else:
        _GLOBAL_CONFIG = DEFAULTS.copy()

    # Apply env var fallback for keys not explicitly set in config
    _apply_env_var_fallback(_explicit_keys)


def _parse_env_value(value: str, expected_type: type | tuple) -> Any:
    """Parse env var string to expected type."""
    try:
        if expected_type == bool:
            return value.lower() in ("true", "1", "yes", "on")
        elif expected_type == int:
            return int(value)
        elif expected_type == float:
            return float(value)
        elif expected_type == str:
            return value
        elif expected_type == list:
            # Try JSON array first (["*.log", "*.tmp"])
            try:
                return json.loads(value)
            except (ValueError, json.JSONDecodeError):
                # Fall back to legacy comma-separated format (*.log,*.tmp)
                result = []
                for token in value.split(","):
                    token = token.strip()
                    if token:
                        result.append(token)
                return result if result else None
        elif expected_type == dict:
            # Try JSON first (new format: {"ext": "lang", ...})
            try:
                return json.loads(value)
            except (ValueError, json.JSONDecodeError):
                # Fall back to legacy comma-separated format (.ext:lang,.ext:lang)
                result = {}
                for token in value.split(","):
                    token = token.strip()
                    if not token or ":" not in token:
                        continue
                    ext, _, lang = token.partition(":")
                    ext = ext.strip()
                    lang = lang.strip()
                    if ext and lang:
                        result[ext] = lang
                return result if result else None
        else:
            logger.warning(f"Unknown config type %s for env var value: %s", expected_type, value)
            return None
    except (ValueError, json.JSONDecodeError):
        logger.warning(f"Failed to parse env var value: {value}")
        return None
    return value


def _apply_env_var_fallback(explicit_keys: set[str] | None = None) -> None:
    """Apply deprecated env var fallback for keys not explicitly set in config."""
    global _GLOBAL_CONFIG

    if explicit_keys is None:
        explicit_keys = set()

    for env_var, config_key in ENV_VAR_MAPPING.items():
        # Skip if config key was explicitly set in config file
        if config_key in explicit_keys:
            continue

        env_value = os.environ.get(env_var)
        if env_value is not None:
            # Log warning once per var
            if env_var not in _DEPRECATED_ENV_VARS_LOGGED:
                logger.warning(
                    f"Deprecated: Using {env_var} environment variable. "
                    f"This will be removed in v2.0. Use config.jsonc instead."
                )
                _DEPRECATED_ENV_VARS_LOGGED.add(env_var)

            # Parse and apply value
            expected_type = CONFIG_TYPES.get(config_key)
            if expected_type is None:
                continue
            parsed = _parse_env_value(env_value, expected_type)  # type: ignore[arg-type]
            if parsed is not None:
                _GLOBAL_CONFIG[config_key] = parsed


def get(key: str, default: Any = None, repo: str | None = None) -> Any:
    """Get config value. If repo is given, uses merged project config."""
    if repo and repo in _PROJECT_CONFIGS:
        return _PROJECT_CONFIGS[repo].get(key, default)
    return _GLOBAL_CONFIG.get(key, default)


def _content_hash(content: str) -> str:
    """Compute SHA-256 hash of content (first 12 hex chars)."""
    import hashlib
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def load_project_config(source_root: str) -> None:
    """Load and cache .jcodemunch.jsonc for a project.
    
    Uses hash-based caching: if the config file content hasn't changed,
    the cached config is reused. This handles:
    - First-time indexing (no cache)
    - Incremental reindexes (cache hit, no parse)
    - Config file edited (hash changed, reload)
    - File touched but unchanged (hash same, no reload)
    - Index dropped and recreated (cache still valid if file unchanged)
    """
    project_config_path = Path(source_root) / ".jcodemunch.jsonc"
    repo_key = str(Path(source_root).resolve())

    if project_config_path.exists():
        try:
            content = project_config_path.read_text(encoding="utf-8-sig")
            content_hash = _content_hash(content)
            
            # Check if content unchanged (handles touch, mtime-only changes)
            if repo_key in _PROJECT_CONFIGS:
                if _PROJECT_CONFIG_HASHES.get(repo_key) == content_hash:
                    return  # Cache is fresh
            
            stripped = _strip_jsonc(content)
            project_config = json.loads(stripped)

            # Merge over global
            merged = {**_GLOBAL_CONFIG}
            for key, value in project_config.items():
                if key in CONFIG_TYPES:
                    if _validate_type(key, value, CONFIG_TYPES[key]):
                        merged[key] = value
                    else:
                        logger.warning(
                            f"Project config key '{key}' has invalid type. Using global default."
                        )
            _PROJECT_CONFIGS[repo_key] = merged
            _PROJECT_CONFIG_HASHES[repo_key] = content_hash
        except Exception as e:
            logger.warning(f"Failed to load project config: {e}")
            _PROJECT_CONFIGS[repo_key] = _GLOBAL_CONFIG.copy()
    else:
        # No config file - use global defaults
        # Only cache if not already cached (avoids overwriting valid config)
        if repo_key not in _PROJECT_CONFIGS:
            _PROJECT_CONFIGS[repo_key] = _GLOBAL_CONFIG.copy()
        _PROJECT_CONFIG_HASHES.pop(repo_key, None)


def _list_repos_for_config() -> list[dict]:
    """Get list of indexed repos for project config loading.
    
    Deferred import to avoid circular dependency at module load time.
    """
    from .storage.index_store import IndexStore
    storage_path = os.environ.get("CODE_INDEX_PATH", str(Path.home() / ".code-index"))
    store = IndexStore(base_path=storage_path)
    return store.list_repos()


def load_all_project_configs() -> None:
    """Load project configs for all already-indexed local repos.
    
    Called once at server startup after load_config(). Discovers all indexed
    local repos via list_repos() and loads their .jcodemunch.jsonc files.
    Remote repos (empty source_root) are skipped.
    """
    if not _GLOBAL_CONFIG:
        return
    
    try:
        repos = _list_repos_for_config()
        for repo_entry in repos:
            source_root = repo_entry.get("source_root", "")
            if not source_root:
                continue
            repo_key = str(Path(source_root).resolve())
            if repo_key not in _PROJECT_CONFIGS:
                load_project_config(source_root)
    except Exception as e:
        logger.warning("Failed to load project configs at startup: %s", e)


def is_tool_disabled(tool_name: str, repo: str | None = None) -> bool:
    """Check if a tool is in disabled_tools."""
    disabled = get("disabled_tools", [], repo=repo)
    return tool_name in disabled


def is_language_enabled(language: str, repo: str | None = None) -> bool:
    """Check if a language is in the languages list."""
    languages = get("languages", None, repo=repo)
    if languages is None:  # None = all enabled
        return True
    return language in languages


def get_descriptions() -> dict:
    """Get the nested descriptions dict."""
    return _GLOBAL_CONFIG.get("descriptions", {})


def validate_config(config_path: str) -> list[str]:
    """Validate a config.jsonc file and return a list of issue messages.

    Returns an empty list if the config is valid.
    Checks:
    - File exists
    - JSONC parses to valid JSON
    - All keys have correct types
    - Unknown keys are flagged (warning, not error)
    """
    issues: list[str] = []
    path = Path(config_path)

    if not path.exists():
        return [f"Config file not found: {config_path}"]

    try:
        content = path.read_text(encoding="utf-8-sig")  # utf-8-sig handles BOM
        stripped = _strip_jsonc(content)
        loaded = json.loads(stripped)
    except json.JSONDecodeError as e:
        return [f"Config parse error: {e}"]

    # Validate types
    for key, value in loaded.items():
        if key in CONFIG_TYPES:
            if not _validate_type(key, value, CONFIG_TYPES[key]):
                expected = CONFIG_TYPES[key]
                type_name = getattr(expected, "__name__", str(expected))
                issues.append(
                    f"Config key '{key}' has invalid type: "
                    f"expected {type_name}, got {type(value).__name__}"
                )
        else:
            issues.append(f"Config key '{key}' is not recognized (unknown key)")

    return issues


def generate_template() -> str:
    """Return default config.jsonc content."""
    from .parser.languages import LANGUAGE_REGISTRY

    languages_list = list(LANGUAGE_REGISTRY.keys())
    lang_str = ", ".join(f'"{lang}"' for lang in languages_list)

    # All available tools (for disabled_tools reference)
    all_tools = [
        "index_repo", "index_folder", "index_file", "list_repos",
        "get_file_tree", "get_file_outline", "get_symbol", "get_file_content",
        "get_symbols", "search_symbols", "search_text", "search_columns",
        "get_repo_outline", "get_context_bundle", "get_session_stats",
        "get_dependency_graph", "get_symbol_diff", "get_class_hierarchy",
        "get_related_symbols", "get_blast_radius", "suggest_queries",
        "find_importers", "find_references", "check_references",
        "invalidate_cache", "wait_for_fresh",
    ]
    tools_str = "\n    //   ".join(f'"{t}",' for t in all_tools)

    # All available meta_fields (for template documentation)
    meta_fields_list = [
        "timing_ms", "powered_by", "index_stale", "reindex_in_progress",
        "stale_since_ms", "reindex_error", "reindex_failures",
        "candidates_scored", "token_budget", "tokens_used", "tokens_remaining",
    ]
    # Commented-out example for meta_fields section in template (each field on its own line)
    meta_str = "\n  //   ".join(f'"{mf}",' for mf in meta_fields_list)

    return f'''// jcodemunch-mcp configuration
// Global: ~/.code-index/config.jsonc
// Project: {{project_root}}/.jcodemunch.jsonc (optional, overrides global)
//
// All values below show defaults. Uncomment to override.
// Env vars still work as fallback but are deprecated.
{{
  // === Indexing ===
  // "use_ai_summaries": true,
  // "max_folder_files": 2000,
  // "max_index_files": 10000,
  // "staleness_days": 7,
  // "max_results": 500,
  // "extra_ignore_patterns": [],
  // "extra_extensions": {{}},
  // "context_providers": true,

  // === Meta Response Control ===
  // Allowlist of _meta fields to include in responses.
  // Empty list = no _meta at all (maximum token savings).
  // Absent/null = all fields included (backward compatible default).
  // Uncomment and set to a list of field names to include only those fields.
  // Available fields:{meta_str}
  // "meta_fields": [
  //   "timing_ms",
  //   "powered_by"
  // ],
  "meta_fields": null,

  // === Languages ===
  // All supported languages. Comment out to disable a language
  // and its dependent features (e.g. "sql" disables dbt parsing
  // and search_columns tool).
  "languages": [{lang_str}],

  // === Disabled Tools ===
  // Global: tools listed here are removed from the schema entirely.
  // Project: tools listed here are rejected at call_tool() with an
  //   explanatory error (schema is global, can't be changed per-project).
  // Default: empty (all tools enabled). Uncomment to disable specific tools.
  // Available tools: {tools_str}
  "disabled_tools": [],

  // === Descriptions ===
  // Append text to shortened tool/param descriptions.
  // Empty string = use hardcoded minimal base only.
  // _tool = tool-level description, other keys = param names.
  // _shared applies across all tools (tool-specific overrides _shared).
  // Tools not listed here keep their full current descriptions unchanged.
  "descriptions": {{
    // === Example: Uncomment to enable ===
    // "search_symbols": {{
    //   "_tool": "",
    //   "debug": "",
    //   "detail_level": "",
    //   "language": ""
    // }},
    // "find_importers": {{ "_tool": "" }},
    // "find_references": {{ "_tool": "" }},
    // "get_blast_radius": {{ "_tool": "" }},
    // "get_context_bundle": {{ "_tool": "" }},
    // "suggest_queries": {{ "_tool": "" }},
    // "_shared": {{ "repo": "" }}
  }}

  // === Transport ===
  // "transport": "stdio",
  // "host": "127.0.0.1",
  // "port": 8901,
  // "rate_limit": 0,

  // === Watcher ===
  // "watch": false,
  // "watch_debounce_ms": 2000,
  // "freshness_mode": "relaxed",
  // "claude_poll_interval": 5.0,

  // === Logging ===
  // "log_level": "WARNING",
  // "log_file": null,

  // === Privacy & Telemetry ===
  // "redact_source_root": false,
  // "stats_file_interval": 3,
  // "share_savings": true,
  // "summarizer_concurrency": 4,
  // "allow_remote_summarizer": false
}}
'''
