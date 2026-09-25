# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Unit tests for the observability_pipelines resource model."""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.observability_pipelines import ObservabilityPipelines
from datadog_sync.utils.configuration import init_resources
from datadog_sync.utils.resource_utils import prep_resource


def _make_observability_pipelines() -> ObservabilityPipelines:
    config = MagicMock()
    config.state = MagicMock()
    config.state.destination = defaultdict(dict)
    config.state.source = defaultdict(dict)
    config.skip_failed_resource_connections = False
    config.logger = MagicMock()
    return ObservabilityPipelines(config)


class TestObservabilityPipelinesRegistration:
    def test_resource_type(self):
        assert ObservabilityPipelines.resource_type == "observability_pipelines"

    def test_config_contract(self):
        rc = ObservabilityPipelines.resource_config
        assert rc.base_path == "/api/v2/obs-pipelines/pipelines"
        assert rc.resource_mapping_key == "id"
        assert rc.excluded_attributes == ["root['id']"]
        assert rc.skip_resource_mapping is False

    def test_registered_in_init_resources(self):
        config = MagicMock()
        resources = init_resources(config)
        assert "observability_pipelines" in resources
        assert isinstance(resources["observability_pipelines"], ObservabilityPipelines)


class TestObservabilityPipelinesGetResources:
    def test_get_resources_uses_paginated_request(self):
        op = _make_observability_pipelines()
        client = MagicMock()
        expected = [{"id": "pipe-src-uuid", "type": "pipelines"}, {"id": "pipe-src-2", "type": "pipelines"}]
        inner = AsyncMock(return_value=expected)
        client.paginated_request = MagicMock(return_value=inner)

        result = asyncio.run(op.get_resources(client))

        assert result == expected
        client.paginated_request.assert_called_once()
        args, _ = client.paginated_request.call_args
        assert args[0] == client.get
        inner.assert_awaited_once_with("/api/v2/obs-pipelines/pipelines")


class TestObservabilityPipelinesImportResource:
    def test_import_resource_passthrough(self):
        op = _make_observability_pipelines()
        resource = {
            "id": "pipe-src-uuid",
            "type": "pipelines",
            "attributes": {"name": "test-pipeline", "config": {"sources": [], "destinations": []}},
        }

        _id, result = asyncio.run(op.import_resource(resource=resource))

        assert _id == "pipe-src-uuid"
        assert result == resource

    def test_import_resource_by_id_does_get(self):
        op = _make_observability_pipelines()
        resource = {
            "id": "pipe-src-uuid",
            "type": "pipelines",
            "attributes": {"name": "test-pipeline", "config": {"sources": [], "destinations": []}},
        }
        op.config.source_client = AsyncMock()
        op.config.source_client.get.return_value = {"data": resource}

        _id, result = asyncio.run(op.import_resource(_id="pipe-src-uuid"))

        assert _id == "pipe-src-uuid"
        assert result == resource
        op.config.source_client.get.assert_awaited_once()
        call_path = op.config.source_client.get.call_args[0][0]
        assert call_path == "/api/v2/obs-pipelines/pipelines/pipe-src-uuid"


class TestObservabilityPipelinesCreateResource:
    def test_create_when_id_absent_posts(self):
        op = _make_observability_pipelines()
        op._existing_resources_map = {}
        resource = {
            "type": "pipelines",
            "attributes": {"name": "test-pipeline", "config": {"sources": [], "destinations": []}},
        }
        resp_data = {"id": "pipe-dst-uuid", "type": "pipelines", "attributes": resource["attributes"]}
        op.config.destination_client = AsyncMock()
        op.config.destination_client.post.return_value = {"data": resp_data}

        _id, result = asyncio.run(op.create_resource("pipe-src-uuid", resource))

        assert _id == "pipe-src-uuid"
        assert result == resp_data
        op.config.destination_client.post.assert_awaited_once()
        args, _ = op.config.destination_client.post.call_args
        assert args[0] == "/api/v2/obs-pipelines/pipelines"
        assert args[1] == {"data": resource}
        assert "id" not in resource

    def test_create_when_id_present_delegates_to_update(self):
        op = _make_observability_pipelines()
        existing = {"id": "pipe-dst-uuid", "type": "pipelines", "attributes": {"name": "test-pipeline"}}
        op._existing_resources_map = {"pipe-src-uuid": existing}
        resource = {
            "type": "pipelines",
            "attributes": {"name": "test-pipeline", "config": {"sources": [], "destinations": []}},
        }
        op.config.destination_client = AsyncMock()
        op.update_resource = AsyncMock(return_value=("pipe-src-uuid", existing))

        _id, result = asyncio.run(op.create_resource("pipe-src-uuid", resource))

        assert _id == "pipe-src-uuid"
        assert result == existing
        assert op.config.state.destination["observability_pipelines"]["pipe-src-uuid"] == existing
        op.update_resource.assert_awaited_once_with("pipe-src-uuid", resource)


class TestObservabilityPipelinesUpdateResource:
    def test_update_puts_with_destination_id(self):
        op = _make_observability_pipelines()
        op.config.state.destination["observability_pipelines"]["pipe-src-uuid"] = {
            "id": "pipe-dst-uuid",
            "type": "pipelines",
            "attributes": {"name": "test-pipeline"},
        }
        resource = {
            "type": "pipelines",
            "attributes": {"name": "test-pipeline", "config": {"sources": [], "destinations": []}},
        }
        resp_data = {"id": "pipe-dst-uuid", "type": "pipelines", "attributes": resource["attributes"]}
        op.config.destination_client = AsyncMock()
        op.config.destination_client.put.return_value = {"data": resp_data}

        _id, result = asyncio.run(op.update_resource("pipe-src-uuid", resource))

        assert _id == "pipe-src-uuid"
        assert result == resp_data
        assert resource["id"] == "pipe-dst-uuid"
        op.config.destination_client.put.assert_awaited_once()
        args, _ = op.config.destination_client.put.call_args
        assert args[0] == "/api/v2/obs-pipelines/pipelines/pipe-dst-uuid"
        assert args[1] == {"data": resource}


class TestObservabilityPipelinesDeleteResource:
    def test_delete_uses_destination_id(self):
        op = _make_observability_pipelines()
        op.config.state.destination["observability_pipelines"]["pipe-src-uuid"] = {"id": "pipe-dst-uuid"}
        op.config.destination_client = AsyncMock()

        asyncio.run(op.delete_resource("pipe-src-uuid"))

        op.config.destination_client.delete.assert_awaited_once()
        call_path = op.config.destination_client.delete.call_args[0][0]
        assert call_path == "/api/v2/obs-pipelines/pipelines/pipe-dst-uuid"


class TestObservabilityPipelinesPrepResource:
    def test_prep_resource_strips_id(self):
        resource = {
            "id": "pipe-src-uuid",
            "type": "pipelines",
            "attributes": {
                "name": "test-pipeline",
                "config": {"sources": [], "destinations": []},
            },
        }

        prep_resource(ObservabilityPipelines.resource_config, resource)

        assert "id" not in resource
        assert resource["attributes"]["name"] == "test-pipeline"
        assert resource["attributes"]["config"] == {"sources": [], "destinations": []}


class TestObservabilityPipelinesHooks:
    def test_pre_hooks_are_noops(self):
        op = _make_observability_pipelines()

        asyncio.run(op.pre_resource_action_hook("pipe-src-uuid", {}))
        result = asyncio.run(op.pre_apply_hook())

        assert result is None
