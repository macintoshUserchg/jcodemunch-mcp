"""Tests for the embedded watcher (--watcher flag on serve subcommand)."""
import asyncio
import os
import signal
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

pytest.importorskip("watchfiles")

from jcodemunch_mcp.watcher import watch_folders


# ---------------------------------------------------------------------------
# Task 1: External stop_event
# ---------------------------------------------------------------------------

class TestExternalStopEvent:
    """watch_folders with an external stop_event skips signal handler setup."""

    @pytest.fixture()
    def folder(self, tmp_path):
        d = tmp_path / "project"
        d.mkdir()
        return d

    def test_external_stop_event_no_signal_handlers(self, folder, tmp_path):
        """When stop_event is provided, watch_folders must NOT install signal handlers."""
        storage = tmp_path / "storage"
        storage.mkdir()
        stop = asyncio.Event()

        async def run():
            # Set stop immediately so watch_folders exits after lock acquisition
            stop.set()
            with patch("jcodemunch_mcp.watcher._watch_single") as mock_ws:
                mock_ws.return_value = None
                with patch("signal.signal") as mock_sig:
                    await watch_folders(
                        paths=[str(folder)],
                        storage_path=str(storage),
                        stop_event=stop,
                    )
                    # signal.signal should NOT have been called for SIGINT/SIGTERM
                    for call in mock_sig.call_args_list:
                        assert call[0][0] not in (signal.SIGINT, signal.SIGTERM), \
                            "signal handler installed despite external stop_event"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Task 2: Parse watcher flag (placeholder - implemented in server.py)
# ---------------------------------------------------------------------------

class TestServeWatcherCliArgs:
    """CLI argument parsing for --watcher on serve subcommand."""

    def test_watcher_flag_absent_is_none(self):
        from jcodemunch_mcp.server import main
        from jcodemunch_mcp.config import get as real_get
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        # Mock config to ensure watch=False so test is isolated from config file
        def mock_get(key, default=None):
            if key == "watch":
                return False
            return real_get(key, default)

        with patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run):
            with patch("jcodemunch_mcp.server.config_module.get", side_effect=mock_get):
                try:
                    main(["serve"])
                except SystemExit:
                    pass

        # The coroutine should be run_stdio_server (no wrapper)
        assert len(captured) == 1
        assert "watcher" not in captured[0].cr_code.co_name
        captured[0].close()

    def test_watcher_flag_present_no_value(self):
        """--watcher with no value should enable the watcher."""
        from jcodemunch_mcp.server import main
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        with patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run):
            try:
                main(["serve", "--watcher"])
            except SystemExit:
                pass

        assert len(captured) == 1
        # Should be _run_server_with_watcher
        assert "watcher" in captured[0].cr_code.co_name
        captured[0].close()

    def test_watcher_path_defaults_to_cwd(self, tmp_path):
        """--watcher without --watcher-path uses CWD."""
        from jcodemunch_mcp.server import main
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        with patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run), \
             patch("os.getcwd", return_value=str(tmp_path)):
            try:
                main(["serve", "--watcher"])
            except SystemExit:
                pass

        coro = captured[0]
        # Inspect the watcher_kwargs passed to _run_server_with_watcher
        frame = coro.cr_frame
        watcher_kwargs = frame.f_locals.get("watcher_kwargs")
        assert watcher_kwargs["paths"] == [str(tmp_path)]
        coro.close()

    def test_watcher_false_means_no_watcher(self):
        """--watcher=false should not launch the watcher."""
        from jcodemunch_mcp.server import main
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        with patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run):
            try:
                main(["serve", "--watcher=false"])
            except SystemExit:
                pass

        assert len(captured) == 1
        assert "watcher" not in captured[0].cr_code.co_name
        captured[0].close()


# ---------------------------------------------------------------------------
# Task 4: _run_server_with_watcher integration
# ---------------------------------------------------------------------------

class TestRunServerWithWatcher:
    """Integration: server + watcher lifecycle."""

    def test_watcher_stops_when_server_exits(self):
        """When the server coroutine completes, the watcher should be stopped."""
        from jcodemunch_mcp.server import _run_server_with_watcher
        from jcodemunch_mcp.watcher import WatcherManager

        watcher_stopped = False

        async def fake_server():
            await asyncio.sleep(0.05)  # simulate short-lived server

        class FakeWatcherManager(WatcherManager):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._stop_called = False

            def stop(self):
                nonlocal watcher_stopped
                self._stop_called = True
                watcher_stopped = True
                super().stop()

        async def run():
            with patch("jcodemunch_mcp.server.WatcherManager", side_effect=FakeWatcherManager):
                await _run_server_with_watcher(
                    fake_server, (),
                    dict(paths=["."], debounce_ms=2000, use_ai_summaries=False,
                         storage_path=None, extra_ignore_patterns=None,
                         follow_symlinks=False, idle_timeout_minutes=None),
                )

        asyncio.run(run())
        assert watcher_stopped

    def test_missing_watchfiles_exits_cleanly(self, tmp_path):
        """--watcher with missing watchfiles should exit with error."""
        from jcodemunch_mcp.server import main

        import builtins
        real_import = builtins.__import__

        def blocking_import(name, *args, **kwargs):
            if name == "watchfiles":
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=blocking_import):
            with pytest.raises(SystemExit) as exc_info:
                main(["serve", "--watcher"])
            assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Task 2: Parse watcher flag (placeholder - implemented in server.py)
# ---------------------------------------------------------------------------

class TestParseWatcherFlag:
    """Unit tests for _parse_watcher_flag."""

    def test_none_means_disabled(self):
        from jcodemunch_mcp.server import _parse_watcher_flag
        assert _parse_watcher_flag(None) is False

    def test_true_string_means_enabled(self):
        from jcodemunch_mcp.server import _parse_watcher_flag
        for val in ("true", "True", "TRUE", "1", "yes", "Yes"):
            assert _parse_watcher_flag(val) is True, f"Failed for {val!r}"

    def test_false_string_means_disabled(self):
        from jcodemunch_mcp.server import _parse_watcher_flag
        for val in ("false", "False", "0", "no", "No"):
            assert _parse_watcher_flag(val) is False, f"Failed for {val!r}"


# ---------------------------------------------------------------------------
# Task 5: Lock cleanup on external stop
# ---------------------------------------------------------------------------

class TestLockCleanupOnExternalStop:
    """Verify locks are released when watch_folders is stopped externally."""

    @pytest.fixture()
    def folders(self, tmp_path):
        d = tmp_path / "proj"
        d.mkdir()
        return d, tmp_path / "storage"

    def test_locks_released_after_external_stop(self, folders):
        folder, storage = folders
        storage.mkdir()

        from jcodemunch_mcp.watcher import _lock_path

        async def run():
            stop = asyncio.Event()

            async def set_stop_soon():
                await asyncio.sleep(0.1)
                stop.set()

            with patch("jcodemunch_mcp.watcher._watch_single") as mock_ws:
                # _watch_single should just wait forever
                async def hang(**kw):
                    await asyncio.Event().wait()
                mock_ws.side_effect = hang

                asyncio.create_task(set_stop_soon())
                await watch_folders(
                    paths=[str(folder)],
                    storage_path=str(storage),
                    stop_event=stop,
                )

            # Lock file should be gone after clean shutdown
            lp = _lock_path(str(folder), str(storage))
            assert not lp.exists(), f"Lock file not cleaned up: {lp}"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Bug 1: Bare print() leaks to stderr in quiet mode
# ---------------------------------------------------------------------------

class TestQuietModeNoLeaks:
    """Verify quiet mode suppresses ALL stderr output from watch_folders."""

    @pytest.fixture()
    def folder(self, tmp_path):
        d = tmp_path / "project"
        d.mkdir()
        return d

    def test_quiet_mode_suppresses_monitoring_message(self, folder, tmp_path):
        """The 'monitoring N folder(s)' message must NOT reach stderr in quiet mode."""
        storage = tmp_path / "storage"
        storage.mkdir()
        stop = asyncio.Event()
        stop.set()  # exit immediately

        async def run():
            with patch("jcodemunch_mcp.watcher._watch_single") as mock_ws:
                mock_ws.return_value = None
                with patch("sys.stderr", new_callable=MagicMock) as mock_stderr:
                    await watch_folders(
                        paths=[str(folder)],
                        storage_path=str(storage),
                        stop_event=stop,
                        quiet=True,
                    )
                    writes = [call[0][0] for call in mock_stderr.write.call_args_list]
                    assert not any("monitoring" in w for w in writes), \
                        f"'monitoring' leaked to stderr in quiet mode: {writes}"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Bug 2: Logger handlers never removed
# ---------------------------------------------------------------------------

class TestLoggerHandlerCleanup:
    """Logger handlers must be removed after watch_folders exits."""

    def test_no_handler_leak_on_repeated_calls(self, tmp_path):
        """Calling watch_folders multiple times must not accumulate handlers."""
        import logging
        d = tmp_path / "proj"
        d.mkdir()
        storage = tmp_path / "storage"
        storage.mkdir()

        async def run():
            stop = asyncio.Event()
            stop.set()
            wl = logging.getLogger("jcodemunch_mcp.watcher")

            with patch("jcodemunch_mcp.watcher._watch_single") as mock_ws:
                mock_ws.return_value = None

                for _ in range(3):
                    await watch_folders(
                        paths=[str(d)],
                        storage_path=str(storage),
                        stop_event=stop,
                        quiet=True,
                    )

            # Count quiet+log handlers that were NOT cleaned up
            remaining = [
                h for h in wl.handlers
                if isinstance(h, (logging.FileHandler, logging.NullHandler))
            ]
            assert len(remaining) == 0, f"Leaked handlers: {remaining}"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Feature: JCODEMUNCH_WATCH env var
# ---------------------------------------------------------------------------

class TestWatcherEnvVar:
    """JCODEMUNCH_WATCH env var enables watcher when --watcher flag absent.

    After the config-layering fix, env vars flow through config._apply_env_var_fallback
    rather than being read directly in _get_watcher_enabled.  These tests mock
    config_module.get to isolate _get_watcher_enabled from disk config state.
    """

    def test_env_var_enables_watcher(self):
        """config.get('watch') == True (via env var fallback) enables watcher."""
        from jcodemunch_mcp.server import main
        from jcodemunch_mcp.config import get as real_get
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        def mock_get(key, default=None):
            if key == "watch":
                return True  # simulates JCODEMUNCH_WATCH=1 applied through config system
            return real_get(key, default)

        with patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run), \
             patch("jcodemunch_mcp.server.config_module.get", side_effect=mock_get):
            try:
                main(["serve"])
            except SystemExit:
                pass

        assert len(captured) == 1
        assert "watcher" in captured[0].cr_code.co_name
        captured[0].close()

    def test_flag_overrides_env_var(self):
        """--watcher=false disables watcher even when config returns watch=True."""
        from jcodemunch_mcp.server import main
        from jcodemunch_mcp.config import get as real_get
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        def mock_get(key, default=None):
            if key == "watch":
                return True
            return real_get(key, default)

        with patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run), \
             patch("jcodemunch_mcp.server.config_module.get", side_effect=mock_get):
            try:
                main(["serve", "--watcher=false"])
            except SystemExit:
                pass

        assert len(captured) == 1
        assert "watcher" not in captured[0].cr_code.co_name
        captured[0].close()

    def test_env_var_false_disables(self):
        """config.get('watch') == False (JCODEMUNCH_WATCH=0 with no explicit config) does not start watcher."""
        from jcodemunch_mcp.server import main
        from jcodemunch_mcp.config import get as real_get
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        def mock_get(key, default=None):
            if key == "watch":
                return False  # simulates JCODEMUNCH_WATCH=0 / no config set
            return real_get(key, default)

        with patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run), \
             patch("jcodemunch_mcp.server.config_module.get", side_effect=mock_get):
            try:
                main(["serve"])
            except SystemExit:
                pass

        assert len(captured) == 1
        assert "watcher" not in captured[0].cr_code.co_name
        captured[0].close()


# ---------------------------------------------------------------------------
# Task 5: Config "watch" key enables watcher
# ---------------------------------------------------------------------------

class TestWatcherConfig:
    """Config file 'watch' key enables watcher when --watcher flag and env var absent."""

    def test_config_watch_true_enables_watcher(self):
        """config 'watch': true enables watcher when no flag/env var."""
        from jcodemunch_mcp.server import main
        from jcodemunch_mcp.config import get as real_get
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        def mock_get(key, default=None):
            if key == "watch":
                return True
            return real_get(key, default)

        with patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run), \
             patch("jcodemunch_mcp.server.config_module.get", side_effect=mock_get):
            try:
                main(["serve"])
            except SystemExit:
                pass

        assert len(captured) == 1
        assert "watcher" in captured[0].cr_code.co_name
        captured[0].close()

    def test_flag_overrides_config_watch_true(self):
        """--watcher=false disables watcher even when config 'watch': true."""
        from jcodemunch_mcp.server import main
        from jcodemunch_mcp.config import get as real_get
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        def mock_get(key, default=None):
            if key == "watch":
                return True
            return real_get(key, default)

        with patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run), \
             patch("jcodemunch_mcp.server.config_module.get", side_effect=mock_get):
            try:
                main(["serve", "--watcher=false"])
            except SystemExit:
                pass

        assert len(captured) == 1
        assert "watcher" not in captured[0].cr_code.co_name
        captured[0].close()

    def test_config_watch_takes_priority_over_env_var(self):
        """Config file 'watch': true wins over JCODEMUNCH_WATCH=0.

        After removing the direct os.environ.get("JCODEMUNCH_WATCH") check from
        _get_watcher_enabled, the env var is only a fallback for when the key is
        absent from the config file (handled by config._apply_env_var_fallback).
        An explicit config file setting always beats the env var.
        """
        from jcodemunch_mcp.server import main
        from jcodemunch_mcp.config import get as real_get
        captured = []

        def capturing_run(coro, *a, **kw):
            captured.append(coro)

        def mock_get(key, default=None):
            if key == "watch":
                return True  # simulates config file explicitly set "watch": true
            return real_get(key, default)

        with patch.dict(os.environ, {"JCODEMUNCH_WATCH": "0"}), \
             patch("jcodemunch_mcp.server.asyncio.run", side_effect=capturing_run), \
             patch("jcodemunch_mcp.server.config_module.get", side_effect=mock_get):
            try:
                main(["serve"])
            except SystemExit:
                pass

        assert len(captured) == 1
        # Config file wins — watcher IS enabled despite JCODEMUNCH_WATCH=0
        assert "watcher" in captured[0].cr_code.co_name
        captured[0].close()


# ---------------------------------------------------------------------------
# Fix 1: Logger propagation leak in quiet mode
# ---------------------------------------------------------------------------

class TestLoggerPropagation:
    """Logger messages must not leak to stderr via root logger in quiet/log mode.

    Fix: When quiet=True, watch_folders must set propagate=False on the watcher
    logger so that logger.exception() calls inside _watch_single do not bubble up
    to the root logger's StreamHandler (stderr). The finally block must restore
    propagate to its original value.
    """

    def test_quiet_mode_prevents_logger_exception_propagation(self, tmp_path):
        """watch_folders(quiet=True) must block logger.exception() from reaching stderr."""
        import logging
        import io

        d = tmp_path / "proj"
        d.mkdir()
        storage = tmp_path / "storage"
        storage.mkdir()

        # Capture root logger output
        root_handler = logging.StreamHandler(io.StringIO())
        root_handler.setLevel(logging.DEBUG)
        root_logger = logging.getLogger()
        root_logger.addHandler(root_handler)

        try:
            watcher_logger = logging.getLogger("jcodemunch_mcp.watcher")
            propagate_before = watcher_logger.propagate

            # Patch asyncio.wait to return immediately so _watch_single is actually awaited
            original_wait = asyncio.wait

            async def fake_wait(tasks, *, return_when):
                done, pending = await original_wait(tasks, return_when=asyncio.ALL_COMPLETED)
                return done, pending

            # Side effect that runs INSIDE _watch_single's execution window
            # (when propagate=False should be active in quiet mode)
            propagate_during_execution = []

            async def run_with_propagate_check(**kw):
                # This coroutine runs during watch_folders' execution window
                # Check that propagate=False is active
                propagate_during_execution.append(watcher_logger.propagate)
                # Simulate the finally restoration so we don't break cleanup
                old = watcher_logger.propagate
                watcher_logger.propagate = propagate_before
                watcher_logger.addHandler(logging.NullHandler())
                return None

            async def run():
                stop = asyncio.Event()
                stop.set()

                with patch("jcodemunch_mcp.watcher._watch_single", side_effect=run_with_propagate_check), \
                     patch("jcodemunch_mcp.watcher.asyncio.wait", side_effect=fake_wait):
                    await watch_folders(
                        paths=[str(d)],
                        storage_path=str(storage),
                        stop_event=stop,
                        quiet=True,
                    )

                # Key assertion: propagate must have been False DURING execution
                # (without the fix, propagate=True throughout and the test fails here)
                assert len(propagate_during_execution) == 1, \
                    f"_watch_single was not called (got {len(propagate_during_execution)} calls)"
                assert propagate_during_execution[0] is False, (
                    f"propagate must be False during quiet mode execution, "
                    f"got {propagate_during_execution[0]}"
                )

                # After watch_folders returns, propagate must be restored
                assert watcher_logger.propagate is propagate_before, (
                    f"propagate was not restored: expected {propagate_before}, "
                    f"got {watcher_logger.propagate}"
                )

            asyncio.run(run())
        finally:
            root_logger.removeHandler(root_handler)


# ---------------------------------------------------------------------------
# Bug 5: Log file permission error produces warning, not crash
# ---------------------------------------------------------------------------

def test_watcher_log_permission_error_is_warning_not_crash(tmp_path):
    """Unopenable log file is handled gracefully inside watch_folders — no crash at server level.

    Permission handling is in _run_server_with_watcher, which catches OSError
    on log file open and falls back to quiet mode with a warning.
    The server never sees the error.
    """
    from jcodemunch_mcp.server import _run_server_with_watcher

    async def fake_server():
        await asyncio.sleep(0.05)

    class FakeWatcherManager:
        """Fake manager that captures initialization args."""
        def __init__(self, *args, **kwargs):
            self._stop_event = kwargs.get("_stop_event")
            self._watched = set()
            self._locked = set()

        def is_watched(self, folder):
            return folder in self._watched

        def list_folders(self):
            return sorted(self._watched)

        async def add_folder(self, folder):
            self._watched.add(folder)
            return {"status": "started"}

        async def remove_folder(self, folder):
            self._watched.discard(folder)
            return {"status": "stopped"}

        async def ensure_indexed(self, folder, **kwargs):
            return {"status": "indexed"}

        async def run(self):
            if self._stop_event:
                await self._stop_event.wait()

        def stop(self):
            if self._stop_event:
                self._stop_event.set()

    # A path that will fail to open for write on Windows (system dir)
    protected_path = "C:\\Windows\\System32\\protected_test.log"

    async def run():
        with patch("jcodemunch_mcp.server.WatcherManager", side_effect=FakeWatcherManager):
            await _run_server_with_watcher(
                fake_server, (),
                dict(paths=["."], debounce_ms=2000, use_ai_summaries=False,
                     storage_path=None, extra_ignore_patterns=None,
                     follow_symlinks=False, idle_timeout_minutes=None),
                log_path=protected_path,
            )

    # The function must NOT raise PermissionError/OSError — server stays up
    # Even if the log file can't be opened, the manager is created with log_file_handle=None
    asyncio.run(run())
    # Test passes if no exception is raised


# ---------------------------------------------------------------------------
# Bug: sys.exit(1) kills entire embedded server
# ---------------------------------------------------------------------------

class TestNoValidDirsEmbedded:
    """watch_folders must not sys.exit(1) when embedded — it should raise instead."""

    def test_no_sys_exit_on_no_valid_dirs_embedded(self, tmp_path):
        """When stop_event is provided and no dirs are valid, NO sys.exit occurs."""
        import pathlib

        async def run():
            stop = asyncio.Event()

            with patch("jcodemunch_mcp.watcher._watch_single") as mock_ws:
                async def hang(**kw):
                    await asyncio.Event().wait()
                mock_ws.side_effect = hang

                with patch.object(pathlib.Path, "is_dir", return_value=False):
                    # This should raise, not sys.exit
                    with pytest.raises(Exception) as exc_info:
                        await watch_folders(
                            paths=["/nonexistent/path"],
                            storage_path=str(tmp_path / "storage"),
                            stop_event=stop,
                            quiet=True,
                        )
                    # Must NOT be a bare SystemExit
                    if isinstance(exc_info.value, SystemExit):
                        pytest.fail(
                            f"sys.exit({exc_info.value.code}) was called instead of raising "
                            f"a proper exception. When embedded, watch_folders must not exit the process."
                        )

        asyncio.run(run())

    def test_embedded_invalid_path_caught_by_wrapper(self, tmp_path):
        """_run_server_with_watcher catches WatcherError and continues without crashing."""
        from jcodemunch_mcp.server import _run_server_with_watcher
        from jcodemunch_mcp.watcher import WatcherError

        async def fake_server():
            await asyncio.sleep(0.01)

        async def run():
            with patch("jcodemunch_mcp.server.watch_folders") as mock_wf:
                # Simulate what happens when no dirs are valid
                async def raise_on_invalid(**kw):
                    raise WatcherError("No valid directories to watch")

                mock_wf.side_effect = raise_on_invalid
                # Should NOT raise uncaught — wrapper should handle it and server continues
                await _run_server_with_watcher(
                    fake_server, (),
                    dict(paths=["/nonexistent"], debounce_ms=2000,
                         use_ai_summaries=False, storage_path=str(tmp_path / "storage"),
                         extra_ignore_patterns=None, follow_symlinks=False,
                         idle_timeout_minutes=None),
                    None,
                )

        asyncio.run(run())  # Should complete without raising


# ---------------------------------------------------------------------------
# Feature: Config-driven watcher parameters
# ---------------------------------------------------------------------------

class TestWatcherConfigParams:
    """Watcher parameters read from config.jsonc when CLI flags absent."""

    def _run_serve_capture_kwargs(self, config_overrides, cli_args=None):
        """Run main(['serve', ...]) and capture watcher_kwargs + log_path.

        Returns (watcher_kwargs, log_path) or (None, None) if watcher was not enabled.
        """
        from jcodemunch_mcp.server import main
        from jcodemunch_mcp.config import get as real_get

        base_config = {"watch": True, "use_ai_summaries": False}
        base_config.update(config_overrides)

        def mock_get(key, default=None):
            if key in base_config:
                return base_config[key]
            return real_get(key, default)

        def noop_run(coro, *a, **kw):
            # Close coroutine to avoid ResourceWarning
            if hasattr(coro, "close"):
                coro.close()

        with patch("jcodemunch_mcp.server.config_module.get", side_effect=mock_get), \
             patch("jcodemunch_mcp.server._run_server_with_watcher") as mock_rsww, \
             patch("jcodemunch_mcp.server.asyncio.run", side_effect=noop_run):
            try:
                main(cli_args or ["serve"])
            except SystemExit:
                pass

        if not mock_rsww.called:
            return None, None
        args, kwargs_call = mock_rsww.call_args
        # _run_server_with_watcher(server_func, server_args, watcher_kwargs, log_path)
        watcher_kwargs = args[2] if len(args) > 2 else kwargs_call.get("watcher_kwargs")
        log_path = args[3] if len(args) > 3 else kwargs_call.get("log_path")
        return dict(watcher_kwargs), log_path

    def test_debounce_from_config(self):
        """watch_debounce_ms config value flows to watcher_kwargs."""
        kwargs, _ = self._run_serve_capture_kwargs({"watch_debounce_ms": 5000})
        assert kwargs is not None
        assert kwargs["debounce_ms"] == 5000

    def test_debounce_cli_overrides_config(self):
        """--watcher-debounce overrides watch_debounce_ms from config."""
        kwargs, _ = self._run_serve_capture_kwargs(
            {"watch_debounce_ms": 5000},
            ["serve", "--watcher-debounce=100"],
        )
        assert kwargs is not None
        assert kwargs["debounce_ms"] == 100

    def test_paths_from_config(self):
        """watch_paths config value flows to watcher_kwargs."""
        kwargs, _ = self._run_serve_capture_kwargs({"watch_paths": ["/tmp/proj"]})
        assert kwargs is not None
        assert kwargs["paths"] == ["/tmp/proj"]

    def test_paths_cli_overrides_config(self):
        """--watcher-path overrides watch_paths from config."""
        kwargs, _ = self._run_serve_capture_kwargs(
            {"watch_paths": ["/tmp/proj"]},
            ["serve", "--watcher-path", "/other"],
        )
        assert kwargs is not None
        assert kwargs["paths"] == ["/other"]

    def test_paths_default_is_cwd(self):
        """When neither CLI nor config provides paths, default is CWD."""
        kwargs, _ = self._run_serve_capture_kwargs({})
        assert kwargs is not None
        assert kwargs["paths"] == [os.getcwd()]

    def test_idle_timeout_from_config(self):
        """watch_idle_timeout config value flows to watcher_kwargs."""
        kwargs, _ = self._run_serve_capture_kwargs({"watch_idle_timeout": 30})
        assert kwargs is not None
        assert kwargs["idle_timeout_minutes"] == 30

    def test_idle_timeout_cli_overrides_config(self):
        """--watcher-idle-timeout overrides watch_idle_timeout from config."""
        kwargs, _ = self._run_serve_capture_kwargs(
            {"watch_idle_timeout": 30},
            ["serve", "--watcher-idle-timeout=10"],
        )
        assert kwargs is not None
        assert kwargs["idle_timeout_minutes"] == 10

    def test_extra_ignore_from_config(self):
        """watch_extra_ignore config value flows to watcher_kwargs."""
        kwargs, _ = self._run_serve_capture_kwargs({"watch_extra_ignore": ["*.log", "build/"]})
        assert kwargs is not None
        assert kwargs["extra_ignore_patterns"] == ["*.log", "build/"]

    def test_extra_ignore_cli_overrides_config(self):
        """--watcher-extra-ignore overrides watch_extra_ignore from config."""
        kwargs, _ = self._run_serve_capture_kwargs(
            {"watch_extra_ignore": ["*.log"]},
            ["serve", "--watcher-extra-ignore", "*.tmp"],
        )
        assert kwargs is not None
        assert kwargs["extra_ignore_patterns"] == ["*.tmp"]

    def test_follow_symlinks_from_config(self):
        """watch_follow_symlinks config value flows to watcher_kwargs."""
        kwargs, _ = self._run_serve_capture_kwargs({"watch_follow_symlinks": True})
        assert kwargs is not None
        assert kwargs["follow_symlinks"] is True

    def test_follow_symlinks_cli_overrides_config(self):
        """--watcher-follow-symlinks overrides watch_follow_symlinks from config."""
        kwargs, _ = self._run_serve_capture_kwargs(
            {"watch_follow_symlinks": False},
            ["serve", "--watcher-follow-symlinks"],
        )
        assert kwargs is not None
        assert kwargs["follow_symlinks"] is True

    def test_log_from_config(self):
        """watch_log config value flows to log_path."""
        _, log_path = self._run_serve_capture_kwargs({"watch_log": "/tmp/w.log"})
        assert log_path == "/tmp/w.log"

    def test_log_cli_overrides_config(self):
        """--watcher-log overrides watch_log from config."""
        _, log_path = self._run_serve_capture_kwargs(
            {"watch_log": "/tmp/w.log"},
            ["serve", "--watcher-log=/other.log"],
        )
        assert log_path == "/other.log"

    def test_use_ai_summaries_false_from_config(self):
        """use_ai_summaries=false in config disables AI in watcher."""
        kwargs, _ = self._run_serve_capture_kwargs({"use_ai_summaries": False})
        assert kwargs is not None
        assert kwargs["use_ai_summaries"] is False

    def test_use_ai_summaries_cli_overrides_config(self):
        """--watcher-no-ai-summaries overrides use_ai_summaries from config."""
        kwargs, _ = self._run_serve_capture_kwargs(
            {"use_ai_summaries": True},
            ["serve", "--watcher-no-ai-summaries"],
        )
        assert kwargs is not None
        assert kwargs["use_ai_summaries"] is False


class TestDefaultUseAiSummaries:
    """Unit tests for _default_use_ai_summaries() tri-state normalization."""

    def _call(self, config_value):
        from jcodemunch_mcp import server as server_mod
        from jcodemunch_mcp import config as config_mod

        with patch.object(config_mod, "get", return_value=config_value):
            return server_mod._default_use_ai_summaries()

    def test_auto_string_maps_to_true(self):
        """'auto' (new default) normalizes to True."""
        assert self._call("auto") is True

    def test_true_bool_maps_to_true(self):
        """True (bool, backward compat) normalizes to True."""
        assert self._call(True) is True

    def test_false_bool_maps_to_false(self):
        """False (bool) normalizes to False."""
        assert self._call(False) is False

    def test_false_string_maps_to_false(self):
        """'false' (string) normalizes to False."""
        assert self._call("false") is False

    def test_true_string_maps_to_true(self):
        """'true' (string) normalizes to True."""
        assert self._call("true") is True

    def test_zero_string_maps_to_false(self):
        """'0' string normalizes to False."""
        assert self._call("0") is False

    def test_off_string_maps_to_false(self):
        """'off' string normalizes to False."""
        assert self._call("off") is False
