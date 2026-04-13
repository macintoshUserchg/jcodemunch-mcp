"""Tests for the gcm CLI (Groq Codebase Q&A)."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from jcodemunch_mcp.groq.config import GcmConfig, DEFAULT_MODEL, FAST_MODEL


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestGcmConfig:
    def test_defaults(self):
        cfg = GcmConfig(groq_api_key="gsk_test")
        assert cfg.model == DEFAULT_MODEL
        assert cfg.token_budget == 8000
        assert cfg.max_answer_tokens == 2048
        assert cfg.groq_api_key == "gsk_test"

    def test_env_var_fallback(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk_from_env")
        cfg = GcmConfig()
        assert cfg.groq_api_key == "gsk_from_env"

    def test_validate_missing_key(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        cfg = GcmConfig(groq_api_key="")
        err = cfg.validate()
        assert err is not None
        assert "GROQ_API_KEY" in err

    def test_validate_ok(self):
        cfg = GcmConfig(groq_api_key="gsk_test")
        assert cfg.validate() is None

    def test_github_token_from_env(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        cfg = GcmConfig(groq_api_key="gsk_test")
        assert cfg.github_token == "ghp_test"


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------

class TestRetriever:
    def test_is_github_repo(self):
        from jcodemunch_mcp.groq.retriever import _is_github_repo
        assert _is_github_repo("pallets/flask") is True
        assert _is_github_repo("facebook/react") is True
        assert _is_github_repo("flask") is False
        assert _is_github_repo(".") is False
        assert _is_github_repo("./src/foo") is False
        assert _is_github_repo("a/b/c") is False  # too many slashes
        assert _is_github_repo("/absolute/path") is False
        assert _is_github_repo("C:\\Users\\foo") is False

    def test_find_indexed_repo_match(self):
        from jcodemunch_mcp.groq.retriever import _find_indexed_repo
        with patch("jcodemunch_mcp.groq.retriever.list_repos") as mock_lr:
            mock_lr.return_value = {
                "repos": [
                    {"repo": "pallets/flask", "display_name": "flask"},
                    {"repo": "facebook/react", "display_name": "react"},
                ]
            }
            assert _find_indexed_repo("pallets/flask") == "pallets/flask"
            assert _find_indexed_repo("flask") == "pallets/flask"
            assert _find_indexed_repo("react") == "facebook/react"
            assert _find_indexed_repo("nonexistent") is None

    def test_retrieve_context_formats(self):
        from jcodemunch_mcp.groq.retriever import retrieve_context
        with patch("jcodemunch_mcp.groq.retriever.get_ranked_context") as mock_rc:
            mock_rc.return_value = {
                "context_items": [
                    {"file": "app.py", "symbol": "create_app", "kind": "function", "source": "def create_app(): ..."},
                    {"file": "auth.py", "symbol": "login", "kind": "function", "source": "def login(): ..."},
                ],
                "tokens_used": 200,
            }
            text, raw = retrieve_context("pallets/flask", "how does auth work?")
            assert "app.py :: create_app" in text
            assert "auth.py :: login" in text
            assert "```" in text
            assert raw["tokens_used"] == 200

    def test_retrieve_context_error(self):
        from jcodemunch_mcp.groq.retriever import retrieve_context
        with patch("jcodemunch_mcp.groq.retriever.get_ranked_context") as mock_rc:
            mock_rc.return_value = {"error": "not found"}
            text, raw = retrieve_context("x/y", "test")
            assert text == ""
            assert "error" in raw


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

class TestInference:
    def test_ask_calls_openai(self):
        cfg = GcmConfig(groq_api_key="gsk_test")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "The answer is 42."
        mock_client.chat.completions.create.return_value = mock_response

        with patch("jcodemunch_mcp.groq.inference._get_client", return_value=mock_client):
            from jcodemunch_mcp.groq.inference import ask
            result = ask(cfg, "context here", "what is this?")
            assert result == "The answer is 42."
            mock_client.chat.completions.create.assert_called_once()
            call_kwargs = mock_client.chat.completions.create.call_args[1]
            assert call_kwargs["model"] == DEFAULT_MODEL
            assert "stream" not in call_kwargs  # non-streaming mode

    def test_ask_stream_yields_tokens(self):
        cfg = GcmConfig(groq_api_key="gsk_test")
        mock_client = MagicMock()

        # Build fake stream chunks
        chunks = []
        for token in ["Hello", " world", "!"]:
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = token
            chunks.append(chunk)

        mock_client.chat.completions.create.return_value = iter(chunks)

        with patch("jcodemunch_mcp.groq.inference._get_client", return_value=mock_client):
            from jcodemunch_mcp.groq.inference import ask_stream
            tokens = list(ask_stream(cfg, "ctx", "q"))
            assert tokens == ["Hello", " world", "!"]


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

class TestCliParser:
    def test_single_question(self):
        from jcodemunch_mcp.groq.cli import _build_parser
        p = _build_parser()
        args = p.parse_args(["how does auth work?", "--repo", "pallets/flask"])
        assert args.question == "how does auth work?"
        assert args.repo == "pallets/flask"
        assert args.model == DEFAULT_MODEL

    def test_fast_flag(self):
        from jcodemunch_mcp.groq.cli import _build_parser
        p = _build_parser()
        args = p.parse_args(["test", "--fast"])
        assert args.model == FAST_MODEL

    def test_chat_flag(self):
        from jcodemunch_mcp.groq.cli import _build_parser
        p = _build_parser()
        args = p.parse_args(["--chat", "--repo", "x/y"])
        assert args.chat is True

    def test_version_flag(self):
        from jcodemunch_mcp.groq.cli import _build_parser
        p = _build_parser()
        args = p.parse_args(["--version"])
        assert args.version is True

    def test_custom_budget(self):
        from jcodemunch_mcp.groq.cli import _build_parser
        p = _build_parser()
        args = p.parse_args(["q", "--budget", "4000"])
        assert args.budget == 4000
