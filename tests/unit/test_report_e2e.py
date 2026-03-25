# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
E2E tests for --json outcome streaming on stdout.

These tests invoke the CLI via CliRunner with pre-populated state files
and validate the JSON lines written to stdout. They don't require VCR
cassettes because diffs only reads local state.
"""

import json
import os

import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli


@pytest.fixture(autouse=True)
def work_in_tmpdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield tmp_path


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _write_state(base_dir, resource_type, resources):
    """Write resource state files in the standard format."""
    os.makedirs(base_dir, exist_ok=True)
    path = os.path.join(base_dir, f"{resource_type}.json")
    with open(path, "w") as f:
        json.dump(resources, f)


def _setup_source_dashboards(base_dir="resources/source"):
    """Set up 3 source dashboards."""
    dashboards = {
        "abc-123": {
            "id": "abc-123",
            "title": "Dashboard A",
            "widgets": [],
            "layout_type": "ordered",
        },
        "def-456": {
            "id": "def-456",
            "title": "Dashboard B",
            "widgets": [],
            "layout_type": "ordered",
        },
        "ghi-789": {
            "id": "ghi-789",
            "title": "Dashboard C",
            "widgets": [],
            "layout_type": "ordered",
        },
    }
    _write_state(base_dir, "dashboards", dashboards)
    return dashboards


def _setup_dest_dashboards(base_dir="resources/destination"):
    """Set up 1 destination dashboard (matching abc-123 from source)."""
    dashboards = {
        "abc-123": {
            "id": "dest-abc-123",
            "title": "Dashboard A",
            "widgets": [],
            "layout_type": "ordered",
        },
    }
    _write_state(base_dir, "dashboards", dashboards)
    return dashboards


def _setup_dest_dashboards_with_drift(base_dir="resources/destination"):
    """Set up dest dashboard with title drift so diffs detects an update."""
    dashboards = {
        "abc-123": {
            "id": "dest-abc-123",
            "title": "Dashboard A (stale)",
            "widgets": [],
            "layout_type": "ordered",
        },
    }
    _write_state(base_dir, "dashboards", dashboards)
    return dashboards


def _parse_outcomes(output):
    """Parse JSON outcome lines from CLI stdout.

    Only lines that are valid JSON with ``"type": "outcome"`` are returned.
    This filters out log events and any non-JSON preamble.
    """
    outcomes = []
    for line in output.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            if parsed.get("type") == "outcome":
                outcomes.append(parsed)
        except json.JSONDecodeError:
            continue
    return outcomes


def _run_diffs(runner, extra_args=None):
    """Run diffs command with --json and return (exit_code, outcomes, raw_output)."""
    _setup_source_dashboards()
    _setup_dest_dashboards()

    args = [
        "diffs",
        "--validate=false",
        "--verify-ddr-status=False",
        "--resources=dashboards",
        "--send-metrics=False",
        "--skip-failed-resource-connections=true",
        "--json",
    ]
    if extra_args:
        args.extend(extra_args)

    ret = runner.invoke(cli, args)
    outcomes = _parse_outcomes(ret.output)
    return ret.exit_code, outcomes, ret.output


class TestJsonFlagAccepted:
    """Test that --json flag is accepted by commands."""

    @staticmethod
    def _check_no_option_error(ret):
        stderr = ret.stderr_bytes.decode() if ret.stderr_bytes else ""
        output = ret.output or ""
        combined = stderr + output
        assert "No such option" not in combined, f"--json flag not recognized: {combined}"
        assert ret.exit_code != 2, f"CLI returned usage error (exit 2): {combined}"

    def test_diffs_accepts_json_flag(self, runner):
        _setup_source_dashboards()
        ret = runner.invoke(
            cli,
            [
                "diffs",
                "--validate=false",
                "--verify-ddr-status=False",
                "--resources=dashboards",
                "--send-metrics=False",
                "--json",
            ],
        )
        self._check_no_option_error(ret)

    def test_import_accepts_json_flag(self, runner):
        ret = runner.invoke(
            cli,
            [
                "import",
                "--validate=false",
                "--verify-ddr-status=False",
                "--resources=dashboards",
                "--send-metrics=False",
                "--json",
            ],
        )
        self._check_no_option_error(ret)

    def test_sync_accepts_json_flag(self, runner):
        ret = runner.invoke(
            cli,
            [
                "sync",
                "--validate=false",
                "--verify-ddr-status=False",
                "--resources=dashboards",
                "--send-metrics=False",
                "--create-global-downtime=False",
                "--json",
            ],
        )
        self._check_no_option_error(ret)


class TestOutcomeStreaming:
    """Test that JSON outcome lines appear on stdout when --json is passed."""

    def test_outcomes_emitted_on_stdout(self, runner):
        exit_code, outcomes, _ = _run_diffs(runner)
        assert exit_code == 0
        assert len(outcomes) > 0

    def test_each_outcome_has_required_fields(self, runner):
        _, outcomes, _ = _run_diffs(runner)
        for o in outcomes:
            assert "resource_type" in o
            assert "id" in o
            assert "action_type" in o
            assert "status" in o
            assert "action_sub_type" in o
            assert "reason" in o

    def test_outcomes_are_valid_json(self, runner):
        _, outcomes, _ = _run_diffs(runner)
        assert len(outcomes) >= 3


class TestNoJsonWithoutFlag:
    """Test that no JSON lines appear on stdout without --json."""

    def test_no_outcomes_without_json_flag(self, runner):
        _setup_source_dashboards()
        _setup_dest_dashboards()
        ret = runner.invoke(
            cli,
            [
                "diffs",
                "--validate=false",
                "--verify-ddr-status=False",
                "--resources=dashboards",
                "--send-metrics=False",
                "--skip-failed-resource-connections=true",
            ],
        )
        outcomes = _parse_outcomes(ret.output)
        assert len(outcomes) == 0, f"Expected no JSON outcomes without --json, got {len(outcomes)}"


class TestOutcomeContent:
    """Test that diffs outcomes correctly identify resource states."""

    def test_new_resources_reported_as_create(self, runner):
        _, outcomes, _ = _run_diffs(runner)
        create_ids = {
            o["id"] for o in outcomes if o["status"] == "success" and o["action_sub_type"] == "create"
        }
        assert "def-456" in create_ids
        assert "ghi-789" in create_ids

    def test_matching_resource_reported_as_skip(self, runner):
        _, outcomes, _ = _run_diffs(runner)
        skip_ids = {o["id"] for o in outcomes if o["status"] == "skipped"}
        assert "abc-123" in skip_ids

    def test_all_dashboards_accounted_for(self, runner):
        _, outcomes, _ = _run_diffs(runner)
        dashboard_outcomes = [o for o in outcomes if o["resource_type"] == "dashboards"]
        assert len(dashboard_outcomes) == 3

    def test_outcome_resource_type(self, runner):
        _, outcomes, _ = _run_diffs(runner)
        for o in outcomes:
            assert o["resource_type"] == "dashboards"

    def test_outcome_action_type(self, runner):
        _, outcomes, _ = _run_diffs(runner)
        for o in outcomes:
            assert o["action_type"] in ("sync", "delete")

    def test_skip_reason_populated(self, runner):
        _, outcomes, _ = _run_diffs(runner)
        skips = [o for o in outcomes if o["status"] == "skipped"]
        for s in skips:
            assert s["reason"] != "", f"Skip outcome for {s['id']} has empty reason"

    def test_create_has_action_sub_type(self, runner):
        _, outcomes, _ = _run_diffs(runner)
        creates = [o for o in outcomes if o["status"] == "success" and o["action_sub_type"] == "create"]
        assert len(creates) >= 2

    def test_skip_has_empty_action_sub_type(self, runner):
        _, outcomes, _ = _run_diffs(runner)
        skips = [o for o in outcomes if o["status"] == "skipped"]
        for s in skips:
            assert s["action_sub_type"] == "", f"Skip should have empty action_sub_type, got {s['action_sub_type']}"


class TestUpdateOutcome:
    """Test that drifted resources produce update outcomes."""

    def test_drifted_resource_reported_as_update(self, runner):
        """When dest dashboard has a different title, diffs should emit update."""
        _setup_source_dashboards()
        _setup_dest_dashboards_with_drift()

        ret = runner.invoke(
            cli,
            [
                "diffs",
                "--validate=false",
                "--verify-ddr-status=False",
                "--resources=dashboards",
                "--send-metrics=False",
                "--skip-failed-resource-connections=true",
                "--json",
            ],
        )
        outcomes = _parse_outcomes(ret.output)
        updates = [o for o in outcomes if o["status"] == "success" and o["action_sub_type"] == "update"]
        assert len(updates) == 1, f"Expected 1 update outcome, got {len(updates)}: {outcomes}"
        assert updates[0]["id"] == "abc-123"

    def test_update_coexists_with_create(self, runner):
        """Drift on abc-123 + new def-456/ghi-789 → 1 update + 2 creates."""
        _setup_source_dashboards()
        _setup_dest_dashboards_with_drift()

        ret = runner.invoke(
            cli,
            [
                "diffs",
                "--validate=false",
                "--verify-ddr-status=False",
                "--resources=dashboards",
                "--send-metrics=False",
                "--skip-failed-resource-connections=true",
                "--json",
            ],
        )
        outcomes = _parse_outcomes(ret.output)
        creates = [o for o in outcomes if o["action_sub_type"] == "create"]
        updates = [o for o in outcomes if o["action_sub_type"] == "update"]
        assert len(creates) == 2
        assert len(updates) == 1
        assert len(outcomes) == 3


class TestFilteredOutcome:
    """Test that --filter excludes resources and emits filtered status."""

    def test_filtered_resources_emitted(self, runner):
        _setup_source_dashboards()
        _setup_dest_dashboards()

        ret = runner.invoke(
            cli,
            [
                "diffs",
                "--validate=false",
                "--verify-ddr-status=False",
                "--resources=dashboards",
                "--send-metrics=False",
                "--skip-failed-resource-connections=true",
                "--json",
                "--filter=Type=dashboards;Name=id;Value=^abc-123$",
            ],
        )
        outcomes = _parse_outcomes(ret.output)
        filtered = [o for o in outcomes if o["status"] == "filtered"]
        non_filtered = [o for o in outcomes if o["status"] != "filtered"]
        # def-456 and ghi-789 should be filtered out, only abc-123 passes
        assert len(non_filtered) <= 1, f"Expected at most 1 non-filtered, got {non_filtered}"
        assert len(filtered) >= 2, f"Expected at least 2 filtered outcomes, got {filtered}"


class TestParseOutcomes:
    """Test the _parse_outcomes helper itself."""

    def test_skips_non_json_lines(self):
        output = 'some log line\n{"type": "outcome", "resource_type": "x", "status": "y"}\nanother log\n'
        outcomes = _parse_outcomes(output)
        assert len(outcomes) == 1

    def test_skips_json_without_type_outcome(self):
        output = (
            '{"foo": "bar"}\n'
            '{"type": "log", "level": "info", "message": "hi"}\n'
            '{"type": "outcome", "resource_type": "x", "status": "y"}\n'
        )
        outcomes = _parse_outcomes(output)
        assert len(outcomes) == 1

    def test_empty_output(self):
        assert _parse_outcomes("") == []
        assert _parse_outcomes("\n\n") == []
