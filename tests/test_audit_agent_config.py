"""Tests for audit_agent_config tool."""

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from jcodemunch_mcp.tools.audit_agent_config import (
    _discover_files,
    _estimate_tokens,
    _extract_file_refs,
    _extract_symbol_refs,
    _find_duplicate_runs,
    _find_line,
    _check_bloat,
    _check_dead_paths,
    _check_redundancy,
    _check_scope_leaks,
    _check_stale_symbols,
    _fuzzy_suggest,
    audit_agent_config,
)


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def test_estimate_tokens_nonempty():
    t = _estimate_tokens("hello world this is a test")
    assert t > 0


def test_estimate_tokens_empty():
    t = _estimate_tokens("")
    assert t >= 0


# ---------------------------------------------------------------------------
# Reference extraction
# ---------------------------------------------------------------------------

def test_extract_symbol_refs_backtick():
    text = "Use `handleAuth` to authenticate. Also check `validateInput`."
    refs = _extract_symbol_refs(text)
    assert "handleAuth" in refs
    assert "validateInput" in refs


def test_extract_symbol_refs_keyword():
    text = "function processOrder should be called first"
    refs = _extract_symbol_refs(text)
    assert "processOrder" in refs


def test_extract_symbol_refs_ignores_urls():
    text = "See `https://example.com` for details"
    refs = _extract_symbol_refs(text)
    assert not any("https" in r for r in refs)


def test_extract_symbol_refs_dotted():
    text = "Call `auth.middleware.validate` for validation"
    refs = _extract_symbol_refs(text)
    assert "auth.middleware.validate" in refs


def test_extract_file_refs():
    text = "Check src/utils/helpers.js for the implementation"
    refs = _extract_file_refs(text)
    assert "src/utils/helpers.js" in refs


def test_extract_file_refs_dotslash():
    text = "Edit ./src/config.py to change settings"
    refs = _extract_file_refs(text)
    assert any("src/config.py" in r for r in refs)


def test_extract_file_refs_no_false_positive():
    text = "This is a normal sentence without file paths."
    refs = _extract_file_refs(text)
    assert len(refs) == 0


# ---------------------------------------------------------------------------
# _find_line
# ---------------------------------------------------------------------------

def test_find_line():
    text = "line one\nline two\nline three"
    assert _find_line(text, "two") == 2
    assert _find_line(text, "missing") is None


# ---------------------------------------------------------------------------
# Duplicate run detection
# ---------------------------------------------------------------------------

def test_find_duplicate_runs():
    a = ["alpha", "beta", "gamma", "delta"]
    b = ["alpha", "beta", "gamma", "epsilon"]
    runs = _find_duplicate_runs(a, b, min_run=3)
    assert len(runs) == 1
    assert runs[0]["count"] == 3


def test_find_duplicate_runs_no_match():
    a = ["alpha", "beta"]
    b = ["gamma", "delta"]
    runs = _find_duplicate_runs(a, b, min_run=3)
    assert len(runs) == 0


def test_find_duplicate_runs_short():
    a = ["alpha", "beta"]
    b = ["alpha", "beta"]
    runs = _find_duplicate_runs(a, b, min_run=3)
    assert len(runs) == 0  # Only 2 matching, min_run=3


# ---------------------------------------------------------------------------
# Fuzzy suggestion
# ---------------------------------------------------------------------------

def test_fuzzy_suggest_close():
    candidates = {"handleAuth", "processOrder", "validateInput"}
    assert _fuzzy_suggest("handleAut", candidates) == "handleAuth"


def test_fuzzy_suggest_no_match():
    candidates = {"handleAuth", "processOrder"}
    assert _fuzzy_suggest("zzzzzzz", candidates) is None


def test_fuzzy_suggest_exact():
    candidates = {"handleAuth"}
    assert _fuzzy_suggest("handleAuth", candidates) == "handleAuth"


# ---------------------------------------------------------------------------
# Check: bloat
# ---------------------------------------------------------------------------

def test_check_bloat_small():
    f = {"path": "test.md", "content": "small", "tokens": 50}
    assert _check_bloat(f) == []


def test_check_bloat_large():
    f = {"path": "test.md", "content": "x " * 2000, "tokens": 2500}
    findings = _check_bloat(f)
    assert len(findings) >= 1
    assert findings[0]["category"] == "bloat"


def test_check_bloat_code_blocks():
    blocks = "```\ncode here\n```\n" * 5
    content = "intro\n" + blocks
    f = {"path": "test.md", "content": content, "tokens": 500}
    findings = _check_bloat(f)
    assert any(f["category"] == "bloat" and "code block" in f["message"] for f in findings)


# ---------------------------------------------------------------------------
# Check: scope leaks
# ---------------------------------------------------------------------------

def test_check_scope_leaks_global_with_framework():
    f = {
        "path": "~/.claude/CLAUDE.md",
        "scope": "global",
        "content": "Always use Django admin for database management",
    }
    findings = _check_scope_leaks(f)
    assert len(findings) == 1
    assert findings[0]["category"] == "scope_leak"


def test_check_scope_leaks_project_ignored():
    f = {
        "path": "CLAUDE.md",
        "scope": "project",
        "content": "Always use Django admin for database management",
    }
    assert _check_scope_leaks(f) == []


# ---------------------------------------------------------------------------
# Check: stale symbols
# ---------------------------------------------------------------------------

def test_check_stale_symbols_found():
    f = {
        "path": "CLAUDE.md",
        "content": "Use `handleAuth` for authentication",
    }
    names = {"authenticateUser", "processOrder"}
    findings = _check_stale_symbols(f, None, names)
    assert len(findings) == 1
    assert "handleAuth" in findings[0]["message"]


def test_check_stale_symbols_present():
    f = {
        "path": "CLAUDE.md",
        "content": "Use `handleAuth` for authentication",
    }
    names = {"handleAuth", "processOrder"}
    findings = _check_stale_symbols(f, None, names)
    assert len(findings) == 0


def test_check_stale_symbols_with_suggestion():
    f = {
        "path": "CLAUDE.md",
        "content": "Use `handleAut` for authentication",
    }
    names = {"handleAuth", "processOrder"}
    findings = _check_stale_symbols(f, None, names)
    assert len(findings) == 1
    assert "handleAuth" in findings[0]["message"]
    assert "Did you mean" in findings[0]["message"]


# ---------------------------------------------------------------------------
# Check: dead paths
# ---------------------------------------------------------------------------

def test_check_dead_paths_missing():
    f = {
        "path": "CLAUDE.md",
        "content": "Check src/utils/helpers.js for the implementation",
    }
    source_files = {"src/auth.js", "src/server.js"}
    findings = _check_dead_paths(f, source_files, "")
    assert len(findings) == 1
    assert "src/utils/helpers.js" in findings[0]["message"]


def test_check_dead_paths_present():
    f = {
        "path": "CLAUDE.md",
        "content": "Check src/utils/helpers.js for the implementation",
    }
    source_files = {"src/utils/helpers.js", "src/server.js"}
    findings = _check_dead_paths(f, source_files, "")
    assert len(findings) == 0


# ---------------------------------------------------------------------------
# Check: redundancy
# ---------------------------------------------------------------------------

def test_check_redundancy_duplicate():
    global_f = {
        "path": "~/.claude/CLAUDE.md",
        "scope": "global",
        "content": "rule one\nrule two\nrule three\nrule four\n",
    }
    project_f = {
        "path": "CLAUDE.md",
        "scope": "project",
        "content": "rule one\nrule two\nrule three\nrule four\nextra stuff\n",
    }
    findings = _check_redundancy([global_f, project_f])
    assert len(findings) >= 1
    assert findings[0]["category"] == "redundancy"


def test_check_redundancy_no_duplicate():
    global_f = {
        "path": "~/.claude/CLAUDE.md",
        "scope": "global",
        "content": "global rule alpha\n",
    }
    project_f = {
        "path": "CLAUDE.md",
        "scope": "project",
        "content": "project rule beta\n",
    }
    findings = _check_redundancy([global_f, project_f])
    assert len(findings) == 0


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_discover_files_project(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Test", encoding="utf-8")
    cursorrules = tmp_path / ".cursorrules"
    cursorrules.write_text("rule 1", encoding="utf-8")

    files = _discover_files(str(tmp_path))
    project_files = [f for f in files if f["scope"] == "project"]
    assert any("CLAUDE.md" in f["path"] for f in project_files)
    assert any(".cursorrules" in f["path"] for f in project_files)


def test_discover_files_empty(tmp_path):
    files = _discover_files(str(tmp_path))
    # May find global files but no project files
    project_files = [f for f in files if f["scope"] == "project"]
    assert len(project_files) == 0


def test_discover_files_project_rules_dir(tmp_path):
    """Rules in .claude/rules/*.md should be discovered as project-scoped."""
    rules_dir = tmp_path / ".claude" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "guardrails.md").write_text("# Guardrails\nNo destructive actions.", encoding="utf-8")
    (rules_dir / "search-opsec.md").write_text("# Search OPSEC\nDon't leak strategy.", encoding="utf-8")
    # Non-.md files should be ignored
    (rules_dir / "notes.txt").write_text("not a rule", encoding="utf-8")

    files = _discover_files(str(tmp_path))
    project_rules = [f for f in files if f["scope"] == "project" and "rule:" in f["description"]]
    assert len(project_rules) == 2
    descriptions = {f["description"] for f in project_rules}
    assert "project rule: guardrails" in descriptions
    assert "project rule: search-opsec" in descriptions


# ---------------------------------------------------------------------------
# Full audit
# ---------------------------------------------------------------------------

def test_audit_no_files(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jcodemunch_mcp.tools.audit_agent_config._discover_files",
        lambda p: [],
    )
    result = audit_agent_config(project_path=str(tmp_path))
    assert result["files_scanned"] == 0
    assert result["total_tokens"] == 0


def test_audit_with_files(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("Use `myFunction` for processing", encoding="utf-8")

    result = audit_agent_config(project_path=str(tmp_path))
    assert result["files_scanned"] >= 1
    assert result["total_tokens"] > 0
    assert "token_breakdown" in result
    assert "findings" in result


def test_audit_returns_sorted_findings(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("x " * 2000, encoding="utf-8")  # triggers bloat

    result = audit_agent_config(project_path=str(tmp_path))
    severities = [f["severity"] for f in result["findings"]]
    # Warnings should come before info
    if severities:
        warning_idx = [i for i, s in enumerate(severities) if s == "warning"]
        info_idx = [i for i, s in enumerate(severities) if s == "info"]
        if warning_idx and info_idx:
            assert max(warning_idx) < min(info_idx)


def test_audit_finding_counts(tmp_path):
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("x " * 2000, encoding="utf-8")

    result = audit_agent_config(project_path=str(tmp_path))
    total = result["finding_counts"]["warning"] + result["finding_counts"]["info"]
    assert total == len(result["findings"])
