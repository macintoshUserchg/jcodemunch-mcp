# PR Title

feat: tiered tool surface with runtime model-driven switching + MUNCH search_text round-trip fix

# PR Body

## Summary

Narrows the jcodemunch-mcp tool surface per-model at runtime so request-capped plans stretch further on small-model usage, without adding any extra MCP request per task. Ships user-editable tier bundles (always on), an opt-in `adaptive_tiering` flag for runtime switching, a fuzzy model→tier resolver, two new runtime tools (`set_tool_tier`, `announce_model`), and a `plan_turn(model=...)` piggyback that flips tiers as a side effect of the opening-move call.

Bundles three fixes found during the work:

1. **MUNCH `search_text` round-trip** — schema mismatch was dropping match content and context lines; decoder was stringifying all scalars. Rewritten with flatten/regroup, typed scalars, and legacy `st1` decode compatibility.
2. **`tools/list_changed` notification** — `_emit_tools_list_changed` was a `pass` placeholder so the whole runtime-tier feature silently never notified clients. Now does concrete session lookup + emission, with warnings on SDK mismatches.
3. **`render_diagram` integration test portability** — the viewer binary path was hardcoded to the maintainer's local build, so the test skipped silently on every other machine. Now reads `MMD_VIEWER_PATH` env var with the maintainer path as fallback.

## Why

jcodemunch-mcp exposes 60+ tools. Small and mid-size models (Haiku, GPT-4o-mini, local Llamas) systematically favor primitives (`search_symbols`, `get_symbol_source`, `find_references`) over composites (`get_context_bundle`, `get_ranked_context`) when the full surface is visible. The result is many small requests per task — each costing a round trip and eating into request-capped usage plans. On a typical "understand this feature, change these three call sites" task, the primitive-preference bias can double or triple the request count compared to what a composite-first plan would spend.

The existing `tool_profile` setting (`core` / `standard` / `full`) already addressed the narrowing, but (a) tier contents were hardcoded in `server.py`, not editable, and (b) tier was startup-only — runners like OpenCode swap models mid-session across agents, so a static startup tier can't track the active model. The work in this PR makes both dimensions adaptive: editable bundles by default, runtime switching opt-in. Zero extra MCP requests because `plan_turn` (already the opening-move tool per CLAUDE.md) carries the model identifier.

Two secondary issues surfaced during implementation got bundled:

1. **MUNCH `search_text` dropped match content.** The on-wire schema declared flat columns `file|line|line_content`, but the real tool response is `{results:[{file, matches:[{line, text, before?, after?}]}]}`. Two matches in the same file collapsed to one null row; the `text` field was read as `line_content` (wrong column name); `before`/`after` context arrays were lost entirely.
2. **`schema_driven.decode` hardcoded `str` for all scalars** and skipped `_meta` coercion — so `result_count`, `timing_ms`, `truncated` etc. round-tripped as raw strings instead of typed values.

## What

### Tiered tool surface (always-on parts)

- **User-editable tier bundles.** `tool_tier_bundles.core` and `tool_tier_bundles.standard` moved from hardcoded constants in `server.py` into `config.jsonc`. `full` remains unfiltered. `_resolve_tier_bundle` reads from config first, falls back to baked-in defaults when the key is missing/malformed.
- **`model_tier_map`** maps model identifiers to tier names via layered matching: normalize (lowercase, strip provider prefix, strip `[1m]`/`[beta]` bracket suffixes, strip `-YYYYMMDD` date stamps) → exact → glob → substring → `*` wildcard → hardcoded `full` fallback.
- **Overlap validation.** When a tool appears in both a bundle and `disabled_tools`, the server logs a WARNING on startup and `jcodemunch-mcp config --check` prints a WARN line. `disabled_tools` still applies after tier filtering, so the tool stays hidden — the warning just surfaces the silent filtering.
- **`upgrade_config`** migrates the new keys (`tool_tier_bundles`, `model_tier_map`, `adaptive_tiering`) into existing `config.jsonc` files without clobbering user values.

### Runtime switching (opt-in via `adaptive_tiering: true`)

- **`plan_turn(model="<id>")`** accepts a new optional `model` parameter. When adaptive tiering is on, the tier flip happens *after* `plan_turn` returns successfully — a handler failure can no longer leave a half-applied session tier. The plan_turn response gets a `tier_announcement` field carrying `{tier, changed, match_reason, adaptive_tiering}`.
- **`announce_model(model="...")`** fallback tool for non-plan_turn flows. Same fuzzy resolution. Idempotent: a second call with the same model is a no-op with `changed: false`.
- **`set_tool_tier(tier="core"|"standard"|"full")`** explicit override for power-user / escape-hatch use. Not gated by `adaptive_tiering` because it's an explicit user invocation.
- **Force-inclusion.** `set_tool_tier` and `announce_model` are exempt from `disabled_tools` filtering *and* from the call-time `is_tool_disabled` gate — users can never lose their own tier controls via config edits.
- **HTTP footgun warning.** When `adaptive_tiering: true` and the server starts under SSE or streamable-http transport, a WARNING logs at startup noting the process-global state can leak across concurrent clients.

### Notification path hardening (audit remediation)

- **`_get_mcp_session()`** now does concrete session lookup (`srv.request_context.session`) with narrowed exception handling (`LookupError`, `AttributeError`). Accepts the server instance as a parameter so tests can exercise the real lookup chain instead of mocking the helper.
- **`_emit_tools_list_changed()`** actually emits now — previous implementation was a `pass` placeholder that silently did nothing. Warns at WARNING level when the session lacks a `send_tool_list_changed` method so SDK version mismatches are visible.

### MUNCH `search_text` round-trip

- **Flatten/regroup rewrite.** `_flatten` transforms `{results:[{file, matches:[...]}]}` into a flat row list on encode; `_regroup` reassembles it preserving file order on decode. Columns: `file|line|text|before|after`. `before`/`after` context lines ride as JSON strings inside CSV cells — `csv.writer` handles quoting end-to-end, verified by an adversarial round-trip test covering embedded commas, quotes, and newlines.
- **Encoding ID bump** `st1` → `st2`. Legacy `st1` payloads still decode via a new `LEGACY_ENCODING_IDS` discovery hook in the encoder registry.
- **Opt-in typed scalars.** `schema_driven.decode()` gains an optional `scalar_types: Mapping[str, str]` parameter. When supplied, it coerces top-level scalars, nested-dict subkeys, and `_meta.*` fields via prefixed-key lookup. Default `None` preserves prior behavior for every schema that doesn't opt in — confirmed by existing tests still asserting stringly values. `search_text` declares types for its numeric/boolean fields.

### `render_diagram` integration test portability (bundled fix)

- **`MMD_VIEWER_PATH` env var** in `tests/test_render_diagram_integration.py` replaces the hardcoded `D:\1.Development\mmd-viewer\target\debug\mmd-viewer.exe`. Every other dev's machine was silently skipping the test because the path didn't exist locally. Now: reads the env var first, falls back to the maintainer path so automatic runs still work there, and the skip message explicitly tells other devs to set `MMD_VIEWER_PATH`.

### Tooling

- **`.claudeignore`** restricts what Claude Code scans — build artifacts, virtualenvs, test/mypy caches, `.code-index/` storage, local notes.

## What we expect

- Haiku / small-model sessions should land a `plan_turn(model="claude-haiku-4-5")` opener, see the tool list narrow to ~16 tools, and converge on composites (`get_context_bundle`, `get_ranked_context`) instead of primitive chains. Target: ≥30% fewer requests per task vs. `tool_profile=full` throughout.
- Existing installs see **no behavior change** unless they explicitly set `adaptive_tiering: true` in their config. Defaults ship false.
- `search_text` results now include the actual matched line content and per-match `before`/`after` context when requested — the empty-content regression from the previous MUNCH encoder is closed.
- `tools/list_changed` notifications reach clients that support them, so narrowed tier lists are reflected without reconnecting.
- HTTP operators running the adaptive path see a clear startup warning about the process-global tier state.

## Before / After

### Tool surface and runtime control

| Dimension | Before | After |
|-----------|--------|-------|
| Tier bundle contents | Hardcoded in `server.py:84-119` | User-editable in `config.jsonc` with in-code fallback |
| Runtime tier switching | Startup-only via `tool_profile` | Opt-in per-turn via `plan_turn(model=...)` or `announce_model` |
| Extra MCP requests per task to switch tier | 1 (a separate `announce_model` call) | 0 (piggybacks on existing `plan_turn`) |
| `plan_turn` tier switch atomicity | Applied before handler; handler failure left half-applied tier | Applied after handler returns successfully |
| `set_tool_tier` / `announce_model` availability | Could be silently hidden by `disabled_tools` in config | Force-included at list-time AND call-time gate |
| Bundle ∩ `disabled_tools` conflicts | Silent filtering | WARNING at startup + `config --check` diagnostic |
| Model id matching | N/A | Fuzzy: normalize → exact → glob → substring → `*` → `full` fallback |
| `adaptive_tiering` + HTTP transport | No signal | Startup WARNING about cross-client tier leak |

### `tools/list_changed` notification path

| Aspect | Before | After |
|--------|--------|-------|
| `_emit_tools_list_changed` body | `pass` placeholder (feature silently no-op) | Concrete session lookup + emission with warnings on failure |
| `_get_mcp_session` | Did not exist | Concrete helper, narrowed exception handling, parameterized for tests |
| Integration test coverage | Mocked helper only | Real `FakeServer.request_context.session` path exercised |

### MUNCH `search_text` encoder

| Behavior | Before (`st1`) | After (`st2`) |
|----------|----------------|---------------|
| Schema shape | Flat `file\|line\|line_content` | Flatten/regroup around real nested response |
| Two matches in same file | Collapsed to one null row | Both preserved, regrouped by file in original order |
| Matched line content | Lost (wrong column name) | Round-trips correctly in `text` field |
| `before` / `after` context lines | Lost entirely | Preserved via JSON-in-CSV cells (adversarial-tested) |
| `result_count`, `timing_ms`, `truncated` etc. | All strings on decode | Typed (int / float / bool) via opt-in `scalar_types` |
| Legacy `st1` payloads | N/A (only shape) | Decode via `LEGACY_ENCODING_IDS` registry hook |

### Test surface

| Area | Before | After |
|------|--------|-------|
| Total tests | 3256 passed / 9 skipped | 3471 passed / 9 skipped |
| Tier resolver coverage | 0 tests | 25 tests (normalize, resolve, overlap validation) |
| Tier runtime coverage | 0 tests | Session state, emit helper (integration), force-inclusion, plan_turn piggyback, adaptive_tiering gate |
| MUNCH `search_text` coverage | 1 test asserting wrong shape | Nested shape, context lines, multi-file ordering, adversarial CSV/JSON cells, `st1` legacy decode |
| `render_diagram` integration test | Skipped on every machine except maintainer's | Runs anywhere `MMD_VIEWER_PATH` points (maintainer default kept as fallback) |

## Out of scope

- HTTP multi-client session-keyed tier state (stdio-only for v1; documented and start-time warning added).
- Auto-detecting the active model server-side without an explicit agent-passed identifier (MCP has no such channel).
- Response-embedded `_meta.suggested_next` hints on primitive tools.

## Co-authors

- GPT 5.3 XHIGH
- Claude Opus 4.7
