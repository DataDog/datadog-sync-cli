# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Unit tests for the prune command (PR 3).

Tests fall in two layers:
  1. CLI argparse smoke (CliRunner) — verifies the command exists and the
     CLI-layer guards (e.g. --resources required) fire before run_cmd is
     invoked.
  2. Handler.prune() preconditions — directly invokes ResourcesHandler.prune()
     with a mock Configuration, verifying each precondition raises UsageError
     before the API import is attempted.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click import UsageError
from click.testing import CliRunner

from datadog_sync.cli import cli


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


class TestPruneCliArgparse:
    def test_prune_command_exists(self, runner):
        result = runner.invoke(cli, ["prune", "--help"])
        assert result.exit_code == 0
        assert "prune" in result.output.lower()

    def test_resources_is_required_at_cli_layer(self, runner):
        # No --resources, no --validate=false — the CLI layer must reject this
        # *before* run_cmd attempts to authenticate.
        result = runner.invoke(cli, ["prune", "--validate=false", "--resource-per-file"])
        assert result.exit_code != 0
        assert "requires --resources" in (result.output + (result.stderr or ""))

    def test_force_and_dry_run_flags_accepted(self, runner):
        # Flags parse cleanly even if the run later fails on missing creds.
        result = runner.invoke(
            cli,
            [
                "prune",
                "--resources",
                "monitors",
                "--validate=false",
                "--resource-per-file",
                "--force",
                "--dry-run",
            ],
        )
        # exit_code 2 would mean argparse rejection; anything else means
        # the flags parsed and the handler started doing real work.
        assert result.exit_code != 2


def _mock_handler(emit_json=False, prune_force=False, prune_dry_run=False, filters=None, resource_per_file=True):
    """Construct a ResourcesHandler with a mock Configuration that has the
    minimum fields the precondition checks read."""
    from datadog_sync.utils.resources_handler import ResourcesHandler

    config = MagicMock()
    config.resource_per_file = resource_per_file
    config.filters = filters or {}
    config.emit_json = emit_json
    config.prune_force = prune_force
    config.prune_dry_run = prune_dry_run
    config.resources_arg = ["monitors"]
    config.logger = MagicMock()
    handler = ResourcesHandler.__new__(ResourcesHandler)
    handler.config = config
    handler.import_resources_without_saving = AsyncMock()
    handler._emit = MagicMock()
    return handler


class TestPrunePreconditions:
    """Each precondition test must assert that import_resources_without_saving
    is NEVER called — the guard fires before any API access."""

    def test_no_resource_per_file_raises(self):
        import asyncio

        handler = _mock_handler(resource_per_file=False)
        with pytest.raises(UsageError, match="--resource-per-file"):
            asyncio.run(handler.prune())
        handler.import_resources_without_saving.assert_not_awaited()

    def test_filters_raises(self):
        import asyncio

        handler = _mock_handler(filters={"monitors": [object()]})
        with pytest.raises(UsageError, match="--filters"):
            asyncio.run(handler.prune())
        handler.import_resources_without_saving.assert_not_awaited()

    def test_json_without_force_or_dry_run_raises(self):
        import asyncio

        handler = _mock_handler(emit_json=True, prune_force=False, prune_dry_run=False)
        with pytest.raises(UsageError, match="--force or --dry-run"):
            asyncio.run(handler.prune())
        handler.import_resources_without_saving.assert_not_awaited()

    def test_json_with_force_proceeds(self):
        """--json + --force is allowed (no prompt; deletes); precondition passes."""
        import asyncio

        handler = _mock_handler(emit_json=True, prune_force=True)
        # Mock state to return empty stale set → method returns cleanly.
        handler.config.state = MagicMock()
        handler.config.state.compute_stale_files = MagicMock(return_value={})
        asyncio.run(handler.prune())
        handler.import_resources_without_saving.assert_awaited_once()

    def test_json_with_dry_run_proceeds(self):
        """--json + --dry-run is allowed (no prompt; nothing deleted)."""
        import asyncio

        handler = _mock_handler(emit_json=True, prune_dry_run=True)
        handler.config.state = MagicMock()
        handler.config.state.compute_stale_files = MagicMock(return_value={})
        asyncio.run(handler.prune())
        handler.import_resources_without_saving.assert_awaited_once()


class TestPruneFlow:
    def test_dry_run_does_not_delete(self):
        """--dry-run: compute_stale_files called, delete_stale_files NOT called."""
        import asyncio

        from datadog_sync.constants import Origin

        handler = _mock_handler(prune_dry_run=True)
        handler.config.state = MagicMock()
        handler.config.state.compute_stale_files = MagicMock(
            return_value={(Origin.SOURCE, "monitors"): {"monitors.stale.json"}}
        )
        asyncio.run(handler.prune())
        handler.config.state.delete_stale_files.assert_not_called()

    def test_force_skips_confirm(self):
        """--force: confirm() not called, delete_stale_files invoked."""
        import asyncio

        from datadog_sync.constants import Origin

        handler = _mock_handler(prune_force=True)
        handler.config.state = MagicMock()
        handler.config.state.compute_stale_files = MagicMock(
            return_value={(Origin.SOURCE, "monitors"): {"monitors.stale.json"}}
        )
        handler.config.state.delete_stale_files = MagicMock(return_value={(Origin.SOURCE, "monitors"): (1, 0)})
        with patch("datadog_sync.utils.resources_handler.confirm") as mock_confirm:
            asyncio.run(handler.prune())
        mock_confirm.assert_not_called()
        handler.config.state.delete_stale_files.assert_called_once()

    def test_partial_failure_emits_partial_status(self):
        """delete_stale_files returns failures → _emit called with status='partial'."""
        import asyncio

        from datadog_sync.constants import Origin

        handler = _mock_handler(prune_force=True)
        handler.config.state = MagicMock()
        handler.config.state.compute_stale_files = MagicMock(
            return_value={(Origin.SOURCE, "monitors"): {"a.json", "b.json"}}
        )
        handler.config.state.delete_stale_files = MagicMock(return_value={(Origin.SOURCE, "monitors"): (1, 1)})
        asyncio.run(handler.prune())
        # Expect exactly one _emit call with status='partial'
        emit_calls = handler._emit.call_args_list
        assert len(emit_calls) == 1
        assert emit_calls[0].kwargs["status"] == "partial"
        assert "failed=1" in emit_calls[0].kwargs["reason"]

    def test_empty_stale_set_logs_and_returns(self):
        """No stale files → INFO log, no delete call, no _emit."""
        import asyncio

        handler = _mock_handler(prune_force=True)
        handler.config.state = MagicMock()
        handler.config.state.compute_stale_files = MagicMock(return_value={})
        asyncio.run(handler.prune())
        handler.config.state.delete_stale_files.assert_not_called()
        handler._emit.assert_not_called()
        # "no stale state files found" log line
        info_calls = [c for c in handler.config.logger.info.call_args_list]
        assert any("no stale state files" in str(c) for c in info_calls)

    def test_progress_bar_flag_respected_during_import(self):
        """The internal import can be slow on large orgs, so prune honors
        --show-progress-bar. The flag value passed in is what the import sees;
        prune does not override it."""
        import asyncio

        handler = _mock_handler(prune_force=True)
        handler.config.show_progress_bar = True
        handler.config.state = MagicMock()
        handler.config.state.compute_stale_files = MagicMock(return_value={})
        seen_during_import = {}

        async def record_pbar(*args, **kwargs):
            seen_during_import["pbar"] = handler.config.show_progress_bar

        handler.import_resources_without_saving = record_pbar
        asyncio.run(handler.prune())
        assert seen_during_import["pbar"] is True, "progress bar flag should reach the internal import unchanged"
        assert handler.config.show_progress_bar is True, "config must be untouched"

    def test_progress_bar_disabled_passes_through(self):
        """When the user passes --show-progress-bar=False, the import sees False."""
        import asyncio

        handler = _mock_handler(prune_force=True)
        handler.config.show_progress_bar = False
        handler.config.state = MagicMock()
        handler.config.state.compute_stale_files = MagicMock(return_value={})
        seen_during_import = {}

        async def record_pbar(*args, **kwargs):
            seen_during_import["pbar"] = handler.config.show_progress_bar

        handler.import_resources_without_saving = record_pbar
        asyncio.run(handler.prune())
        assert seen_during_import["pbar"] is False

    def test_snapshot_fence_intersects_two_listings(self):
        """compute_stale_files is called twice; final delete uses intersection."""
        import asyncio

        from datadog_sync.constants import Origin

        handler = _mock_handler(prune_force=True)
        handler.config.state = MagicMock()
        # First call: {a, b, c}; second call: {b, c, d} (a was rewritten by a
        # concurrent sync; d became newly stale). Intersection: {b, c}.
        handler.config.state.compute_stale_files = MagicMock(
            side_effect=[
                {(Origin.SOURCE, "monitors"): {"a.json", "b.json", "c.json"}},
                {(Origin.SOURCE, "monitors"): {"b.json", "c.json", "d.json"}},
            ]
        )
        captured = {}

        def capture(arg):
            captured["fenced"] = arg
            return {(Origin.SOURCE, "monitors"): (2, 0)}

        handler.config.state.delete_stale_files = MagicMock(side_effect=capture)
        asyncio.run(handler.prune())
        assert handler.config.state.compute_stale_files.call_count == 2
        assert captured["fenced"][(Origin.SOURCE, "monitors")] == {"b.json", "c.json"}
