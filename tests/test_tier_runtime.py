"""Runtime tier state + tools/list_changed emission tests."""

import asyncio
import pytest

from jcodemunch_mcp import server as server_mod


class TestSessionTierState:
    def setup_method(self):
        server_mod._session_tier_override = None

    def test_default_is_none(self):
        assert server_mod._session_tier_override is None

    def test_set_get_tier(self):
        server_mod._set_session_tier("core")
        assert server_mod._session_tier_override == "core"

    def test_effective_profile_prefers_session(self, monkeypatch):
        """When session tier is set, it overrides config tool_profile."""
        from jcodemunch_mcp import config as config_mod
        monkeypatch.setattr(config_mod, "get", lambda k, *a, **kw: "full" if k == "tool_profile" else {})
        server_mod._set_session_tier("core")
        assert server_mod._effective_profile() == "core"

    def test_effective_profile_falls_back_to_config(self, monkeypatch):
        from jcodemunch_mcp import config as config_mod
        monkeypatch.setattr(
            config_mod, "get",
            lambda k, *a, **kw: "standard" if k == "tool_profile" else {}
        )
        server_mod._session_tier_override = None
        assert server_mod._effective_profile() == "standard"


class TestEmitToolsListChanged:
    @pytest.mark.asyncio
    async def test_emit_does_not_raise(self):
        """Helper must be a no-op when MCP session isn't available."""
        await server_mod._emit_tools_list_changed()  # must not raise


def test_startup_logs_bundle_disabled_overlap(caplog, monkeypatch):
    from jcodemunch_mcp import server as server_mod
    from jcodemunch_mcp import config as config_mod

    monkeypatch.setattr(
        config_mod, "get",
        lambda k, *a, **kw: {
            "tool_tier_bundles": {"core": ["search_symbols"]},
            "disabled_tools": ["search_symbols"],
        }.get(k, (a[0] if a else None)),
    )
    caplog.set_level("WARNING")
    server_mod._log_startup_validation_warnings()
    msgs = [r.message for r in caplog.records]
    assert any("search_symbols" in m and "disabled_tools" in m for m in msgs)
