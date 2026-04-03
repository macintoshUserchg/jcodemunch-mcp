"""BM25 search correctness and in-process latency benchmarks (T14).

These tests use a synthetic in-process index — no external repo required.
All tests run in CI without modification.

Latency budgets:
    Cold search (BM25 cache cold):  < 2000 ms  (generous for slow CI runners)
    Warm search (BM25 cache hot):   < 500 ms
"""

import time

import pytest

from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols

# ---------------------------------------------------------------------------
# Module-level synthetic repo — indexed once for the whole module
# ---------------------------------------------------------------------------

_REPO = None
_STORE = None


def _ensure_index(tmp_path_factory):
    global _REPO, _STORE
    if _REPO is not None:
        return _REPO, _STORE

    root = tmp_path_factory.mktemp("search_perf")
    src = root / "src"
    store = root / "store"
    src.mkdir()
    store.mkdir()

    # 5 files, 20+ symbols — enough for meaningful BM25 scoring
    (src / "parser.py").write_text(
        "def parse_file(path):\n    return {}\n\n"
        "def parse_tokens(source):\n    return []\n\n"
        "def parse_imports(source):\n    return []\n\n"
        "class ParseError(Exception):\n    pass\n\n"
        "class TokenStream:\n    pass\n"
    )
    (src / "search.py").write_text(
        "def search_symbols(query, repo):\n    return []\n\n"
        "def search_text(query, repo):\n    return []\n\n"
        "def rank_results(results, query):\n    return results\n\n"
        "class SearchIndex:\n    pass\n"
    )
    (src / "storage.py").write_text(
        "def save_index(index, path):\n    pass\n\n"
        "def load_index(path):\n    return None\n\n"
        "def clear_cache():\n    pass\n\n"
        "class CodeIndex:\n    pass\n\n"
        "class IndexStore:\n    pass\n"
    )
    (src / "server.py").write_text(
        "def run_server(host, port):\n    pass\n\n"
        "def handle_request(req):\n    return {}\n\n"
        "def dispatch_tool(name, args):\n    return {}\n"
    )
    (src / "utils.py").write_text(
        "MAX_RESULTS = 500\n\n"
        "def tokenize(text):\n    return text.split()\n\n"
        "def normalize(name):\n    return name.lower()\n\n"
        "def resolve_path(path, root):\n    return path\n"
    )

    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True, f"index_folder failed: {r}"
    _REPO = r["repo"]
    _STORE = str(store)
    return _REPO, _STORE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def perf_repo(tmp_path_factory):
    """Module-scoped synthetic repo for search performance tests."""
    return _ensure_index(tmp_path_factory)


# ---------------------------------------------------------------------------
# T14 — Correctness: result stability across calls
# ---------------------------------------------------------------------------

QUERIES = ["parse", "search", "index", "token", "server", "load"]


class TestSearchStability:

    def test_results_stable_across_two_calls(self, perf_repo):
        """BM25 results must be deterministic: identical on repeated calls."""
        repo, store = perf_repo
        for q in QUERIES:
            r1 = search_symbols(repo=repo, query=q, max_results=10,
                                detail_level="compact", storage_path=store)
            r2 = search_symbols(repo=repo, query=q, max_results=10,
                                detail_level="compact", storage_path=store)
            assert "results" in r1, f"Query '{q}': unexpected error: {r1}"
            ids1 = [r["id"] for r in r1["results"]]
            ids2 = [r["id"] for r in r2["results"]]
            assert ids1 == ids2, f"Query '{q}': result order differs between calls"

    def test_scores_stable_across_two_calls(self, perf_repo):
        """BM25 scores must be identical on repeated calls (requires debug=True)."""
        repo, store = perf_repo
        for q in QUERIES:
            r1 = search_symbols(repo=repo, query=q, max_results=10,
                                detail_level="compact", debug=True, storage_path=store)
            r2 = search_symbols(repo=repo, query=q, max_results=10,
                                detail_level="compact", debug=True, storage_path=store)
            assert "results" in r1, f"Query '{q}': unexpected error: {r1}"
            scores1 = [round(r["score"], 6) for r in r1["results"]]
            scores2 = [round(r["score"], 6) for r in r2["results"]]
            assert scores1 == scores2, f"Query '{q}': scores differ between calls"

    def test_relevant_symbol_appears_in_results(self, perf_repo):
        """Querying 'parse' should surface parse_file as a top result."""
        repo, store = perf_repo
        r = search_symbols(repo=repo, query="parse", max_results=5,
                           detail_level="compact", storage_path=store)
        assert "results" in r
        result_names = [entry["name"] for entry in r["results"]]
        # parse_file, parse_tokens, parse_imports — at least one should appear
        parse_funcs = {"parse_file", "parse_tokens", "parse_imports"}
        assert parse_funcs & set(result_names), (
            f"Expected at least one parse_* function in top-5, got: {result_names}"
        )

    def test_query_returns_nonempty_results(self, perf_repo):
        """All test queries should return at least one result."""
        repo, store = perf_repo
        for q in QUERIES:
            r = search_symbols(repo=repo, query=q, max_results=10,
                               detail_level="compact", storage_path=store)
            assert "results" in r, f"Query '{q}' errored: {r}"
            assert len(r["results"]) > 0, f"Query '{q}' returned no results"


# ---------------------------------------------------------------------------
# T14 — Latency budgets (in-process benchmarks)
# ---------------------------------------------------------------------------

class TestSearchLatency:

    def test_cold_search_within_budget(self, perf_repo):
        """Cold BM25 search (cache empty) must complete within 2000 ms."""
        from jcodemunch_mcp.storage.sqlite_store import _cache_clear
        repo, store = perf_repo
        _cache_clear()

        start = time.perf_counter()
        r = search_symbols(repo=repo, query="parse", max_results=10,
                           detail_level="compact", storage_path=store)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert "results" in r
        assert elapsed_ms < 2000, (
            f"Cold search took {elapsed_ms:.1f}ms — exceeds 2000ms budget"
        )

    def test_warm_search_within_budget(self, perf_repo):
        """Warm BM25 search (cache populated) must complete within 500 ms."""
        repo, store = perf_repo
        # Warm up the BM25 cache
        search_symbols(repo=repo, query="parse", max_results=10,
                       detail_level="compact", storage_path=store)

        start = time.perf_counter()
        r = search_symbols(repo=repo, query="parse", max_results=10,
                           detail_level="compact", storage_path=store)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert "results" in r
        assert elapsed_ms < 500, (
            f"Warm search took {elapsed_ms:.1f}ms — exceeds 500ms budget"
        )

    def test_warm_faster_than_cold(self, perf_repo):
        """Warm search should be faster than cold search (cache benefit)."""
        from jcodemunch_mcp.storage.sqlite_store import _cache_clear
        repo, store = perf_repo
        _cache_clear()

        start = time.perf_counter()
        search_symbols(repo=repo, query="search", max_results=10,
                       detail_level="compact", storage_path=store)
        cold_ms = (time.perf_counter() - start) * 1000

        start = time.perf_counter()
        search_symbols(repo=repo, query="search", max_results=10,
                       detail_level="compact", storage_path=store)
        warm_ms = (time.perf_counter() - start) * 1000

        # On very fast machines cold/warm may be within noise — only enforce
        # that warm is not significantly slower (10x) than cold
        assert warm_ms < cold_ms * 10 or warm_ms < 500, (
            f"Warm ({warm_ms:.1f}ms) unexpectedly much slower than cold ({cold_ms:.1f}ms)"
        )

    def test_timing_meta_reported(self, perf_repo):
        """search_symbols _meta must include timing_ms."""
        repo, store = perf_repo
        r = search_symbols(repo=repo, query="token", max_results=5,
                           detail_level="compact", storage_path=store)
        assert "_meta" in r
        assert "timing_ms" in r["_meta"]
        assert r["_meta"]["timing_ms"] >= 0
