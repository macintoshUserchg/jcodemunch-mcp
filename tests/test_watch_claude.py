"""Tests for watch-claude v2: hook-event, manifest, git worktree parsing, integration."""

import asyncio
import hashlib
import json
import subprocess
import textwrap
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from jcodemunch_mcp.hook_event import handle_hook_event, read_manifest, default_manifest_path
from jcodemunch_mcp.server import main
from jcodemunch_mcp.watcher import (
    _local_repo_id,
    parse_git_worktrees,
    watch_claude_worktrees,
)


# ---------------------------------------------------------------------------
# hook-event tests
# ---------------------------------------------------------------------------


def _mock_git_success():
    """Return a patch that makes git worktree add/remove succeed."""
    result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    return patch("jcodemunch_mcp.hook_event.subprocess.run", return_value=result)


class TestHookEvent:
    def test_create_runs_git_worktree_add(self, tmp_path):
        """hook-event create should invoke git worktree add."""
        manifest = tmp_path / "manifest.jsonl"
        payload = json.dumps({
            "cwd": str(tmp_path),
            "name": "test-wt",
            "hook_event_name": "WorktreeCreate",
        })
        mock_run = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        ))
        with (
            patch("sys.stdin", StringIO(payload)),
            patch("jcodemunch_mcp.hook_event.subprocess.run", mock_run),
            patch("jcodemunch_mcp.hook_event._get_worktree_base", return_value=""),
        ):
            handle_hook_event("create", manifest_path=manifest)

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "git"
        assert "worktree" in args
        assert "add" in args
        assert "-b" in args
        # Branch name should include the worktree name
        branch_idx = args.index("-b") + 1
        assert "test-wt" in args[branch_idx]

    def test_create_appends_to_manifest(self, tmp_path):
        """hook-event create should record the event to the manifest."""
        manifest = tmp_path / "manifest.jsonl"
        payload = json.dumps({
            "cwd": str(tmp_path),
            "name": "test-wt",
            "hook_event_name": "WorktreeCreate",
        })
        with (
            patch("sys.stdin", StringIO(payload)),
            _mock_git_success(),
            patch("jcodemunch_mcp.hook_event._get_worktree_base", return_value=""),
        ):
            handle_hook_event("create", manifest_path=manifest)

        lines = manifest.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "create"
        assert "test-wt" in entry["path"]
        assert "ts" in entry

    def test_create_prints_path_to_stdout(self, tmp_path, capsys):
        """hook-event create must print the resolved path to stdout for Claude Code."""
        manifest = tmp_path / "manifest.jsonl"
        payload = json.dumps({
            "cwd": str(tmp_path),
            "name": "my-wt",
            "hook_event_name": "WorktreeCreate",
        })
        with (
            patch("sys.stdin", StringIO(payload)),
            _mock_git_success(),
            patch("jcodemunch_mcp.hook_event._get_worktree_base", return_value=""),
        ):
            handle_hook_event("create", manifest_path=manifest)

        captured = capsys.readouterr()
        assert "my-wt" in captured.out.strip()

    def test_create_uses_config_base_path(self, tmp_path):
        """worktree_base_path config overrides the default location."""
        manifest = tmp_path / "manifest.jsonl"
        custom_base = str(tmp_path / "custom-worktrees")
        payload = json.dumps({
            "cwd": str(tmp_path),
            "name": "custom-wt",
            "hook_event_name": "WorktreeCreate",
        })
        with (
            patch("sys.stdin", StringIO(payload)),
            _mock_git_success(),
            patch("jcodemunch_mcp.hook_event._get_worktree_base", return_value=custom_base),
        ):
            handle_hook_event("create", manifest_path=manifest)

        entry = json.loads(manifest.read_text().strip())
        assert custom_base in entry["path"]
        assert "custom-wt" in entry["path"]

    def test_create_default_path_is_claude_convention(self, tmp_path):
        """Without config, worktree path follows {cwd}/.claude/worktrees/{name}."""
        manifest = tmp_path / "manifest.jsonl"
        payload = json.dumps({
            "cwd": str(tmp_path),
            "name": "default-wt",
            "hook_event_name": "WorktreeCreate",
        })
        with (
            patch("sys.stdin", StringIO(payload)),
            _mock_git_success(),
            patch("jcodemunch_mcp.hook_event._get_worktree_base", return_value=""),
        ):
            handle_hook_event("create", manifest_path=manifest)

        entry = json.loads(manifest.read_text().strip())
        expected = str(Path(tmp_path / ".claude" / "worktrees" / "default-wt").resolve())
        assert entry["path"] == expected

    def test_remove_runs_git_worktree_remove_and_branch_delete(self, tmp_path):
        """hook-event remove should invoke git worktree remove and git branch -D."""
        manifest = tmp_path / "manifest.jsonl"
        payload = json.dumps({
            "cwd": str(tmp_path),
            "name": "old-wt",
            "hook_event_name": "WorktreeRemove",
        })
        mock_run = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        ))
        with (
            patch("sys.stdin", StringIO(payload)),
            patch("jcodemunch_mcp.hook_event.subprocess.run", mock_run),
            patch("jcodemunch_mcp.hook_event._get_worktree_base", return_value=""),
            patch("jcodemunch_mcp.hook_event._resolve_main_repo", return_value=str(tmp_path)),
        ):
            handle_hook_event("remove", manifest_path=manifest)

        assert mock_run.call_count == 2
        # First call: git worktree remove
        wt_args = mock_run.call_args_list[0][0][0]
        assert "worktree" in wt_args
        assert "remove" in wt_args
        # Second call: git branch -D worktree-old-wt
        br_args = mock_run.call_args_list[1][0][0]
        assert "branch" in br_args
        assert "-D" in br_args
        assert "worktree-old-wt" in br_args

    def test_remove_nonfatal_on_git_failure(self, tmp_path):
        """git worktree remove failure should not crash."""
        manifest = tmp_path / "manifest.jsonl"
        payload = json.dumps({
            "cwd": str(tmp_path),
            "name": "gone-wt",
            "hook_event_name": "WorktreeRemove",
        })
        fail_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="fatal: not a worktree",
        )
        with (
            patch("sys.stdin", StringIO(payload)),
            patch("jcodemunch_mcp.hook_event.subprocess.run", return_value=fail_result),
            patch("jcodemunch_mcp.hook_event._get_worktree_base", return_value=""),
            patch("jcodemunch_mcp.hook_event._resolve_main_repo", return_value=str(tmp_path)),
        ):
            # Should not raise
            handle_hook_event("remove", manifest_path=manifest)

        # Still records the remove event
        entry = json.loads(manifest.read_text().strip())
        assert entry["event"] == "remove"

    def test_create_fails_on_git_error(self, tmp_path):
        """git worktree add failure should exit with error."""
        manifest = tmp_path / "manifest.jsonl"
        payload = json.dumps({
            "cwd": str(tmp_path),
            "name": "bad-wt",
            "hook_event_name": "WorktreeCreate",
        })
        fail_result = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr="fatal: not a git repository",
        )
        with (
            patch("sys.stdin", StringIO(payload)),
            patch("jcodemunch_mcp.hook_event.subprocess.run", return_value=fail_result),
            patch("jcodemunch_mcp.hook_event._get_worktree_base", return_value=""),
        ):
            with pytest.raises(SystemExit):
                handle_hook_event("create", manifest_path=manifest)

    def test_legacy_worktree_path_skips_git(self, tmp_path):
        """Legacy worktreePath field records to manifest only — no git commands."""
        manifest = tmp_path / "manifest.jsonl"
        payload = json.dumps({"worktreePath": "/tmp/legacy-wt"})
        mock_run = MagicMock()
        with (
            patch("sys.stdin", StringIO(payload)),
            patch("jcodemunch_mcp.hook_event.subprocess.run", mock_run),
        ):
            handle_hook_event("create", manifest_path=manifest)

        # No git commands should be called for legacy path
        mock_run.assert_not_called()
        entry = json.loads(manifest.read_text().strip())
        assert entry["path"] == str(Path("/tmp/legacy-wt").resolve())

    def test_remove_resolves_main_repo(self, tmp_path):
        """Remove should run git commands from the main repo, not the worktree."""
        manifest = tmp_path / "manifest.jsonl"
        wt_path = tmp_path / ".claude" / "worktrees" / "my-wt"
        main_repo = tmp_path / "main-repo"
        payload = json.dumps({
            "cwd": str(wt_path),  # cwd is the worktree itself
            "name": "my-wt",
            "hook_event_name": "WorktreeRemove",
        })
        mock_run = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        ))
        with (
            patch("sys.stdin", StringIO(payload)),
            patch("jcodemunch_mcp.hook_event.subprocess.run", mock_run),
            patch("jcodemunch_mcp.hook_event._get_worktree_base", return_value=""),
            patch("jcodemunch_mcp.hook_event._resolve_main_repo", return_value=str(main_repo)),
        ):
            handle_hook_event("remove", manifest_path=manifest)

        # git -C should use main_repo, not the worktree path
        wt_args = mock_run.call_args_list[0][0][0]
        assert wt_args[2] == str(main_repo)

    def test_remove_with_worktree_path_runs_git(self, tmp_path):
        """Remove with worktree_path must still run git worktree remove."""
        manifest = tmp_path / "manifest.jsonl"
        payload = json.dumps({
            "cwd": str(tmp_path),
            "worktree_path": "/tmp/my-worktree",
        })
        mock_run = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        ))
        with (
            patch("sys.stdin", StringIO(payload)),
            patch("jcodemunch_mcp.hook_event.subprocess.run", mock_run),
            patch("jcodemunch_mcp.hook_event._resolve_main_repo", return_value=str(tmp_path)),
        ):
            handle_hook_event("remove", manifest_path=manifest)

        assert mock_run.call_count == 2
        # First call: git worktree remove
        wt_args = mock_run.call_args_list[0][0][0]
        assert "worktree" in wt_args
        assert "remove" in wt_args
        # Second call: git branch -D (name derived from path)
        br_args = mock_run.call_args_list[1][0][0]
        assert "branch" in br_args
        assert "-D" in br_args
        assert "worktree-my-worktree" in br_args
        # Manifest records the remove
        entry = json.loads(manifest.read_text().strip())
        assert entry["event"] == "remove"

    def test_creates_manifest_if_missing(self, tmp_path):
        manifest = tmp_path / "subdir" / "manifest.jsonl"
        payload = json.dumps({
            "cwd": str(tmp_path),
            "name": "new-wt",
            "hook_event_name": "WorktreeCreate",
        })
        with (
            patch("sys.stdin", StringIO(payload)),
            _mock_git_success(),
            patch("jcodemunch_mcp.hook_event._get_worktree_base", return_value=""),
        ):
            handle_hook_event("create", manifest_path=manifest)
        assert manifest.is_file()

    def test_exits_on_missing_path(self, tmp_path):
        manifest = tmp_path / "manifest.jsonl"
        payload = json.dumps({"unrelated": "data"})
        with patch("sys.stdin", StringIO(payload)):
            with pytest.raises(SystemExit):
                handle_hook_event("create", manifest_path=manifest)

    def test_exits_when_cwd_missing_without_legacy_path(self, tmp_path):
        """Modern path requires both cwd and name — missing cwd should error."""
        manifest = tmp_path / "manifest.jsonl"
        payload = json.dumps({"name": "orphan-wt"})
        with patch("sys.stdin", StringIO(payload)):
            with pytest.raises(SystemExit):
                handle_hook_event("create", manifest_path=manifest)

    def test_exits_when_name_missing_without_legacy_path(self, tmp_path):
        """Modern path requires both cwd and name — missing name should error."""
        manifest = tmp_path / "manifest.jsonl"
        payload = json.dumps({"cwd": "/some/repo"})
        with patch("sys.stdin", StringIO(payload)):
            with pytest.raises(SystemExit):
                handle_hook_event("create", manifest_path=manifest)


# ---------------------------------------------------------------------------
# Config upgrade test
# ---------------------------------------------------------------------------


class TestConfigUpgrade:
    def test_upgrade_adds_worktree_base_path(self, tmp_path, monkeypatch):
        """config --upgrade should add worktree_base_path to existing config."""
        from jcodemunch_mcp.config import generate_template, upgrade_config

        # Write a config without worktree_base_path
        config = tmp_path / "config.jsonc"
        config.write_text('{\n  "version": "0.0.0"\n}\n', encoding="utf-8")

        added, warnings = upgrade_config(config)
        assert "worktree_base_path" in added

        content = config.read_text(encoding="utf-8")
        assert "worktree_base_path" in content


# ---------------------------------------------------------------------------
# Manifest path migration tests
# ---------------------------------------------------------------------------


class TestManifestMigration:
    def test_migrates_legacy_manifest(self, tmp_path, monkeypatch):
        """Legacy manifest in ~/.claude/ should be moved to new location."""
        from jcodemunch_mcp import hook_event

        legacy = tmp_path / "legacy" / "jcodemunch-worktrees.jsonl"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"event":"create","path":"/a"}\n')

        new_path = tmp_path / "new" / "jcodemunch-worktrees.jsonl"

        monkeypatch.setattr(hook_event, "_LEGACY_MANIFEST_PATH", legacy)
        hook_event._migrate_manifest(new_path)

        assert new_path.is_file()
        assert not legacy.is_file()
        assert '"/a"' in new_path.read_text()

    def test_no_migration_if_new_exists(self, tmp_path, monkeypatch):
        """Don't overwrite existing new manifest with legacy one."""
        from jcodemunch_mcp import hook_event

        legacy = tmp_path / "legacy" / "jcodemunch-worktrees.jsonl"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"event":"create","path":"/old"}\n')

        new_path = tmp_path / "new" / "jcodemunch-worktrees.jsonl"
        new_path.parent.mkdir(parents=True)
        new_path.write_text('{"event":"create","path":"/new"}\n')

        monkeypatch.setattr(hook_event, "_LEGACY_MANIFEST_PATH", legacy)
        hook_event._migrate_manifest(new_path)

        # New file unchanged, legacy untouched
        assert '"/new"' in new_path.read_text()
        assert legacy.is_file()


# ---------------------------------------------------------------------------
# Manifest parsing tests
# ---------------------------------------------------------------------------


class TestReadManifest:
    def test_empty_file(self, tmp_path):
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text("")
        assert read_manifest(manifest) == set()

    def test_missing_file(self, tmp_path):
        assert read_manifest(tmp_path / "nope.jsonl") == set()

    def test_create_then_remove(self, tmp_path):
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps({"event": "create", "path": "/a"}) + "\n"
            + json.dumps({"event": "remove", "path": "/a"}) + "\n"
        )
        assert read_manifest(manifest) == set()

    def test_multiple_active(self, tmp_path):
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps({"event": "create", "path": "/a"}) + "\n"
            + json.dumps({"event": "create", "path": "/b"}) + "\n"
            + json.dumps({"event": "create", "path": "/c"}) + "\n"
        )
        assert read_manifest(manifest) == {"/a", "/b", "/c"}

    def test_skips_malformed_lines(self, tmp_path):
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            "not json\n"
            + json.dumps({"event": "create", "path": "/ok"}) + "\n"
            + "\n"
        )
        assert read_manifest(manifest) == {"/ok"}


# ---------------------------------------------------------------------------
# Git worktree parsing tests
# ---------------------------------------------------------------------------
PORCELAIN_OUTPUT = textwrap.dedent("""\
    worktree /home/user/project
    HEAD abc123
    branch refs/heads/main

    worktree /home/user/.claude-worktrees/project/dreamy-fox
    HEAD def456
    branch refs/heads/agent/dreamy-fox

    worktree /home/user/.claude/worktrees/feature-auth
    HEAD 789abc
    branch refs/heads/worktree-feature-auth

    worktree /home/user/.claude/worktrees/claude-feature-x
    HEAD ccc333
    branch refs/heads/claude/feature-x

    worktree /home/user/.claude/worktrees/manual-branch
    HEAD aaa111
    branch refs/heads/feature/manual

    worktree /home/user/.claude-worktrees/project/old-session
    HEAD bbb222
    branch refs/heads/agent/old-session
    prunable gitdir file points to non-existent location

    """)


class TestParseGitWorktrees:
    def _run_with_output(self, stdout):
        import subprocess

        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=stdout, stderr=""
        )
        with patch("jcodemunch_mcp.watcher.subprocess.run", return_value=fake_result):
            return parse_git_worktrees("/fake/repo")

    def test_includes_all_non_main_worktrees(self):
        result = self._run_with_output(PORCELAIN_OUTPUT)
        # Should include all non-main, non-prunable worktrees regardless of branch name
        assert "/home/user/.claude-worktrees/project/dreamy-fox" in result
        assert "/home/user/.claude/worktrees/feature-auth" in result
        assert "/home/user/.claude/worktrees/claude-feature-x" in result
        assert "/home/user/.claude/worktrees/manual-branch" in result

    def test_skips_main_worktree(self):
        result = self._run_with_output(PORCELAIN_OUTPUT)
        assert "/home/user/project" not in result

    def test_skips_prunable(self):
        result = self._run_with_output(PORCELAIN_OUTPUT)
        assert "/home/user/.claude-worktrees/project/old-session" not in result

    def test_handles_git_failure(self):
        import subprocess

        fake_result = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr="not a git repo"
        )
        with patch("jcodemunch_mcp.watcher.subprocess.run", return_value=fake_result):
            assert parse_git_worktrees("/fake/repo") == set()

    def test_handles_empty_output(self):
        result = self._run_with_output("")
        assert result == set()


# ---------------------------------------------------------------------------
# _local_repo_id
# ---------------------------------------------------------------------------


class TestLocalRepoId:
    def test_matches_index_folder_convention(self, tmp_path):
        folder = tmp_path / "my-worktree"
        folder.mkdir()
        repo_id = _local_repo_id(str(folder))
        resolved = str(folder.resolve())
        digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:8]
        assert repo_id == f"local/my-worktree-{digest}"


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCLI:
    def test_watch_claude_help(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["watch-claude", "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "--repos" in out

    def test_hook_event_help(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["hook-event", "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "create" in out
        assert "remove" in out


# ---------------------------------------------------------------------------
# Integration tests (mocked _watch_single)
# ---------------------------------------------------------------------------


class TestWatchClaudeIntegration:
    @pytest.mark.asyncio
    async def test_manifest_mode_starts_existing_worktrees(self, tmp_path):
        """Worktrees listed in manifest should be watched on startup."""
        wt = tmp_path / "wt-1"
        wt.mkdir()
        (wt / "main.py").write_text("x = 1\n")

        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps({"event": "create", "path": str(wt)}) + "\n"
        )

        started = []

        async def fake_watch_single(folder_path, **kwargs):
            started.append(folder_path)
            await asyncio.Event().wait()

        with (
            patch("jcodemunch_mcp.watcher._watch_single", side_effect=fake_watch_single),
            patch("jcodemunch_mcp.watcher.default_manifest_path", return_value=manifest),
        ):
            task = asyncio.create_task(
                watch_claude_worktrees(use_ai_summaries=False)
            )
            await asyncio.sleep(0.3)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(started) == 1
        assert started[0] == str(wt)

    @pytest.mark.asyncio
    async def test_manifest_mode_reacts_to_new_event(self, tmp_path):
        """A create event appended to the manifest should trigger a new watcher."""
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text("")  # empty initially

        wt = tmp_path / "new-wt"
        wt.mkdir()

        started = []

        async def fake_watch_single(folder_path, **kwargs):
            started.append(folder_path)
            await asyncio.Event().wait()

        with (
            patch("jcodemunch_mcp.watcher._watch_single", side_effect=fake_watch_single),
            patch("jcodemunch_mcp.watcher.default_manifest_path", return_value=manifest),
        ):
            task = asyncio.create_task(
                watch_claude_worktrees(use_ai_summaries=False)
            )
            await asyncio.sleep(0.3)
            assert len(started) == 0

            # Simulate hook appending a create event
            with open(manifest, "a") as f:
                f.write(json.dumps({"event": "create", "path": str(wt)}) + "\n")

            # Wait for watchfiles to pick it up
            await asyncio.sleep(1.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(started) == 1
        assert started[0] == str(wt)

    @pytest.mark.asyncio
    async def test_repos_mode_discovers_worktrees(self, tmp_path):
        """--repos mode should discover worktrees via git worktree list."""
        started = []

        async def fake_watch_single(folder_path, **kwargs):
            started.append(folder_path)
            await asyncio.Event().wait()

        wt_path = str(tmp_path / "wt-from-git")
        (tmp_path / "wt-from-git").mkdir()

        def fake_parse(repo_path):
            return {wt_path}

        manifest = tmp_path / "no-manifest.jsonl"  # nonexistent

        with (
            patch("jcodemunch_mcp.watcher._watch_single", side_effect=fake_watch_single),
            patch("jcodemunch_mcp.watcher.parse_git_worktrees", side_effect=fake_parse),
            patch("jcodemunch_mcp.watcher.default_manifest_path", return_value=manifest),
        ):
            task = asyncio.create_task(
                watch_claude_worktrees(
                    repos=["/fake/repo"],
                    poll_interval=0.1,
                    use_ai_summaries=False,
                )
            )
            await asyncio.sleep(0.3)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(started) == 1
        assert started[0] == wt_path

    @pytest.mark.asyncio
    async def test_repos_mode_cleans_up_removed(self, tmp_path):
        """When a worktree disappears from git, it should be stopped and cache invalidated."""
        started = []
        invalidated = []

        async def fake_watch_single(folder_path, **kwargs):
            started.append(folder_path)
            await asyncio.Event().wait()

        def fake_invalidate(repo, storage_path=None):
            invalidated.append(repo)
            return {"success": True}

        wt_path = str(tmp_path / "wt-gone")
        (tmp_path / "wt-gone").mkdir()

        call_count = 0

        def fake_parse(repo_path):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return {wt_path}
            return set()  # worktree gone

        manifest = tmp_path / "no-manifest.jsonl"

        with (
            patch("jcodemunch_mcp.watcher._watch_single", side_effect=fake_watch_single),
            patch("jcodemunch_mcp.watcher.parse_git_worktrees", side_effect=fake_parse),
            patch("jcodemunch_mcp.watcher.invalidate_cache", side_effect=fake_invalidate),
            patch("jcodemunch_mcp.watcher.default_manifest_path", return_value=manifest),
        ):
            task = asyncio.create_task(
                watch_claude_worktrees(
                    repos=["/fake/repo"],
                    poll_interval=0.1,
                    use_ai_summaries=False,
                )
            )
            await asyncio.sleep(0.8)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(started) == 1
        assert len(invalidated) == 1
        assert invalidated[0].startswith("local/wt-gone-")
