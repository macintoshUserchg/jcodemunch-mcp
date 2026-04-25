"""Tests for v1.79.0 online weight tuning."""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from jcodemunch_mcp.retrieval import tuning as _tuning
from jcodemunch_mcp.storage import token_tracker as tt
from jcodemunch_mcp.tools.tune_weights import tune_weights


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch, tmp_path):
    fresh = tt._State()
    fresh._base_path = str(tmp_path)
    monkeypatch.setattr(tt, "_state", fresh)
    # Clear tuning cache between tests
    monkeypatch.setattr(_tuning, "_cache", {})
    monkeypatch.setattr(_tuning, "_cache_loaded_from", None)
    yield


def _enable(monkeypatch):
    from jcodemunch_mcp import config as _config
    real_get = _config.get

    def patched_get(key, default=None, *args, **kwargs):
        if key == "perf_telemetry_enabled":
            return True
        return real_get(key, default, *args, **kwargs)

    monkeypatch.setattr(_config, "get", patched_get)


def _seed(repo: str, n_with_sem: int, n_without_sem: int, conf_with: float, conf_without: float):
    """Insert ranking_events directly into telemetry.db."""
    for _ in range(n_with_sem):
        tt.record_ranking_event(
            tool="search_symbols", repo=repo, query="x",
            returned_ids=[], confidence=conf_with,
            semantic_used=True, identity_hit=False,
        )
    for _ in range(n_without_sem):
        tt.record_ranking_event(
            tool="search_symbols", repo=repo, query="y",
            returned_ids=[], confidence=conf_without,
            semantic_used=False, identity_hit=False,
        )


class TestGetSemanticWeight:
    def test_explicit_wins(self, tmp_path):
        # Even with no override, an explicit value is returned as-is
        assert _tuning.get_semantic_weight("local/x", explicit=0.7, base_path=str(tmp_path)) == 0.7

    def test_default_when_no_override(self, tmp_path):
        assert _tuning.get_semantic_weight("local/x", base_path=str(tmp_path)) == 0.5

    def test_uses_override_when_present(self, tmp_path):
        path = _tuning._tuning_path(str(tmp_path))
        path.write_text(json.dumps({
            "repos": {"local/x": {"semantic_weight": 0.7}}
        }))
        # Force cache reload
        _tuning._cache_loaded_from = None
        assert _tuning.get_semantic_weight("local/x", base_path=str(tmp_path)) == 0.7


class TestTunerLearn:
    def test_insufficient_events_skip(self, monkeypatch, tmp_path):
        _enable(monkeypatch)
        _seed("local/r", 5, 5, 0.9, 0.5)  # only 10 events
        tuner = _tuning.WeightTuner(base_path=str(tmp_path))
        result = tuner.learn("local/r", min_events=50)
        assert result["applied"] is False
        assert "insufficient_events" in result["reason"]

    def test_no_signal_when_groups_equal(self, monkeypatch, tmp_path):
        _enable(monkeypatch)
        _seed("local/r", 30, 30, 0.7, 0.7)
        tuner = _tuning.WeightTuner(base_path=str(tmp_path))
        result = tuner.learn("local/r", min_events=50)
        assert result["applied"] is False
        assert result["reason"] == "no_significant_signal"

    def test_semantic_helps_bumps_weight(self, monkeypatch, tmp_path):
        _enable(monkeypatch)
        _seed("local/r", 30, 30, 0.9, 0.5)  # semantic helps by 0.4
        tuner = _tuning.WeightTuner(base_path=str(tmp_path))
        result = tuner.learn("local/r", min_events=50)
        assert result["applied"] is True
        # Default 0.5 + step 0.05 = 0.55
        assert result["after"]["semantic_weight"] == pytest.approx(0.55)
        assert result["events"] == 60

    def test_semantic_hurts_drops_weight(self, monkeypatch, tmp_path):
        _enable(monkeypatch)
        _seed("local/r", 30, 30, 0.4, 0.85)  # semantic hurts by 0.45
        tuner = _tuning.WeightTuner(base_path=str(tmp_path))
        result = tuner.learn("local/r", min_events=50)
        assert result["applied"] is True
        assert result["after"]["semantic_weight"] == pytest.approx(0.45)

    def test_dry_run_doesnt_persist(self, monkeypatch, tmp_path):
        _enable(monkeypatch)
        _seed("local/r", 30, 30, 0.9, 0.5)
        tuner = _tuning.WeightTuner(base_path=str(tmp_path))
        result = tuner.learn("local/r", dry_run=True, min_events=50)
        assert result["applied"] is False
        # File should not exist after a dry run
        assert not _tuning._tuning_path(str(tmp_path)).exists()

    def test_persisted_jsonc_is_valid_json(self, monkeypatch, tmp_path):
        _enable(monkeypatch)
        _seed("local/r", 30, 30, 0.9, 0.5)
        tuner = _tuning.WeightTuner(base_path=str(tmp_path))
        tuner.learn("local/r", min_events=50)
        path = _tuning._tuning_path(str(tmp_path))
        text = path.read_text()
        # The persisted file has // comments — strip then JSON-parse
        parsed = json.loads(_tuning._strip_jsonc(text))
        assert "local/r" in parsed["repos"]


class TestTuneWeightsTool:
    def test_no_repos_in_ledger(self, monkeypatch, tmp_path):
        out = tune_weights(storage_path=str(tmp_path))
        assert out["summary"]["repos_examined"] == 0
        assert out["summary"]["applied"] == 0

    def test_runs_for_all_repos_when_repo_omitted(self, monkeypatch, tmp_path):
        _enable(monkeypatch)
        _seed("local/r1", 30, 30, 0.9, 0.5)
        _seed("local/r2", 5, 5, 0.9, 0.5)  # under min_events
        out = tune_weights(storage_path=str(tmp_path), min_events=50)
        repos = {r["repo"]: r for r in out["results"]}
        assert "local/r1" in repos
        assert "local/r2" in repos
        assert repos["local/r1"]["applied"] is True
        assert repos["local/r2"]["applied"] is False
        assert out["summary"]["applied"] == 1

    def test_explain_includes_signals(self, monkeypatch, tmp_path):
        _enable(monkeypatch)
        _seed("local/r", 30, 30, 0.9, 0.5)
        out = tune_weights(repo="local/r", explain=True, storage_path=str(tmp_path), min_events=50)
        result = out["results"][0]
        assert "signals" in result
        assert result["signals"]["events_with_confidence"] == 60

    def test_explain_off_strips_signals(self, monkeypatch, tmp_path):
        _enable(monkeypatch)
        _seed("local/r", 30, 30, 0.9, 0.5)
        out = tune_weights(repo="local/r", explain=False, storage_path=str(tmp_path), min_events=50)
        assert "signals" not in out["results"][0]


class TestServerRegistration:
    def test_tune_weights_in_canonical(self):
        from jcodemunch_mcp.server import _CANONICAL_TOOL_NAMES
        assert "tune_weights" in _CANONICAL_TOOL_NAMES

    def test_tune_weights_in_standard_tier(self):
        from jcodemunch_mcp.server import _TOOL_TIER_STANDARD
        assert "tune_weights" in _TOOL_TIER_STANDARD

    def test_tune_weights_in_default_bundle(self):
        from jcodemunch_mcp.config import DEFAULTS
        assert "tune_weights" in DEFAULTS["tool_tier_bundles"]["standard"]


class TestSemanticWeightOverrideAtQueryTime:
    def test_default_semantic_weight_picks_up_override(self, tmp_path, monkeypatch):
        # Write a tuning file that bumps semantic_weight for local/x
        path = _tuning._tuning_path(str(tmp_path))
        path.write_text(json.dumps({
            "repos": {"local/x": {"semantic_weight": 0.7}}
        }))
        _tuning._cache_loaded_from = None
        # When caller passes the default 0.5, override applies
        assert _tuning.get_semantic_weight("local/x", explicit=None, base_path=str(tmp_path)) == 0.7
        # Explicit pass always wins
        assert _tuning.get_semantic_weight("local/x", explicit=0.3, base_path=str(tmp_path)) == 0.3
