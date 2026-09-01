# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for dashboards support in _ID_FILE_IMPORT_SUPPORTED_TYPES.

Pins that 'dashboards' is accepted by --id-file and that the per-ID GET
path used by get_resources_by_ids (import --id-file) does exactly one GET
per dashboard, with the prefetched-body short-circuit preventing a
double-fetch on queue-handler re-entry.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.dashboards import Dashboards
from datadog_sync.utils.configuration import (
    _ID_FILE_IMPORT_SUPPORTED_TYPES,
    _ID_FILE_SUPPORTED_TYPES,
)
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource


class TestDashboardsIDFileSupport:
    """Tests for dashboards support in the --id-file allowlist."""

    def test_dashboards_in_id_file_import_supported_types(self):
        """'dashboards' must be present in _ID_FILE_IMPORT_SUPPORTED_TYPES so
        that `import --id-file=- < {"dashboards": [...]}` is accepted."""
        assert "dashboards" in _ID_FILE_IMPORT_SUPPORTED_TYPES, (
            "dashboards must be in _ID_FILE_IMPORT_SUPPORTED_TYPES. "
            "If this fails, id-file import support for dashboards is missing."
        )

    def test_dashboards_in_id_file_supported_types_union(self):
        """'dashboards' must also be in the union allowlist consulted by
        _parse_id_file (rejects unknown types up-front)."""
        assert "dashboards" in _ID_FILE_SUPPORTED_TYPES

    def test_dashboards_in_state_load_supported_types(self):
        """Adding dashboards to the import allowlist also adds it to the
        union (_ID_FILE_SUPPORTED_TYPES), which _parse_id_file consults for
        *both* import and sync --minimize-reads. Since the state-load path
        scopes by --resources intersection rather than by
        _ID_FILE_STATE_LOAD_SUPPORTED_TYPES, dashboards must be explicitly
        in the state-load set too — otherwise it's accepted incidentally via
        the union without the ID-derivability verification the set exists to
        enforce. Dashboards' state key is dashboards.<id>.json (ID-derivable),
        so it qualifies."""
        from datadog_sync.utils.configuration import _ID_FILE_STATE_LOAD_SUPPORTED_TYPES

        assert "dashboards" in _ID_FILE_STATE_LOAD_SUPPORTED_TYPES, (
            "dashboards is in _ID_FILE_IMPORT_SUPPORTED_TYPES, so the union "
            "accepts it on the sync state-load path too; it must be explicitly "
            "in _ID_FILE_STATE_LOAD_SUPPORTED_TYPES (state key is ID-derivable)."
        )

    def test_import_resource_id_does_real_get(self):
        """import_resource(_id=...) performs a GET to /api/v1/dashboard/{id}
        and returns the body — the per-ID fan-out path used by
        get_resources_by_ids on id-file import runs."""
        mock_config = MagicMock()
        mock_client = AsyncMock()
        body = {
            "id": "abc-def-ghi",
            "title": "test-dashboard",
            "widgets": [{"definition": {"type": "timeseries"}}],
        }
        mock_client.get.return_value = body
        mock_config.source_client = mock_client
        dashboards = Dashboards(mock_config)

        _id, resource = asyncio.run(dashboards.import_resource(_id="abc-def-ghi"))

        mock_client.get.assert_awaited_once()
        call_path = mock_client.get.call_args[0][0]
        assert call_path == "/api/v1/dashboard/abc-def-ghi", (
            f"import_resource(_id=...) must GET /api/v1/dashboard/{{id}}; got {call_path!r}"
        )
        assert _id == "abc-def-ghi"
        assert resource == body

    def test_import_resource_id_short_circuits_when_caller_supplies_full_body(self):
        """When the queue handler re-enters import_resource with the body
        already fetched by get_resources_by_ids (detected by 'widgets'
        presence), the model must NOT issue a second GET — otherwise id-file
        import doubles rate-limit pressure on /api/v1/dashboard/{id}."""
        mock_config = MagicMock()
        mock_client = AsyncMock()
        mock_config.source_client = mock_client
        dashboards = Dashboards(mock_config)
        full_body = {
            "id": "abc-def-ghi",
            "title": "prefetched",
            "widgets": [{"definition": {"type": "timeseries"}}],
        }

        _id, resource = asyncio.run(dashboards.import_resource(resource=full_body))

        mock_client.get.assert_not_awaited()
        assert _id == "abc-def-ghi"
        assert resource == full_body

    def test_import_resource_id_403_raises_skip_resource(self):
        """A 403 on the per-ID GET raises SkipResource so the id-file path
        buckets it as 'skipped' (matching get_resources_by_ids' handling)
        rather than aborting the whole import."""
        mock_config = MagicMock()
        mock_client = AsyncMock()
        resp = MagicMock()
        resp.status = 403
        mock_client.get.side_effect = CustomClientHTTPError(resp, message="Forbidden")
        mock_config.source_client = mock_client
        dashboards = Dashboards(mock_config)

        with pytest.raises(SkipResource):
            asyncio.run(dashboards.import_resource(_id="abc-def-ghi"))

    def test_import_resource_id_propagates_non_403_errors(self):
        """A 500 on the per-ID GET propagates (get_resources_by_ids classifies
        5xx as transient and retries; the model must not swallow it)."""
        mock_config = MagicMock()
        mock_client = AsyncMock()
        resp = MagicMock()
        resp.status = 500
        mock_client.get.side_effect = CustomClientHTTPError(resp, message="boom")
        mock_config.source_client = mock_client
        dashboards = Dashboards(mock_config)

        with pytest.raises(CustomClientHTTPError):
            asyncio.run(dashboards.import_resource(_id="abc-def-ghi"))

    def test_import_resource_id_without_explicit_id_uses_resource_id(self):
        """import_resource() with only a resource dict derives the id from
        resource['id'] (the legacy list-path shape) — pins that both call
        shapes produce a consistent _id."""
        mock_config = MagicMock()
        mock_client = AsyncMock()
        body = {"id": "abc-def-ghi", "title": "t", "widgets": []}
        mock_client.get.return_value = body
        mock_config.source_client = mock_client
        dashboards = Dashboards(mock_config)

        _id, _ = asyncio.run(dashboards.import_resource(_id=None, resource={"id": "abc-def-ghi"}))

        assert _id == "abc-def-ghi"
