"""Tests for JSONC config parsing."""

import json
import tempfile
from pathlib import Path

import pytest

from src.jcodemunch_mcp.config import _strip_jsonc


class TestJSONCParser:
    """Test JSONC comment stripping."""

    def test_strips_line_comments(self):
        """Should strip // comments to end of line."""
        text = '{"key": "value" // this is a comment\n}'
        result = _strip_jsonc(text)
        # Space before // is preserved; valid JSON
        assert result == '{"key": "value" \n}'
        import json
        json.loads(result)  # Must be valid JSON

    def test_strips_line_comment_no_trailing_newline(self):
        """Should strip // comment at end of file."""
        text = '{"key": "value"} // comment'
        result = _strip_jsonc(text)
        # Space before // is preserved
        assert result == '{"key": "value"} '
        import json
        json.loads(result)  # Must be valid JSON

    def test_strips_block_comments(self):
        """Should strip /* */ block comments."""
        text = '{"key" /* comment */: "value"}'
        result = _strip_jsonc(text)
        assert result == '{"key" : "value"}'

    def test_strips_multiline_block_comments(self):
        """Should strip multiline /* */ comments."""
        text = '''{
    "key": "value" /* this is
    a multiline
    comment */
}'''
        result = _strip_jsonc(text)
        assert '"key"' in result
        assert 'this is' not in result

    def test_preserves_strings_with_comment_chars(self):
        """Should not strip // or /* inside quoted strings."""
        text = '{"url": "http://example.com", "note": "use /* here*/"}'
        result = _strip_jsonc(text)
        assert result == text  # Should be unchanged


class TestConfigDefaults:
    """Test default config values."""

    def test_default_max_folder_files(self):
        """Should default to 2000 max folder files."""
        from src.jcodemunch_mcp.config import DEFAULTS
        assert DEFAULTS["max_folder_files"] == 2000

    def test_default_max_index_files(self):
        """Should default to 10000 max index files."""
        from src.jcodemunch_mcp.config import DEFAULTS
        assert DEFAULTS["max_index_files"] == 10000

    def test_default_languages_is_none(self):
        """Should default to None (all languages enabled)."""
        from src.jcodemunch_mcp.config import DEFAULTS
        assert DEFAULTS["languages"] is None

    def test_default_disabled_tools_is_empty(self):
        """Should default to empty list (all tools enabled)."""
        from src.jcodemunch_mcp.config import DEFAULTS
        assert DEFAULTS["disabled_tools"] == []


class TestConfigLoading:
    """Test config file loading."""

    def test_missing_file_uses_defaults(self, monkeypatch):
        """Should use defaults when config file doesn't exist."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        # Clear any existing config
        _GLOBAL_CONFIG.clear()

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            non_existent = Path(tmpdir) / "nonexistent" / "config.jsonc"
            monkeypatch.setenv("CODE_INDEX_PATH", str(Path(tmpdir) / "nonexistent"))

            load_config(str(Path(tmpdir) / "nonexistent"))

            assert get("max_folder_files") == 2000
            assert get("use_ai_summaries") is True

    def test_loads_valid_config(self, monkeypatch):
        """Should load valid JSONC config."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('''{
                "max_folder_files": 5000,
                "use_ai_summaries": false
            }''')

            load_config(tmpdir)

            assert get("max_folder_files") == 5000
            assert get("use_ai_summaries") is False

    def test_type_mismatch_logs_warning_and_uses_default(self, monkeypatch, caplog):
        """Should log warning and use default on type mismatch."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG
        import logging

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{ "max_folder_files": "2000" }')  # String instead of int

            with caplog.at_level(logging.WARNING):
                load_config(tmpdir)

            # Should have logged a warning
            assert "invalid type" in caplog.text.lower()

            # Should use default
            assert get("max_folder_files") == 2000


class TestProjectConfig:
    """Test project-level config loading."""

    def test_project_config_merges_over_global(self):
        """Should merge project config over global config."""
        from src.jcodemunch_mcp.config import (
            load_config, load_project_config, get,
            _GLOBAL_CONFIG, _PROJECT_CONFIGS
        )

        _GLOBAL_CONFIG.clear()
        _PROJECT_CONFIGS.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Set up global config
            global_config = Path(tmpdir) / "global" / "config.jsonc"
            global_config.parent.mkdir()
            global_config.write_text('{"max_folder_files": 2000, "use_ai_summaries": true}')

            load_config(str(global_config.parent))

            # Set up project config
            project_root = Path(tmpdir) / "project"
            project_root.mkdir()
            project_config = project_root / ".jcodemunch.jsonc"
            project_config.write_text('{"max_folder_files": 5000}')

            load_project_config(str(project_root))

            # Project value should override
            repo_key = str(project_root.resolve())
            assert get("max_folder_files", repo=repo_key) == 5000
            # Non-overridden values should come from global
            assert get("use_ai_summaries", repo=repo_key) is True


class TestConfigGetters:
    """Test config getter functions."""

    def test_is_tool_disabled(self):
        """Should return True if tool is in disabled_tools."""
        from src.jcodemunch_mcp.config import (
            load_config, is_tool_disabled, _GLOBAL_CONFIG
        )

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"disabled_tools": ["index_repo", "search_columns"]}')

            load_config(tmpdir)

            assert is_tool_disabled("index_repo") is True
            assert is_tool_disabled("search_columns") is True
            assert is_tool_disabled("get_file_tree") is False

    def test_is_language_enabled_all_enabled(self):
        """Should return True for all languages when languages is None."""
        from src.jcodemunch_mcp.config import (
            load_config, is_language_enabled, _GLOBAL_CONFIG, DEFAULTS
        )

        _GLOBAL_CONFIG.clear()
        _GLOBAL_CONFIG.update(DEFAULTS)  # languages = None

        assert is_language_enabled("python") is True
        assert is_language_enabled("sql") is True

    def test_is_language_enabled_filtered(self):
        """Should return False for disabled languages."""
        from src.jcodemunch_mcp.config import load_config, is_language_enabled, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"languages": ["python", "javascript"]}')

            load_config(tmpdir)

            assert is_language_enabled("python") is True
            assert is_language_enabled("javascript") is True
            assert is_language_enabled("sql") is False


class TestTemplateGeneration:
    """Test config template generation."""

    def test_generate_template_returns_valid_jsonc(self):
        """Should generate valid JSONC template."""
        from src.jcodemunch_mcp.config import generate_template

        template = generate_template()

        # Should be parseable after stripping comments
        from src.jcodemunch_mcp.config import _strip_jsonc
        stripped = _strip_jsonc(template)
        parsed = json.loads(stripped)

        assert "languages" in parsed
        assert "disabled_tools" in parsed
        assert "meta_fields" in parsed

    def test_template_languages_synced_from_registry(self):
        """Should include all languages from LANGUAGE_REGISTRY."""
        from src.jcodemunch_mcp.config import generate_template
        from src.jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY

        template = generate_template()
        stripped = _strip_jsonc(template)
        parsed = json.loads(stripped)

        # All registry languages should be in template
        for lang in LANGUAGE_REGISTRY.keys():
            assert lang in parsed["languages"]


class TestGetDescriptions:
    """Test get_descriptions() function."""

    def test_returns_descriptions_dict(self):
        """Should return descriptions dict from config."""
        from src.jcodemunch_mcp.config import load_config, get_descriptions, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('''{
                "descriptions": {
                    "search_symbols": {"_tool": "custom"},
                    "_shared": {"repo": "shared desc"}
                }
            }''')

            load_config(tmpdir)

            result = get_descriptions()
            assert isinstance(result, dict)
            assert "search_symbols" in result
            assert "_shared" in result

    def test_returns_empty_dict_when_absent(self):
        """Should return empty dict when descriptions key absent."""
        from src.jcodemunch_mcp.config import load_config, get_descriptions, _GLOBAL_CONFIG, DEFAULTS

        _GLOBAL_CONFIG.clear()
        _GLOBAL_CONFIG.update(DEFAULTS)  # descriptions = {}

        result = get_descriptions()
        assert result == {}


class TestEnvVarFallback:
    """Test deprecated env var fallback with warnings."""

    def test_env_var_used_when_config_key_absent(self, monkeypatch, caplog):
        """Should use env var value when config key not set."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG, _DEPRECATED_ENV_VARS_LOGGED
        import logging

        _GLOBAL_CONFIG.clear()
        _DEPRECATED_ENV_VARS_LOGGED.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Config without max_folder_files
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"use_ai_summaries": false}')

            # Env var set for max_folder_files
            monkeypatch.setenv("JCODEMUNCH_MAX_FOLDER_FILES", "5000")

            with caplog.at_level(logging.WARNING):
                load_config(tmpdir)

            # Should use env var value
            assert get("max_folder_files") == 5000

    def test_warning_logged_once_per_deprecated_var(self, monkeypatch, caplog):
        """Should log one warning per deprecated env var found."""
        from src.jcodemunch_mcp.config import load_config, _GLOBAL_CONFIG, _DEPRECATED_ENV_VARS_LOGGED
        import logging

        _GLOBAL_CONFIG.clear()
        _DEPRECATED_ENV_VARS_LOGGED.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{}')  # Empty config

            monkeypatch.setenv("JCODEMUNCH_MAX_FOLDER_FILES", "5000")
            monkeypatch.setenv("JCODEMUNCH_MAX_INDEX_FILES", "15000")

            with caplog.at_level(logging.WARNING):
                load_config(tmpdir)

            # Should have warnings (one per env var)
            warning_count = sum(1 for rec in caplog.records if rec.levelname == "WARNING")
            assert warning_count >= 2

            # Each warning should mention v2.0 removal
            for rec in caplog.records:
                if "deprecated" in rec.message.lower():
                    assert "v2.0" in rec.message

    def test_no_warning_when_config_key_present(self, monkeypatch, caplog):
        """Should NOT log warning when config key is present."""
        from src.jcodemunch_mcp.config import load_config, get, _GLOBAL_CONFIG, _DEPRECATED_ENV_VARS_LOGGED
        import logging

        _GLOBAL_CONFIG.clear()
        _DEPRECATED_ENV_VARS_LOGGED.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": 3000}')

            # Env var set but should be ignored (config takes precedence)
            monkeypatch.setenv("JCODEMUNCH_MAX_FOLDER_FILES", "5000")

            with caplog.at_level(logging.WARNING):
                load_config(tmpdir)

            # Should NOT log deprecation warning (config key present)
            assert "deprecated" not in caplog.text.lower()

            # Config value should be used, not env var
            assert get("max_folder_files") == 3000


# ── Config file validation ────────────────────────────────────────────────────

class TestConfigValidation:
    """Test validate_config() function in config module."""

    def test_validate_valid_config_returns_empty(self):
        """Should return no issues for a valid config."""
        from src.jcodemunch_mcp.config import validate_config, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": 5000}')
            issues = validate_config(str(config_path))
            assert issues == []

    def test_validate_invalid_json_returns_parse_error(self):
        """Should report JSON parse errors."""
        from src.jcodemunch_mcp.config import validate_config, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": }')  # Invalid JSON
            issues = validate_config(str(config_path))
            assert any("parse" in i.lower() for i in issues)

    def test_validate_type_mismatch_returns_warning(self):
        """Should report type mismatches."""
        from src.jcodemunch_mcp.config import validate_config, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": "not_an_int"}')
            issues = validate_config(str(config_path))
            assert any("type" in i.lower() or "invalid" in i.lower() for i in issues)

    def test_validate_unknown_key_returns_warning(self):
        """Should warn about unknown config keys."""
        from src.jcodemunch_mcp.config import validate_config, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": 5000, "unknown_key": true}')
            issues = validate_config(str(config_path))
            assert any("unknown" in i.lower() for i in issues)

    def test_validate_missing_file_returns_error(self):
        """Should report when config file is missing."""
        from src.jcodemunch_mcp.config import validate_config, _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "nonexistent.jsonc"
            issues = validate_config(str(missing))
            assert any("not found" in i.lower() for i in issues)


class TestServerConfigCheck:
    """Test that `config --check` validates the config file."""

    def test_run_config_check_reports_config_parse_error(self, capsys, monkeypatch):
        """Should report config file parse errors in --check output."""
        from src.jcodemunch_mcp.config import _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": }')  # Invalid JSON

            monkeypatch.setenv("CODE_INDEX_PATH", tmpdir)

            from src.jcodemunch_mcp.server import _run_config
            with pytest.raises(SystemExit) as exc_info:
                _run_config(check=True)
            assert exc_info.value.code == 1

            captured = capsys.readouterr().out
            # Should mention config.jsonc and parse error
            assert "config" in captured.lower()
            assert "parse" in captured.lower()

    def test_run_config_check_reports_type_error(self, capsys, monkeypatch):
        """Should report config type errors in --check output."""
        from src.jcodemunch_mcp.config import _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": "wrong_type"}')

            monkeypatch.setenv("CODE_INDEX_PATH", tmpdir)

            from src.jcodemunch_mcp.server import _run_config
            with pytest.raises(SystemExit) as exc_info:
                _run_config(check=True)
            assert exc_info.value.code == 1

            captured = capsys.readouterr().out
            assert "max_folder_files" in captured.lower()
            assert "type" in captured.lower()

    def test_run_config_check_passes_for_valid_config(self, capsys, monkeypatch):
        """Should pass checks when config is valid."""
        from src.jcodemunch_mcp.config import _GLOBAL_CONFIG

        _GLOBAL_CONFIG.clear()

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.jsonc"
            config_path.write_text('{"max_folder_files": 5000}')

            monkeypatch.setenv("CODE_INDEX_PATH", tmpdir)

            from src.jcodemunch_mcp.server import _run_config
            _run_config(check=True)

            captured = capsys.readouterr().out
            # Should NOT mention config errors
            assert "config error" not in captured.lower()
            assert "parse error" not in captured.lower()


class TestLoadConfigWiredIntoMain:
    """Test that load_config() is called during server startup."""

    @pytest.mark.asyncio
    async def test_main_calls_load_config_for_serve_command(self, monkeypatch, tmp_path):
        """main() should call load_config() when serving."""
        from src.jcodemunch_mcp.config import _GLOBAL_CONFIG, DEFAULTS

        _GLOBAL_CONFIG.clear()
        _GLOBAL_CONFIG.update(DEFAULTS)

        # Create a temp config with a distinctive value
        config_path = tmp_path / "config.jsonc"
        config_path.write_text('{"max_folder_files": 9999}')
        monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))

        # Track whether load_config was called
        call_count = 0

        # Import fresh — need to get the original reference before patching
        import src.jcodemunch_mcp.config as cfg_module
        real_load = cfg_module.load_config

        def tracked_load(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return real_load(*args, **kwargs)

        cfg_module.load_config = tracked_load

        # Patch sys.exit to prevent exit
        monkeypatch.setattr("sys.exit", lambda code=0: None)

        # Patch asyncio.run to avoid starting the actual server
        async def fake_asyncio_run(coro):
            # Just run the coroutine briefly, don't actually start the server
            pass

        monkeypatch.setattr("asyncio.run", fake_asyncio_run)

        # Also patch the MCP server.run to prevent actual startup
        import src.jcodemunch_mcp.server as server_module
        async def fake_server_run(*args, **kwargs):
            pass
        monkeypatch.setattr(server_module.server, "run", fake_server_run)

        from src.jcodemunch_mcp.server import main
        main(["serve"])

        assert call_count >= 1, "load_config should have been called during serve"

    @pytest.mark.asyncio
    async def test_config_loaded_before_list_tools(self, monkeypatch, tmp_path):
        """After main() starts, config should be loaded and usable by list_tools."""
        # Use the SAME module object that server.py imports
        import src.jcodemunch_mcp.config as cfg_module
        cfg_module._GLOBAL_CONFIG.clear()
        cfg_module._GLOBAL_CONFIG.update({"max_folder_files": 2000})  # default

        config_path = tmp_path / "config.jsonc"
        config_path.write_text('{"max_folder_files": 7777}')
        monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))

        monkeypatch.setattr("sys.exit", lambda code=0: None)

        async def fake_asyncio_run(coro):
            pass

        monkeypatch.setattr("asyncio.run", fake_asyncio_run)

        import src.jcodemunch_mcp.server as server_module
        async def fake_server_run(*args, **kwargs):
            pass
        monkeypatch.setattr(server_module.server, "run", fake_server_run)

        from src.jcodemunch_mcp.server import main
        main(["serve"])

        # After main() runs, config should reflect the file (not just defaults)
        # cfg_module is the same object that server.py's config_module references
        assert cfg_module.get("max_folder_files") == 7777
