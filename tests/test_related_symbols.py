"""Tests for get_related_symbols tool (T13)."""

import pytest
from jcodemunch_mcp.tools.get_related_symbols import get_related_symbols, _tokenize_name
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools._utils import resolve_repo


# ---------------------------------------------------------------------------
# Unit tests for _tokenize_name
# ---------------------------------------------------------------------------

class TestTokenizeName:

    def test_snake_case(self):
        assert _tokenize_name("get_user") == {"get", "user"}

    def test_camel_case(self):
        assert _tokenize_name("getUserById") == {"get", "user", "by", "id"}

    def test_single_word(self):
        assert _tokenize_name("process") == {"process"}

    def test_filters_short_tokens(self):
        # tokens shorter than 2 chars are filtered out
        result = _tokenize_name("a_b_c")
        assert "a" not in result
        assert "b" not in result

    def test_returns_lowercase(self):
        tokens = _tokenize_name("ParseFile")
        assert all(t == t.lower() for t in tokens)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

def _get_symbol_id_by_name(repo, store, name):
    """Helper: look up a symbol's full ID by name from the index."""
    owner, repo_name = resolve_repo(repo, store)
    index = IndexStore(base_path=store).load_index(owner, repo_name)
    matches = [s for s in index.symbols if s["name"] == name]
    assert matches, f"Symbol '{name}' not found in index"
    return matches[0]["id"]


class TestGetRelatedSymbolsErrors:

    def test_repo_not_indexed_returns_error(self, tmp_path):
        r = get_related_symbols(
            repo="no_such_repo",
            symbol_id="utils.py::add#function",
            storage_path=str(tmp_path),
        )
        assert "error" in r

    def test_symbol_not_found_returns_error(self, small_index):
        r = get_related_symbols(
            repo=small_index["repo"],
            symbol_id="nonexistent.py::ghost#function",
            storage_path=small_index["store"],
        )
        assert "error" in r
        assert "not found" in r["error"].lower()


class TestGetRelatedSymbolsSameFile:

    def test_same_file_symbols_are_related(self, small_index):
        """Symbols co-located in the same file score highest."""
        sid = _get_symbol_id_by_name(small_index["repo"], small_index["store"], "add")
        r = get_related_symbols(
            repo=small_index["repo"],
            symbol_id=sid,
            storage_path=small_index["store"],
        )
        assert "error" not in r
        assert r["related_count"] > 0
        # All results from a single-file repo should be from the same file
        result_names = [s["name"] for s in r["related"]]
        assert "subtract" in result_names

    def test_same_file_score_positive(self, small_index):
        sid = _get_symbol_id_by_name(small_index["repo"], small_index["store"], "add")
        r = get_related_symbols(
            repo=small_index["repo"],
            symbol_id=sid,
            storage_path=small_index["store"],
        )
        assert "error" not in r
        for entry in r["related"]:
            assert entry["relatedness_score"] > 0


class TestGetRelatedSymbolsNameTokens:

    def test_name_token_overlap_scores(self, tmp_path):
        """Symbols sharing name tokens get relatedness_score > 0."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()
        (src / "users.py").write_text(
            "def get_user(uid):\n    return uid\n\n"
            "def get_user_profile(uid):\n    return {}\n\n"
            "def delete_order(oid):\n    return True\n"
        )
        r_idx = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert r_idx["success"] is True

        repo = r_idx["repo"]
        sid = _get_symbol_id_by_name(repo, str(store), "get_user")
        r = get_related_symbols(repo=repo, symbol_id=sid, storage_path=str(store))
        assert "error" not in r

        # get_user_profile shares tokens "get" and "user" with get_user
        related_names = [s["name"] for s in r["related"]]
        assert "get_user_profile" in related_names

    def test_max_results_respected(self, medium_index):
        """max_results caps the number of returned symbols."""
        owner, repo_name = resolve_repo(medium_index["repo"], medium_index["store"])
        index = IndexStore(base_path=medium_index["store"]).load_index(owner, repo_name)
        any_sym = index.symbols[0]
        r = get_related_symbols(
            repo=medium_index["repo"],
            symbol_id=any_sym["id"],
            max_results=2,
            storage_path=medium_index["store"],
        )
        assert "error" not in r
        assert len(r["related"]) <= 2


class TestGetRelatedSymbolsMeta:

    def test_response_has_timing_meta(self, small_index):
        sid = _get_symbol_id_by_name(small_index["repo"], small_index["store"], "add")
        r = get_related_symbols(
            repo=small_index["repo"],
            symbol_id=sid,
            storage_path=small_index["store"],
        )
        assert "_meta" in r
        assert "timing_ms" in r["_meta"]

    def test_response_includes_target_symbol(self, small_index):
        sid = _get_symbol_id_by_name(small_index["repo"], small_index["store"], "add")
        r = get_related_symbols(
            repo=small_index["repo"],
            symbol_id=sid,
            storage_path=small_index["store"],
        )
        assert "error" not in r
        assert r["symbol"]["name"] == "add"
        assert r["symbol"]["id"] == sid

    def test_related_entries_have_required_fields(self, small_index):
        sid = _get_symbol_id_by_name(small_index["repo"], small_index["store"], "add")
        r = get_related_symbols(
            repo=small_index["repo"],
            symbol_id=sid,
            storage_path=small_index["store"],
        )
        assert "error" not in r
        for entry in r["related"]:
            assert "id" in entry
            assert "name" in entry
            assert "kind" in entry
            assert "file" in entry
            assert "relatedness_score" in entry
