"""Tests for v1.76.0 replay benchmark metrics + harness gate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "benchmarks" / "replay"))

from metrics import (  # noqa: E402
    aggregate,
    dcg,
    mrr_at_k,
    ndcg_at_k,
    recall_at_k,
)


class TestRecall:
    def test_perfect_recall(self):
        assert recall_at_k(["a", "b"], ["a", "b"], k=2) == 1.0

    def test_partial_recall(self):
        assert recall_at_k(["a", "x"], ["a", "b"], k=2) == 0.5

    def test_empty_relevant_returns_zero(self):
        assert recall_at_k(["a"], [], k=2) == 0.0

    def test_k_truncation(self):
        assert recall_at_k(["x", "x", "a"], ["a"], k=2) == 0.0


class TestMRR:
    def test_first_position(self):
        assert mrr_at_k(["a", "b"], ["a"], k=10) == 1.0

    def test_second_position(self):
        assert mrr_at_k(["x", "a"], ["a"], k=10) == 0.5

    def test_third_position(self):
        assert mrr_at_k(["x", "y", "a"], ["a"], k=10) == pytest.approx(1 / 3)

    def test_outside_k_is_zero(self):
        assert mrr_at_k(["x", "y", "z", "a"], ["a"], k=3) == 0.0


class TestNDCG:
    def test_perfect_top_match(self):
        assert ndcg_at_k(["a"], ["a"], k=10) == 1.0

    def test_no_match_is_zero(self):
        assert ndcg_at_k(["x", "y"], ["a"], k=10) == 0.0

    def test_lower_position_lowers_score(self):
        early = ndcg_at_k(["a", "x", "y"], ["a"], k=3)
        late = ndcg_at_k(["x", "y", "a"], ["a"], k=3)
        assert early > late
        assert 0 < late < 1

    def test_dcg_matches_known_value(self):
        # DCG with relevant at pos 0,2 = 1/log2(2) + 1/log2(4) = 1 + 0.5 = 1.5
        assert dcg(["a", "x", "b"], ["a", "b"], k=3) == pytest.approx(1.5)


class TestAggregate:
    def test_means_over_queries(self):
        out = aggregate([
            {"ndcg": 1.0, "mrr": 1.0, "recall": 1.0},
            {"ndcg": 0.0, "mrr": 0.0, "recall": 0.0},
        ])
        assert out["ndcg"] == 0.5
        assert out["mrr"] == 0.5
        assert out["recall"] == 0.5

    def test_empty_returns_empty(self):
        assert aggregate([]) == {}


class TestGate:
    def test_gate_passes_when_baseline_missing(self, tmp_path):
        sys.path.insert(0, str(REPO_ROOT / "benchmarks" / "replay"))
        from run_replay import _check_gate

        result = {"overall": {"ndcg": 1.0, "mrr": 1.0, "recall": 1.0}}
        passed, reasons = _check_gate(result, tmp_path / "missing.json", 0.02)
        assert passed is True
        assert any("not found" in r for r in reasons)

    def test_gate_fails_on_regression(self, tmp_path):
        sys.path.insert(0, str(REPO_ROOT / "benchmarks" / "replay"))
        from run_replay import _check_gate

        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps({
            "overall": {"ndcg": 1.0, "mrr": 1.0, "recall": 1.0}
        }))
        result = {"overall": {"ndcg": 0.95, "mrr": 1.0, "recall": 1.0}}  # 5% drop
        passed, reasons = _check_gate(result, baseline_path, 0.02)
        assert passed is False
        assert any("ndcg" in r for r in reasons)

    def test_gate_passes_within_threshold(self, tmp_path):
        sys.path.insert(0, str(REPO_ROOT / "benchmarks" / "replay"))
        from run_replay import _check_gate

        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps({
            "overall": {"ndcg": 1.0, "mrr": 1.0, "recall": 1.0}
        }))
        result = {"overall": {"ndcg": 0.99, "mrr": 1.0, "recall": 1.0}}  # 1% drop
        passed, reasons = _check_gate(result, baseline_path, 0.02)
        assert passed is True


class TestSelfFixtureBaseline:
    """The shipped self_v1_75_0 fixture is the contract that future
    releases must meet. If the structure changes, this test catches it."""

    def test_fixture_loads_and_has_queries(self):
        fixture_path = (
            REPO_ROOT / "benchmarks" / "replay" / "fixtures" / "self_v1_75_0.json"
        )
        assert fixture_path.exists()
        data = json.loads(fixture_path.read_text())
        assert "queries" in data
        assert len(data["queries"]) >= 5
        for q in data["queries"]:
            assert "query" in q
            assert "expected_top_k" in q
            assert isinstance(q["expected_top_k"], list)

    def test_baseline_result_locked_at_v1_75_0(self):
        result_path = (
            REPO_ROOT / "benchmarks" / "replay" / "results" / "self_v1_75_0-v1.75.0.json"
        )
        assert result_path.exists()
        data = json.loads(result_path.read_text())
        # v1.75.0 was the first locked release — all metrics should be 1.0.
        assert data["overall"]["ndcg"] == 1.0
        assert data["overall"]["mrr"] == 1.0
        assert data["overall"]["recall"] == 1.0
