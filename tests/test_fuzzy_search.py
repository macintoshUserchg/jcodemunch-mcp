"""Tests for fuzzy symbol search (Feature 6)."""

import pytest

from jcodemunch_mcp.tools.search_symbols import (
    _trigrams,
    _edit_distance,
    search_symbols,
)
from jcodemunch_mcp.tools.index_folder import index_folder


# ---------------------------------------------------------------------------
# Unit tests: _trigrams
# ---------------------------------------------------------------------------

class TestTrigrams:

    def test_normal_string(self):
        t = _trigrams("hello")
        assert "hel" in t
        assert "ell" in t
        assert "llo" in t
        assert len(t) == 3

    def test_short_string_no_trigrams(self):
        # Strings shorter than 3 chars return the string itself as a set
        t = _trigrams("ab")
        assert "ab" in t

    def test_empty_string(self):
        t = _trigrams("")
        assert len(t) == 0

    def test_lowercase_normalisation(self):
        assert _trigrams("FOO") == _trigrams("foo")

    def test_returns_frozenset(self):
        assert isinstance(_trigrams("test"), frozenset)


# ---------------------------------------------------------------------------
# Unit tests: _edit_distance
# ---------------------------------------------------------------------------

class TestEditDistance:

    def test_identical_strings(self):
        assert _edit_distance("hello", "hello") == 0

    def test_single_insertion(self):
        assert _edit_distance("helo", "hello") == 1

    def test_single_deletion(self):
        assert _edit_distance("hello", "helo") == 1

    def test_single_substitution(self):
        assert _edit_distance("hello", "hXllo") == 1

    def test_empty_vs_string(self):
        assert _edit_distance("", "abc") == 3

    def test_symmetric(self):
        assert _edit_distance("abc", "xyz") == _edit_distance("xyz", "abc")

    def test_connection_pool_vs_conn_pool(self):
        # Verifies the abbreviation case has a measurable distance
        ed = _edit_distance("conn_pool", "connection_pool")
        assert ed > 2  # definitely not a typo — it's an abbreviation

    def test_typo_correction(self):
        # One transposition / substitution
        assert _edit_distance("authenticate", "autenticate") <= 2


# ---------------------------------------------------------------------------
# Integration tests: search_symbols with fuzzy=True/False
# ---------------------------------------------------------------------------

class TestFuzzySearch:
    """Integration tests using a synthetic indexed repo."""

    def _build_repo(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()
        (src / "db.py").write_text(
            "def connection_pool():\n    pass\n\n"
            "def get_connection():\n    pass\n"
        )
        (src / "auth.py").write_text(
            "def authenticate(user, password):\n    pass\n\n"
            "def authorize(user, resource):\n    pass\n"
        )
        # Symbol whose BM25 tokens ('fetcher', 'worker') share nothing with
        # the abbreviation query 'fetchr_workr' ('fetchr', 'workr') —
        # forces fuzzy-only discovery.
        (src / "worker.py").write_text(
            "def fetcher_worker():\n    pass\n"
        )
        result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert result["success"] is True
        return result["repo"], str(store)

    def test_fuzzy_finds_abbreviation(self, tmp_path):
        """conn_pool with fuzzy=True should return connection_pool."""
        repo, store = self._build_repo(tmp_path)
        result = search_symbols(
            repo=repo,
            query="conn_pool",
            fuzzy=True,
            fuzzy_threshold=0.3,
            storage_path=store,
        )
        names = [r["name"] for r in result["results"]]
        assert "connection_pool" in names

    def test_fuzzy_result_has_match_type_fuzzy(self, tmp_path):
        """Fuzzy-matched results carry match_type='fuzzy', fuzzy_similarity, edit_distance.

        Uses 'fetchr_workr' → 'fetcher_worker': no shared BM25 tokens
        ('fetchr'/'workr' ≠ 'fetcher'/'worker'), forcing the fuzzy path.
        """
        repo, store = self._build_repo(tmp_path)
        result = search_symbols(
            repo=repo,
            query="fetchr_workr",
            fuzzy=True,
            fuzzy_threshold=0.3,
            storage_path=store,
        )
        fuzzy_results = [r for r in result["results"] if r.get("match_type") == "fuzzy"]
        assert len(fuzzy_results) > 0, (
            f"Expected fuzzy hits, got: {[r['name'] for r in result['results']]}"
        )
        hit = next(r for r in fuzzy_results if r["name"] == "fetcher_worker")
        assert "fuzzy_similarity" in hit
        assert "edit_distance" in hit
        assert 0.0 < hit["fuzzy_similarity"] <= 1.0

    def test_bm25_results_tagged_exact_when_fuzzy_active(self, tmp_path):
        """BM25 hits get match_type='exact' when fuzzy pass is active."""
        repo, store = self._build_repo(tmp_path)
        # Use a query that has a direct BM25 hit + explicitly request fuzzy
        result = search_symbols(
            repo=repo,
            query="authenticate",
            fuzzy=True,
            storage_path=store,
        )
        exact_results = [r for r in result["results"] if r.get("match_type") == "exact"]
        assert len(exact_results) > 0
        assert exact_results[0]["name"] == "authenticate"

    def test_no_match_type_when_fuzzy_false_and_bm25_confident(self, tmp_path):
        """When fuzzy=False and BM25 finds a good hit, match_type is absent."""
        repo, store = self._build_repo(tmp_path)
        result = search_symbols(
            repo=repo,
            query="authenticate",
            fuzzy=False,
            storage_path=store,
        )
        assert "error" not in result
        assert result["result_count"] > 0
        # No fuzzy results — match_type should not be present
        for r in result["results"]:
            assert "match_type" not in r

    def test_fuzzy_threshold_1_returns_only_exact_trigram_matches(self, tmp_path):
        """fuzzy_threshold=1.0 means only symbols with identical trigram sets pass."""
        repo, store = self._build_repo(tmp_path)
        result = search_symbols(
            repo=repo,
            query="conn_pool",
            fuzzy=True,
            fuzzy_threshold=1.0,
            max_edit_distance=0,
            storage_path=store,
        )
        fuzzy_results = [r for r in result["results"] if r.get("match_type") == "fuzzy"]
        # conn_pool and connection_pool have very different trigram sets — should not match
        assert all(r["name"] != "connection_pool" for r in fuzzy_results)

    def test_typo_found_via_edit_distance(self, tmp_path):
        """A one-character typo is found even when trigrams diverge."""
        repo, store = self._build_repo(tmp_path)
        # "autenticate" (missing 'h') — edit distance 1 to "authenticate"
        result = search_symbols(
            repo=repo,
            query="autenticate",
            fuzzy=True,
            max_edit_distance=2,
            storage_path=store,
        )
        names = [r["name"] for r in result["results"]]
        assert "authenticate" in names

    def test_fuzzy_false_default_no_behavior_change(self, tmp_path):
        """fuzzy=False (default) produces same results as not passing the param."""
        repo, store = self._build_repo(tmp_path)
        r1 = search_symbols(repo=repo, query="connection_pool", storage_path=store)
        r2 = search_symbols(repo=repo, query="connection_pool", fuzzy=False, storage_path=store)
        assert [e["id"] for e in r1["results"]] == [e["id"] for e in r2["results"]]

    def test_auto_trigger_when_bm25_finds_nothing(self, tmp_path):
        """Fuzzy auto-triggers when BM25 returns zero results (near-miss threshold)."""
        repo, store = self._build_repo(tmp_path)
        # "conn_pool" won't BM25-match "connection_pool" well — auto-trigger should kick in
        result = search_symbols(
            repo=repo,
            query="conn_pool",
            fuzzy=False,       # not explicitly requested
            fuzzy_threshold=0.3,
            storage_path=store,
        )
        # Should still find connection_pool via auto-trigger
        names = [r["name"] for r in result["results"]]
        assert "connection_pool" in names
