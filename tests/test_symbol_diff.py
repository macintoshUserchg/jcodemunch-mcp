"""Tests for get_symbol_diff tool (T13)."""

import pytest
from jcodemunch_mcp.tools.get_symbol_diff import get_symbol_diff
from jcodemunch_mcp.tools.index_folder import index_folder


# ---------------------------------------------------------------------------
# Fixture builder: two separate repos (before/after) in the same store
# ---------------------------------------------------------------------------

def _build_dual_repo(tmp_path):
    """Index two repos into the same store for diff testing.

    repo_v1: foo (function), bar (function)
    repo_v2: bar (function, unchanged), baz (function, new), foo removed
             and biz (function, same name as hypothetical changed)

    Returns (repo_a, repo_b, store_path)
    """
    store = tmp_path / "store"
    store.mkdir()

    # Repo A: foo + bar
    src_a = tmp_path / "repo_v1"
    src_a.mkdir()
    (src_a / "main.py").write_text(
        "def foo(x):\n    return x * 2\n\n"
        "def bar(y):\n    return y + 1\n"
    )
    r_a = index_folder(str(src_a), use_ai_summaries=False, storage_path=str(store))
    assert r_a["success"] is True

    # Repo B: bar (same sig), baz (new), foo removed
    src_b = tmp_path / "repo_v2"
    src_b.mkdir()
    (src_b / "main.py").write_text(
        "def bar(y):\n    return y + 1\n\n"
        "def baz(z):\n    return z ** 2\n"
    )
    r_b = index_folder(str(src_b), use_ai_summaries=False, storage_path=str(store))
    assert r_b["success"] is True

    return r_a["repo"], r_b["repo"], str(store)


def _build_signature_change_repo(tmp_path):
    """Two repos with same symbol name but different signature."""
    store = tmp_path / "store"
    store.mkdir()

    src_a = tmp_path / "before"
    src_a.mkdir()
    (src_a / "lib.py").write_text("def compute(x):\n    return x\n")
    r_a = index_folder(str(src_a), use_ai_summaries=False, storage_path=str(store))
    assert r_a["success"] is True

    src_b = tmp_path / "after"
    src_b.mkdir()
    (src_b / "lib.py").write_text("def compute(x, y):\n    return x + y\n")
    r_b = index_folder(str(src_b), use_ai_summaries=False, storage_path=str(store))
    assert r_b["success"] is True

    return r_a["repo"], r_b["repo"], str(store)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestGetSymbolDiffErrors:

    def test_repo_a_not_indexed(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()
        (src / "a.py").write_text("def real():\n    pass\n")
        r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert r["success"] is True

        result = get_symbol_diff(
            repo_a="no_such_repo_xyz",
            repo_b=r["repo"],
            storage_path=str(store),
        )
        assert "error" in result

    def test_repo_b_not_indexed(self, tmp_path):
        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()
        (src / "a.py").write_text("def real():\n    pass\n")
        r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert r["success"] is True

        result = get_symbol_diff(
            repo_a=r["repo"],
            repo_b="no_such_repo_xyz",
            storage_path=str(store),
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# Correctness: added / removed / unchanged
# ---------------------------------------------------------------------------

class TestGetSymbolDiffAdded:

    def test_added_symbols_detected(self, tmp_path):
        repo_a, repo_b, store = _build_dual_repo(tmp_path)
        r = get_symbol_diff(repo_a=repo_a, repo_b=repo_b, storage_path=store)
        assert "error" not in r
        added_names = [s["name"] for s in r["added"]]
        assert "baz" in added_names

    def test_added_count_matches_list_length(self, tmp_path):
        repo_a, repo_b, store = _build_dual_repo(tmp_path)
        r = get_symbol_diff(repo_a=repo_a, repo_b=repo_b, storage_path=store)
        assert r["added_count"] == len(r["added"])


class TestGetSymbolDiffRemoved:

    def test_removed_symbols_detected(self, tmp_path):
        repo_a, repo_b, store = _build_dual_repo(tmp_path)
        r = get_symbol_diff(repo_a=repo_a, repo_b=repo_b, storage_path=store)
        assert "error" not in r
        removed_names = [s["name"] for s in r["removed"]]
        assert "foo" in removed_names

    def test_removed_count_matches_list_length(self, tmp_path):
        repo_a, repo_b, store = _build_dual_repo(tmp_path)
        r = get_symbol_diff(repo_a=repo_a, repo_b=repo_b, storage_path=store)
        assert r["removed_count"] == len(r["removed"])


class TestGetSymbolDiffUnchanged:

    def test_unchanged_symbol_not_in_added_or_removed(self, tmp_path):
        repo_a, repo_b, store = _build_dual_repo(tmp_path)
        r = get_symbol_diff(repo_a=repo_a, repo_b=repo_b, storage_path=store)
        assert "error" not in r
        added_names = {s["name"] for s in r["added"]}
        removed_names = {s["name"] for s in r["removed"]}
        assert "bar" not in added_names
        assert "bar" not in removed_names

    def test_identical_repos_all_unchanged(self, tmp_path):
        """Diffing a repo against itself → zero added, zero removed."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()
        (src / "a.py").write_text("def func():\n    pass\n")
        r_idx = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert r_idx["success"] is True
        repo = r_idx["repo"]

        r = get_symbol_diff(repo_a=repo, repo_b=repo, storage_path=str(store))
        assert "error" not in r
        assert r["added_count"] == 0
        assert r["removed_count"] == 0
        assert r["changed_count"] == 0


class TestGetSymbolDiffChanged:

    def test_signature_change_detected(self, tmp_path):
        repo_a, repo_b, store = _build_signature_change_repo(tmp_path)
        r = get_symbol_diff(repo_a=repo_a, repo_b=repo_b, storage_path=store)
        assert "error" not in r
        changed_names = [s["name"] for s in r["changed"]]
        assert "compute" in changed_names

    def test_changed_entry_has_both_signatures(self, tmp_path):
        repo_a, repo_b, store = _build_signature_change_repo(tmp_path)
        r = get_symbol_diff(repo_a=repo_a, repo_b=repo_b, storage_path=store)
        assert "error" not in r
        compute_changes = [s for s in r["changed"] if s["name"] == "compute"]
        assert compute_changes
        entry = compute_changes[0]
        assert "signature_a" in entry
        assert "signature_b" in entry
        assert entry["signature_a"] != entry["signature_b"]


class TestGetSymbolDiffMeta:

    def test_meta_has_timing(self, tmp_path):
        repo_a, repo_b, store = _build_dual_repo(tmp_path)
        r = get_symbol_diff(repo_a=repo_a, repo_b=repo_b, storage_path=store)
        assert "_meta" in r
        assert "timing_ms" in r["_meta"]

    def test_meta_has_symbol_counts(self, tmp_path):
        repo_a, repo_b, store = _build_dual_repo(tmp_path)
        r = get_symbol_diff(repo_a=repo_a, repo_b=repo_b, storage_path=store)
        assert "symbols_a" in r["_meta"]
        assert "symbols_b" in r["_meta"]
        assert r["_meta"]["symbols_a"] > 0
        assert r["_meta"]["symbols_b"] > 0

    def test_response_has_repo_identifiers(self, tmp_path):
        repo_a, repo_b, store = _build_dual_repo(tmp_path)
        r = get_symbol_diff(repo_a=repo_a, repo_b=repo_b, storage_path=store)
        assert "repo_a" in r
        assert "repo_b" in r
