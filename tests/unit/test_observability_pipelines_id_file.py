# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).

"""Tests for observability_pipelines support in the --id-file allowlist."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.observability_pipelines import ObservabilityPipelines
from datadog_sync.utils.configuration import (
    _ID_FILE_IMPORT_SUPPORTED_TYPES,
    _ID_FILE_SUPPORTED_TYPES,
)


class TestObservabilityPipelinesIDFileSupport:
    """Tests for observability_pipelines support in _ID_FILE_SUPPORTED_TYPES."""

    def test_observability_pipelines_in_id_file_import_supported_types(self):
        """'observability_pipelines' must be present in _ID_FILE_IMPORT_SUPPORTED_TYPES
        so that `import --id-file=- < {"observability_pipelines": [...]}` is accepted."""
        assert "observability_pipelines" in _ID_FILE_IMPORT_SUPPORTED_TYPES, (
            "observability_pipelines must be in _ID_FILE_IMPORT_SUPPORTED_TYPES. "
            "If this fails, id-file import support for observability_pipelines is missing."
        )

    def test_observability_pipelines_in_id_file_supported_types_union(self):
        """'observability_pipelines' must also be in the union allowlist consulted by
        _parse_id_file (rejects unknown types up-front)."""
        assert "observability_pipelines" in _ID_FILE_SUPPORTED_TYPES

    def test_import_resource_id_does_real_get(self):
        """import_resource(_id=...) performs a GET to the OP pipelines API and
        returns the body — the per-ID fan-out path used by get_resources_by_ids
        on id-file import runs."""
        mock_config = MagicMock()
        mock_client = AsyncMock()
        body = {
            "id": "pipe-src-uuid",
            "type": "pipelines",
            "attributes": {"name": "test-pipeline", "config": {"sources": [], "destinations": []}},
        }
        mock_client.get.return_value = {"data": body}
        mock_config.source_client = mock_client
        op = ObservabilityPipelines(mock_config)

        _id, resource = asyncio.run(op.import_resource(_id="pipe-src-uuid"))

        mock_client.get.assert_awaited_once()
        call_path = mock_client.get.call_args[0][0]
        assert (
            call_path == "/api/v2/obs-pipelines/pipelines/pipe-src-uuid"
        ), f"import_resource(_id=...) must GET /api/v2/obs-pipelines/pipelines/{{id}}; got {call_path!r}"
        assert _id == "pipe-src-uuid"
        assert resource == body

    def test_import_resource_id_succeeds_for_valid_uuid(self):
        """import_resource(pipeline_uuid) (1-arg) completes without error for a
        well-formed UUID."""
        mock_config = MagicMock()
        pipeline = {
            "id": "423368a4-956a-11ef-b92a-da7ad0900005",
            "type": "pipelines",
            "attributes": {"name": "test-pipeline", "config": {"sources": [], "destinations": []}},
        }
        mock_config.source_client = AsyncMock()
        mock_config.source_client.get.return_value = {"data": pipeline}
        op = ObservabilityPipelines(mock_config)
        _id, _ = asyncio.run(op.import_resource(_id=pipeline["id"]))
        assert _id == pipeline["id"]

    def test_import_resource_id_api_error_propagates(self):
        """import_resource(uuid) propagates HTTP errors from the upstream GET."""
        mock_config = MagicMock()
        mock_config.source_client = AsyncMock()
        mock_config.source_client.get.side_effect = Exception("HTTP 404")
        op = ObservabilityPipelines(mock_config)
        with pytest.raises(Exception, match="404"):
            asyncio.run(op.import_resource(_id="nonexistent-uuid"))
