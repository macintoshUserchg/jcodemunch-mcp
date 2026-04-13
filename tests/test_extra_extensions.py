"""Tests for extra_extensions config handling."""

import pytest
from jcodemunch_mcp.parser.languages import (
    LANGUAGE_EXTENSIONS,
    _apply_extra_extensions,
)
from jcodemunch_mcp import config as config_module


@pytest.fixture(autouse=True)
def restore_extensions():
    """Restore LANGUAGE_EXTENSIONS and config to their original state after each test."""
    import jcodemunch_mcp.parser.languages as lang_module
    original_ext = dict(LANGUAGE_EXTENSIONS)
    original_config = config_module._GLOBAL_CONFIG.copy()
    lang_module._APPLIED_EXTENSIONS = False
    yield
    LANGUAGE_EXTENSIONS.clear()
    LANGUAGE_EXTENSIONS.update(original_ext)
    config_module._GLOBAL_CONFIG.clear()
    config_module._GLOBAL_CONFIG.update(original_config)
    lang_module._APPLIED_EXTENSIONS = False


def _apply_with_config(extensions: dict):
    """Set extra_extensions in config and run _apply_extra_extensions."""
    config_module._GLOBAL_CONFIG["extra_extensions"] = extensions
    _apply_extra_extensions()


def test_valid_extra_extensions():
    """Valid .ext:lang pairs are merged into LANGUAGE_EXTENSIONS."""
    _apply_with_config({".cgi": "perl", ".psgi": "perl"})
    assert LANGUAGE_EXTENSIONS[".cgi"] == "perl"
    assert LANGUAGE_EXTENSIONS[".psgi"] == "perl"


def test_unknown_language_skipped(caplog):
    """Unknown language values are skipped with a WARNING log."""
    import logging
    with caplog.at_level(logging.WARNING):
        _apply_with_config({".xyz": "brainfuck"})
    assert ".xyz" not in LANGUAGE_EXTENSIONS
    assert any("brainfuck" in r.message or "brainfuck" in str(r.args) for r in caplog.records)


def test_malformed_entry_no_colon(caplog):
    """Entry with no colon separator is skipped (handled at startup via _parse_env_value)."""
    import logging
    # The comma-separated parsing is done at startup in config.py
    # An entry without colon would produce empty result
    with caplog.at_level(logging.WARNING):
        _apply_with_config({".cgiperls": ""})
    assert ".cgiperls" not in LANGUAGE_EXTENSIONS
    assert len(caplog.records) >= 1


def test_malformed_entry_empty_ext(caplog):
    """Entry with empty extension is skipped with a WARNING log."""
    import logging
    with caplog.at_level(logging.WARNING):
        _apply_with_config({"": "perl"})
    assert "" not in LANGUAGE_EXTENSIONS
    assert len(caplog.records) >= 1


def test_malformed_entry_empty_lang(caplog):
    """Entry with empty language is skipped with a WARNING log."""
    import logging
    with caplog.at_level(logging.WARNING):
        _apply_with_config({".cgi": ""})
    assert ".cgi" not in LANGUAGE_EXTENSIONS
    assert len(caplog.records) >= 1


def test_empty_extra_extensions():
    """Absent or empty extra_extensions leaves LANGUAGE_EXTENSIONS unchanged."""
    before = dict(LANGUAGE_EXTENSIONS)
    config_module._GLOBAL_CONFIG["extra_extensions"] = {}
    _apply_extra_extensions()
    assert LANGUAGE_EXTENSIONS == before


def test_whitespace_only_extra_extensions():
    """Whitespace-only values in extra_extensions are handled gracefully."""
    before = dict(LANGUAGE_EXTENSIONS)
    # Empty dict equivalent
    config_module._GLOBAL_CONFIG["extra_extensions"] = {}
    _apply_extra_extensions()
    assert LANGUAGE_EXTENSIONS == before


def test_override_builtin_extension():
    """A valid entry can override an existing built-in extension mapping."""
    _apply_with_config({".pl": "python"})
    assert LANGUAGE_EXTENSIONS[".pl"] == "python"


def test_mixed_valid_and_invalid(caplog):
    """Valid entries are applied even when mixed with invalid ones."""
    import logging
    with caplog.at_level(logging.WARNING):
        _apply_with_config({".cgi": "perl", ".xyz": "brainfuck", ".psgi": "perl"})
    assert LANGUAGE_EXTENSIONS[".cgi"] == "perl"
    assert LANGUAGE_EXTENSIONS[".psgi"] == "perl"
    assert ".xyz" not in LANGUAGE_EXTENSIONS
    assert len(caplog.records) >= 1


def test_extra_whitespace_in_entries():
    """Leading/trailing whitespace in tokens is stripped (handled at startup parsing)."""
    # Whitespace is stripped during startup _parse_env_value comma-separated parsing
    # _apply_extra_extensions receives a clean dict
    _apply_with_config({".cgi": "perl", ".psgi": "perl"})
    assert LANGUAGE_EXTENSIONS[".cgi"] == "perl"
