"""Tests for get_class_hierarchy tool (T13)."""

import pytest
from jcodemunch_mcp.tools.get_class_hierarchy import get_class_hierarchy, _parse_bases
from jcodemunch_mcp.tools.index_folder import index_folder


# ---------------------------------------------------------------------------
# Unit tests for _parse_bases
# ---------------------------------------------------------------------------

class TestParseBases:

    def test_python_single_base(self):
        assert _parse_bases("class Dog(Animal)") == ["Animal"]

    def test_python_multiple_bases(self):
        bases = _parse_bases("class Mixin(Base1, Base2)")
        assert "Base1" in bases
        assert "Base2" in bases

    def test_python_no_base_no_parens(self):
        assert _parse_bases("class Standalone") == []

    def test_python_no_base_empty_parens(self):
        # empty parens — no uppercase candidates
        result = _parse_bases("class Root()")
        assert result == []

    def test_java_extends(self):
        bases = _parse_bases("class Dog extends Animal")
        assert "Animal" in bases

    def test_java_implements(self):
        bases = _parse_bases("class Dog implements IAnimal, IMovable")
        assert "IAnimal" in bases
        assert "IMovable" in bases

    def test_java_extends_and_implements(self):
        bases = _parse_bases("class Dog extends Animal implements IMovable")
        assert "Animal" in bases
        assert "IMovable" in bases

    def test_filters_lowercase_bases(self):
        # Filter requires names to start with uppercase
        result = _parse_bases("class Foo(lowercase, Bar)")
        assert "lowercase" not in result
        assert "Bar" in result

    def test_empty_string(self):
        assert _parse_bases("") == []


# ---------------------------------------------------------------------------
# Integration tests against hierarchy_index fixture (from conftest.py)
# ---------------------------------------------------------------------------

class TestGetClassHierarchyErrors:

    def test_repo_not_indexed_returns_error(self, tmp_path):
        r = get_class_hierarchy(
            repo="no_such_repo", class_name="Foo",
            storage_path=str(tmp_path),
        )
        assert "error" in r

    def test_class_not_found_returns_error(self, hierarchy_index):
        r = get_class_hierarchy(
            repo=hierarchy_index["repo"],
            class_name="NonExistentClass",
            storage_path=hierarchy_index["store"],
        )
        assert "error" in r
        assert "NonExistentClass" in r["error"]


class TestGetClassHierarchyAncestors:

    def test_base_class_has_no_ancestors(self, hierarchy_index):
        r = get_class_hierarchy(
            repo=hierarchy_index["repo"],
            class_name="Animal",
            storage_path=hierarchy_index["store"],
        )
        assert "error" not in r
        assert r["ancestor_count"] == 0
        assert r["ancestors"] == []

    def test_direct_subclass_has_one_ancestor(self, hierarchy_index):
        r = get_class_hierarchy(
            repo=hierarchy_index["repo"],
            class_name="Mammal",
            storage_path=hierarchy_index["store"],
        )
        assert "error" not in r
        ancestor_names = [a["name"] for a in r["ancestors"]]
        assert "Animal" in ancestor_names

    def test_deep_subclass_has_all_ancestors(self, hierarchy_index):
        r = get_class_hierarchy(
            repo=hierarchy_index["repo"],
            class_name="Dog",
            storage_path=hierarchy_index["store"],
        )
        assert "error" not in r
        ancestor_names = [a["name"] for a in r["ancestors"]]
        assert "Mammal" in ancestor_names
        assert "Animal" in ancestor_names

    def test_ancestor_order_nearest_first(self, hierarchy_index):
        r = get_class_hierarchy(
            repo=hierarchy_index["repo"],
            class_name="Dog",
            storage_path=hierarchy_index["store"],
        )
        assert "error" not in r
        names = [a["name"] for a in r["ancestors"]]
        # BFS: Mammal comes before Animal (nearest first)
        assert names.index("Mammal") < names.index("Animal")


class TestGetClassHierarchyDescendants:

    def test_base_class_has_all_descendants(self, hierarchy_index):
        r = get_class_hierarchy(
            repo=hierarchy_index["repo"],
            class_name="Animal",
            storage_path=hierarchy_index["store"],
        )
        assert "error" not in r
        desc_names = [d["name"] for d in r["descendants"]]
        assert "Mammal" in desc_names
        assert "Dog" in desc_names
        assert "Cat" in desc_names

    def test_mid_class_has_direct_descendants(self, hierarchy_index):
        r = get_class_hierarchy(
            repo=hierarchy_index["repo"],
            class_name="Mammal",
            storage_path=hierarchy_index["store"],
        )
        assert "error" not in r
        desc_names = [d["name"] for d in r["descendants"]]
        assert "Dog" in desc_names
        assert "Cat" in desc_names

    def test_leaf_class_has_no_descendants(self, hierarchy_index):
        r = get_class_hierarchy(
            repo=hierarchy_index["repo"],
            class_name="Dog",
            storage_path=hierarchy_index["store"],
        )
        assert "error" not in r
        assert r["descendant_count"] == 0
        assert r["descendants"] == []


class TestGetClassHierarchyMeta:

    def test_case_insensitive_lookup(self, hierarchy_index):
        r = get_class_hierarchy(
            repo=hierarchy_index["repo"],
            class_name="animal",
            storage_path=hierarchy_index["store"],
        )
        assert "error" not in r
        assert r["class"]["name"] == "Animal"

    def test_response_has_timing_meta(self, hierarchy_index):
        r = get_class_hierarchy(
            repo=hierarchy_index["repo"],
            class_name="Animal",
            storage_path=hierarchy_index["store"],
        )
        assert "_meta" in r
        assert "timing_ms" in r["_meta"]
        assert isinstance(r["_meta"]["timing_ms"], (int, float))

    def test_response_has_class_info(self, hierarchy_index):
        r = get_class_hierarchy(
            repo=hierarchy_index["repo"],
            class_name="Animal",
            storage_path=hierarchy_index["store"],
        )
        assert "error" not in r
        assert r["class"]["name"] == "Animal"
        assert "file" in r["class"]
        assert "line" in r["class"]

    def test_external_base_recorded_as_external(self, tmp_path):
        """Class with a base not in the index appears with file='(external)'."""
        src = tmp_path / "src"
        store = tmp_path / "store"
        src.mkdir()
        store.mkdir()
        (src / "derived.py").write_text(
            "class MyWidget(ExternalBase):\n"
            "    pass\n"
        )
        r_idx = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
        assert r_idx["success"] is True

        r = get_class_hierarchy(
            repo=r_idx["repo"],
            class_name="MyWidget",
            storage_path=str(store),
        )
        assert "error" not in r
        # ExternalBase is not in the index — recorded with file=(external)
        ext = [a for a in r["ancestors"] if a["name"] == "ExternalBase"]
        assert ext, "ExternalBase should appear in ancestors"
        assert ext[0]["file"] == "(external)"
