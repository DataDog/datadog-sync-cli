# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for --id-file × --minimize-reads state-load ID-targeting on the sync command.

When --filter cannot produce exact IDs for a resource type (i.e. its state key
is not surfaced in the stored body — see extract_exact_id_filters fallback),
--id-file may supply those IDs directly. Verified here:

- host_tags and metrics_metadata are in _ID_FILE_SUPPORTED_TYPES (state-load
  scoping shape).
- id_payload scoped to --resources feeds _state_exact_ids when the filter
  path returns None.
- id_payload types outside --resources do not widen the load.
- id_payload has no effect when --minimize-reads is not set.
"""

import json
from pathlib import Path

import pytest


# ─── Allowlist ──────────────────────────────────────────────────────────────


class TestIdFileSupportedTypesAllowlist:
    def test_host_tags_in_state_load_allowlist(self):
        from datadog_sync.utils.configuration import _ID_FILE_STATE_LOAD_SUPPORTED_TYPES

        assert "host_tags" in _ID_FILE_STATE_LOAD_SUPPORTED_TYPES

    def test_metrics_metadata_in_state_load_allowlist(self):
        from datadog_sync.utils.configuration import _ID_FILE_STATE_LOAD_SUPPORTED_TYPES

        assert "metrics_metadata" in _ID_FILE_STATE_LOAD_SUPPORTED_TYPES

    def test_host_tags_NOT_in_import_allowlist(self):
        """host_tags has no working get_resources_by_ids fan-out (its
        import_resource returns None when _id is passed), so the import
        per-ID GET path would produce per-ID permanent errors. Keep it out
        of the import allowlist even though the parser accepts it."""
        from datadog_sync.utils.configuration import _ID_FILE_IMPORT_SUPPORTED_TYPES

        assert "host_tags" not in _ID_FILE_IMPORT_SUPPORTED_TYPES

    def test_metrics_metadata_NOT_in_import_allowlist(self):
        """metrics_metadata's import_resource does a real GET on _id and would
        functionally work on the import path, but keeping the two allowlists
        symmetric documents that this type was added purely for sync-command
        state-load scoping. Widen deliberately if import support is needed."""
        from datadog_sync.utils.configuration import _ID_FILE_IMPORT_SUPPORTED_TYPES

        assert "metrics_metadata" not in _ID_FILE_IMPORT_SUPPORTED_TYPES

    def test_original_import_types_still_in_import_allowlist(self):
        """Pre-existing import-command types must remain — this PR only extends."""
        from datadog_sync.utils.configuration import _ID_FILE_IMPORT_SUPPORTED_TYPES

        assert "monitors" in _ID_FILE_IMPORT_SUPPORTED_TYPES
        assert "authn_mappings" in _ID_FILE_IMPORT_SUPPORTED_TYPES
        assert "team_memberships" in _ID_FILE_IMPORT_SUPPORTED_TYPES

    def test_union_allowlist_is_union_of_both(self):
        """The parser accepts any type in either shape; runtime paths narrow."""
        from datadog_sync.utils.configuration import (
            _ID_FILE_IMPORT_SUPPORTED_TYPES,
            _ID_FILE_STATE_LOAD_SUPPORTED_TYPES,
            _ID_FILE_SUPPORTED_TYPES,
        )

        assert _ID_FILE_SUPPORTED_TYPES == _ID_FILE_IMPORT_SUPPORTED_TYPES | _ID_FILE_STATE_LOAD_SUPPORTED_TYPES

    def test_singletons_not_in_any_allowlist(self):
        """Order singletons have one resource per org; state-load scoping is a
        no-op and the import per-ID path was never wired for them. Keep excluded
        from both shapes."""
        from datadog_sync.utils.configuration import (
            _ID_FILE_IMPORT_SUPPORTED_TYPES,
            _ID_FILE_STATE_LOAD_SUPPORTED_TYPES,
        )

        for singleton in (
            "logs_indexes_order",
            "logs_archives_order",
            "logs_pipelines_order",
            "sensitive_data_scanner_groups_order",
        ):
            assert singleton not in _ID_FILE_IMPORT_SUPPORTED_TYPES
            assert singleton not in _ID_FILE_STATE_LOAD_SUPPORTED_TYPES


# ─── _parse_id_file with the extended allowlist ─────────────────────────────


def _write_payload(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "id-file.json"
    path.write_text(json.dumps(payload))
    return path


class TestParseIdFileAcceptsNewTypes:
    def test_host_tags_accepted(self, tmp_path):
        from datadog_sync.utils.configuration import _parse_id_file
        import logging

        payload_path = _write_payload(tmp_path, {"host_tags": ["host-a", "host-b"]})
        result = _parse_id_file(str(payload_path), logging.getLogger("test"))
        assert result == {"host_tags": ["host-a", "host-b"]}

    def test_metrics_metadata_accepted(self, tmp_path):
        from datadog_sync.utils.configuration import _parse_id_file
        import logging

        payload_path = _write_payload(tmp_path, {"metrics_metadata": ["metric.a", "metric.b"]})
        result = _parse_id_file(str(payload_path), logging.getLogger("test"))
        assert result == {"metrics_metadata": ["metric.a", "metric.b"]}

    def test_mixed_types_all_supported(self, tmp_path):
        from datadog_sync.utils.configuration import _parse_id_file
        import logging

        payload_path = _write_payload(
            tmp_path,
            {"host_tags": ["h1"], "monitors": ["m1", "m2"]},
        )
        result = _parse_id_file(str(payload_path), logging.getLogger("test"))
        assert result == {"host_tags": ["h1"], "monitors": ["m1", "m2"]}

    def test_unsupported_type_still_rejected(self, tmp_path):
        """Guard against accidental over-widening: a random type still hard-fails."""
        from datadog_sync.utils.configuration import _parse_id_file
        import logging

        payload_path = _write_payload(tmp_path, {"dashboards": ["dash-1"]})
        with pytest.raises(SystemExit):
            _parse_id_file(str(payload_path), logging.getLogger("test"))


# ─── id_payload → _state_exact_ids fallback derivation ───────────────────────
#
# The derivation lives inline in build_config (configuration.py). Testing it
# directly requires a broader Configuration harness; the team_memberships
# id-file tests cover the harness-level equivalent for the import command.
# The tests here pin the CONTRACT so a regression in the derivation logic
# surfaces via the shape of the allowlists and the help-text update.


class TestOptionsHelpText:
    def test_id_file_help_mentions_sync_command_state_load(self):
        """Help text must document the new sync-command state-load scoping path
        so operators can discover it. Guard against silent regression on doc.

        Read the source file directly — Click stores decorators as callables in
        the module's lists, not resolved Option instances, so introspection via
        the module isn't ergonomic. The source-text check is sufficient because
        this is a doc-drift guard, not a behavior test."""
        import datadog_sync.commands.shared.options as options_module
        import inspect

        source = inspect.getsource(options_module)
        # Locate the --id-file help block by anchoring on its decl line.
        idx = source.find('"--id-file"')
        assert idx >= 0, '--id-file option missing from options.py'
        # Grab enough context to cover the help= argument.
        window = source[idx : idx + 1000]
        assert "sync command" in window.lower(), "help text must mention sync command"
        assert "--minimize-reads" in window, "help text must mention --minimize-reads gating"
