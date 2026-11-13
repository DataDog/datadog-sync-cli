# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Parser-level checks only, so the sync pipeline never runs (see test_force_missing_dependencies_cli.py)."""

import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli

FLAG = "--sync-dashboards-with-missing-dependencies"


def _param(command_name):
    for p in cli.commands[command_name].params:
        if FLAG in p.opts:
            return p
    return None


@pytest.mark.parametrize("command_name", ["sync", "diffs", "migrate"])
def test_flag_registered_with_default_false(command_name):
    p = _param(command_name)
    assert p is not None
    assert p.is_flag is True
    assert p.default is False


def test_import_rejects_flag():
    # Usage error (exit 2) is raised at parse time, before any pipeline runs.
    result = CliRunner(mix_stderr=False).invoke(cli, ["import", FLAG])
    assert result.exit_code == 2


def _base_kwargs(tmp_path):
    return dict(
        resources="dashboards",
        resource_per_file=True,
        source_api_key="k",
        source_app_key="k",
        destination_api_key="k",
        destination_app_key="k",
        source_api_url="https://example.com",
        destination_api_url="https://example.com",
        storage_type="local",
        source_resources_path=str(tmp_path / "source"),
        destination_resources_path=str(tmp_path / "dest"),
        max_workers=1,
        send_metrics=False,
        verify_ddr_status=False,
        validate=False,
        show_progress_bar=False,
        allow_self_lockout=False,
        force_missing_dependencies=False,
        skip_failed_resource_connections=False,
    )


def test_build_config_defaults_false(tmp_path):
    from datadog_sync.constants import Command
    from datadog_sync.utils.configuration import build_config

    cfg = build_config(Command.SYNC, **_base_kwargs(tmp_path))
    assert cfg.sync_dashboards_with_missing_dependencies is False


def test_build_config_true_when_passed(tmp_path):
    from datadog_sync.constants import Command
    from datadog_sync.utils.configuration import build_config

    kwargs = _base_kwargs(tmp_path)
    kwargs["sync_dashboards_with_missing_dependencies"] = True
    cfg = build_config(Command.SYNC, **kwargs)
    assert cfg.sync_dashboards_with_missing_dependencies is True
