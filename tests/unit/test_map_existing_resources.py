# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for the map_existing_resources pattern.

Test categories:
- Green/Green regression tests: pass before AND after changes (guard against regressions)
- RED infrastructure tests: fail until base class implementation is added (PR 1)
- Opt-out resource tests: verify all 32 non-mapping resources opt out correctly
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Resources that opt out of resource mapping (still have skip_resource_mapping=True)
OPT_OUT_RESOURCES = [
    "authn_mappings",
    "dashboard_lists",
    "dashboards",
    "downtime_schedules",
    "downtimes",
    "host_tags",
    "logs_archives",
    "logs_archives_order",
    "logs_custom_pipelines",
    "logs_indexes_order",
    "logs_pipelines",
    "logs_pipelines_order",
    "logs_restriction_queries",
    "metric_percentiles",
    "metrics_metadata",
    "monitors",
    "notebooks",
    "powerpacks",
    "restriction_policies",
    "rum_applications",
    "sensitive_data_scanner_groups",
    "sensitive_data_scanner_groups_order",
    "sensitive_data_scanner_rules",
    "service_level_objectives",
    "slo_corrections",
    "spans_metrics",
    "synthetics_mobile_applications",
    "synthetics_mobile_applications_versions",
    "synthetics_private_locations",
    "synthetics_test_suites",
    "synthetics_tests",
]

# The 9 resources that use resource mapping
MAPPING_RESOURCES = [
    "users",
    "teams",
    "synthetics_global_variables",
    "logs_indexes",
    "logs_metrics",
    "metric_tag_configurations",
    "roles",
    "security_monitoring_rules",
    "team_memberships",
]


def _make_resource_class(resource_config, resource_type_name="test_resource"):
    """Create a concrete BaseResource subclass for testing.

    Uses type() to avoid the Python 3.9 class-body scoping issue where
    closure variables aren't accessible inside a class definition.
    """

    async def get_resources(self, client):
        return []

    async def import_resource(self, _id=None, resource=None):
        return _id, resource

    async def pre_resource_action_hook(self, _id, resource):
        pass

    async def pre_apply_hook(self):
        pass

    async def create_resource(self, _id, resource):
        return _id, resource

    async def update_resource(self, _id, resource):
        return _id, resource

    async def delete_resource(self, _id):
        pass

    cls = type(
        "ConcreteResource",
        (BaseResource,),
        {
            "resource_type": resource_type_name,
            "resource_config": resource_config,
            "get_resources": get_resources,
            "import_resource": import_resource,
            "pre_resource_action_hook": pre_resource_action_hook,
            "pre_apply_hook": pre_apply_hook,
            "create_resource": create_resource,
            "update_resource": update_resource,
            "delete_resource": delete_resource,
        },
    )
    return cls


def _make_resource_instance(mock_config, resource_config, resource_type="test_resource"):
    """Create a concrete BaseResource subclass instance for testing."""
    cls = _make_resource_class(resource_config, resource_type)
    return cls(mock_config)


# ===========================================================================
# GREEN/GREEN REGRESSION TESTS
# These must pass on the current codebase AND after all changes.
# ===========================================================================


class TestGreenGreenNonMappingResources:
    """Verify non-mapping resources are unaffected by the changes."""

    def test_dashboards_create_resource_calls_api_directly(self, config):
        """Dashboards create_resource should POST directly without duplicate checks."""
        dashboards = config.resources["dashboards"]
        # Dashboards has no destination_* dict for duplicate checking
        assert not hasattr(dashboards, "destination_dashboards")
        assert not hasattr(dashboards, "_get_existing_dashboard")

    def test_monitors_create_resource_calls_api_directly(self, config):
        """Monitors create_resource should POST directly without duplicate checks."""
        monitors = config.resources["monitors"]
        assert not hasattr(monitors, "destination_monitors")


class TestGreenGreenPreApplyHook:
    """Verify pre_apply_hook is still called for resource types."""

    def test_pre_apply_hook_exists_on_all_resources(self, config):
        """All resources must have a pre_apply_hook method (it's abstract)."""
        for resource_type, resource in config.resources.items():
            assert hasattr(resource, "pre_apply_hook"), f"{resource_type} missing pre_apply_hook"
            assert hasattr(resource, "_pre_apply_hook"), f"{resource_type} missing _pre_apply_hook"


# ===========================================================================
# RED INFRASTRUCTURE TESTS (PR 1)
# These fail until the base class implementation is added.
# ===========================================================================


class TestGetResourceMappingKey:
    """Tests for BaseResource.get_resource_mapping_key method."""

    def test_dot_path_extracts_value(self, mock_config):
        """Dot-path 'attributes.email' should extract nested value."""
        rc = ResourceConfig(base_path="/test", resource_mapping_key="attributes.email", skip_resource_mapping=False)
        instance = _make_resource_instance(mock_config, rc)
        resource = {"attributes": {"email": "user@example.com"}}
        assert instance.get_resource_mapping_key(resource) == "user@example.com"

    def test_callable_extracts_composite_value(self, mock_config):
        """Callable key should extract composite value."""

        def key_fn(r):
            return f"{r['name']}:{r['handle']}"

        rc = ResourceConfig(base_path="/test", resource_mapping_key=key_fn, skip_resource_mapping=False)
        instance = _make_resource_instance(mock_config, rc)
        resource = {"name": "team-a", "handle": "team-a-handle"}
        assert instance.get_resource_mapping_key(resource) == "team-a:team-a-handle"

    def test_returns_none_for_missing_path(self, mock_config):
        """Missing dot-path key should return None, not 'None'."""
        rc = ResourceConfig(base_path="/test", resource_mapping_key="attributes.email", skip_resource_mapping=False)
        instance = _make_resource_instance(mock_config, rc)
        resource = {"attributes": {"name": "test"}}
        result = instance.get_resource_mapping_key(resource)
        assert result is None

    def test_returns_none_for_none_value(self, mock_config):
        """None terminal value should return None, not 'None'."""
        rc = ResourceConfig(base_path="/test", resource_mapping_key="attributes.email", skip_resource_mapping=False)
        instance = _make_resource_instance(mock_config, rc)
        resource = {"attributes": {"email": None}}
        result = instance.get_resource_mapping_key(resource)
        assert result is None

    def test_returns_none_when_unconfigured(self, mock_config):
        """resource_mapping_key=None should return None."""
        rc = ResourceConfig(base_path="/test", resource_mapping_key=None, skip_resource_mapping=True)
        instance = _make_resource_instance(mock_config, rc)
        resource = {"name": "test"}
        result = instance.get_resource_mapping_key(resource)
        assert result is None

    def test_callable_exception_returns_none(self, mock_config):
        """Callable that raises KeyError should return None."""

        def key_fn(r):
            return r["missing_key"]

        rc = ResourceConfig(base_path="/test", resource_mapping_key=key_fn, skip_resource_mapping=False)
        instance = _make_resource_instance(mock_config, rc)
        resource = {"name": "test"}
        result = instance.get_resource_mapping_key(resource)
        assert result is None

    def test_dot_path_casts_to_string(self, mock_config):
        """Numeric values should be cast to string."""
        rc = ResourceConfig(base_path="/test", resource_mapping_key="id", skip_resource_mapping=False)
        instance = _make_resource_instance(mock_config, rc)
        resource = {"id": 12345}
        result = instance.get_resource_mapping_key(resource)
        assert result == "12345"
        assert isinstance(result, str)


class TestMapExistingResources:
    """Tests for BaseResource.map_existing_resources method."""

    def test_populates_dict(self, mock_config):
        """Default implementation should build _existing_resources_map from get_resources."""
        rc = ResourceConfig(base_path="/test", resource_mapping_key="name", skip_resource_mapping=False)
        instance = _make_resource_instance(mock_config, rc)

        dest_resources = [
            {"name": "alpha", "id": "1"},
            {"name": "beta", "id": "2"},
            {"name": "gamma", "id": "3"},
        ]
        instance.get_resources = AsyncMock(return_value=dest_resources)

        asyncio.run(instance.map_existing_resources())

        assert len(instance._existing_resources_map) == 3
        assert instance._existing_resources_map["alpha"] == {"name": "alpha", "id": "1"}
        assert instance._existing_resources_map["beta"] == {"name": "beta", "id": "2"}
        assert instance._existing_resources_map["gamma"] == {"name": "gamma", "id": "3"}

    def test_skips_none_keys(self, mock_config):
        """Resources where key extraction returns None should be excluded."""
        rc = ResourceConfig(base_path="/test", resource_mapping_key="name", skip_resource_mapping=False)
        instance = _make_resource_instance(mock_config, rc)

        dest_resources = [
            {"name": "alpha", "id": "1"},
            {"name": None, "id": "2"},  # None value → excluded
            {"id": "3"},  # missing key → excluded
        ]
        instance.get_resources = AsyncMock(return_value=dest_resources)

        asyncio.run(instance.map_existing_resources())

        assert len(instance._existing_resources_map) == 1
        assert "alpha" in instance._existing_resources_map

    def test_clears_map_on_each_call(self, mock_config):
        """Calling map_existing_resources again should reset the map."""
        rc = ResourceConfig(base_path="/test", resource_mapping_key="name", skip_resource_mapping=False)
        instance = _make_resource_instance(mock_config, rc)

        # First call
        instance.get_resources = AsyncMock(return_value=[{"name": "alpha"}])
        asyncio.run(instance.map_existing_resources())
        assert len(instance._existing_resources_map) == 1

        # Second call with different data
        instance.get_resources = AsyncMock(return_value=[{"name": "beta"}, {"name": "gamma"}])
        asyncio.run(instance.map_existing_resources())
        assert len(instance._existing_resources_map) == 2
        assert "alpha" not in instance._existing_resources_map

    def test_uses_destination_client(self, mock_config):
        """map_existing_resources should call get_resources with destination_client."""
        rc = ResourceConfig(base_path="/test", resource_mapping_key="name", skip_resource_mapping=False)
        instance = _make_resource_instance(mock_config, rc)
        instance.get_resources = AsyncMock(return_value=[])

        asyncio.run(instance.map_existing_resources())

        instance.get_resources.assert_called_once_with(mock_config.destination_client)


class TestValidation:
    """Tests for ResourceConfig validation in BaseResource.__init__."""

    def test_error_when_no_key_defined(self, mock_config):
        """skip_resource_mapping=False + resource_mapping_key=None should raise ValueError."""
        rc = ResourceConfig(base_path="/test", resource_mapping_key=None, skip_resource_mapping=False)
        with pytest.raises(ValueError, match="resource_mapping_key is not defined"):
            _make_resource_instance(mock_config, rc)

    def test_no_error_when_opted_out(self, mock_config):
        """skip_resource_mapping=True + resource_mapping_key=None should NOT raise."""
        rc = ResourceConfig(base_path="/test", resource_mapping_key=None, skip_resource_mapping=True)
        instance = _make_resource_instance(mock_config, rc)
        assert instance._existing_resources_map == {}

    def test_no_error_when_key_defined(self, mock_config):
        """skip_resource_mapping=False + resource_mapping_key set should NOT raise."""
        rc = ResourceConfig(base_path="/test", resource_mapping_key="name", skip_resource_mapping=False)
        instance = _make_resource_instance(mock_config, rc)
        assert instance._existing_resources_map == {}


class TestMapExistingResourcesCb:
    """Tests for the orchestrator _map_existing_resources_cb callback."""

    def test_calls_map_for_non_skipped_resource(self, mock_config):
        """Callback should call map_existing_resources() for non-skipped resources."""
        from datadog_sync.utils.resources_handler import ResourcesHandler

        handler = ResourcesHandler(mock_config)

        rc = ResourceConfig(base_path="/test", resource_mapping_key="name", skip_resource_mapping=False)
        resource = _make_resource_instance(mock_config, rc, resource_type="test_type")
        resource.map_existing_resources = AsyncMock()
        mock_config.resources = {"test_type": resource}

        asyncio.run(handler._map_existing_resources_cb("test_type"))

        resource.map_existing_resources.assert_called_once()

    def test_skips_opted_out_resource(self, mock_config):
        """Callback should NOT call map_existing_resources() for opted-out resources."""
        from datadog_sync.utils.resources_handler import ResourcesHandler

        handler = ResourcesHandler(mock_config)

        rc = ResourceConfig(base_path="/test", resource_mapping_key=None, skip_resource_mapping=True)
        resource = _make_resource_instance(mock_config, rc, resource_type="test_type")
        resource.map_existing_resources = AsyncMock()
        mock_config.resources = {"test_type": resource}

        asyncio.run(handler._map_existing_resources_cb("test_type"))

        resource.map_existing_resources.assert_not_called()

    def test_logs_and_propagates_error_on_failure(self, mock_config):
        """Callback should log error AND propagate exceptions from map_existing_resources."""
        from datadog_sync.utils.resources_handler import ResourcesHandler

        handler = ResourcesHandler(mock_config)

        rc = ResourceConfig(base_path="/test", resource_mapping_key="name", skip_resource_mapping=False)
        resource = _make_resource_instance(mock_config, rc, resource_type="test_type")
        resource.map_existing_resources = AsyncMock(side_effect=Exception("connection failed"))
        mock_config.resources = {"test_type": resource}

        with pytest.raises(Exception, match="connection failed"):
            asyncio.run(handler._map_existing_resources_cb("test_type"))

        mock_config.logger.error.assert_called_once()
        assert "connection failed" in str(mock_config.logger.error.call_args)


# ===========================================================================
# OPT-OUT RESOURCE TESTS
# ===========================================================================


class TestOptOutResources:
    """Verify non-mapping resources have skip_resource_mapping=True."""

    @pytest.mark.parametrize("resource_type", OPT_OUT_RESOURCES)
    def test_opt_out_resource_has_skip_flag(self, config, resource_type):
        """Each opt-out resource must have skip_resource_mapping=True."""
        resource = config.resources[resource_type]
        assert (
            resource.resource_config.skip_resource_mapping is True
        ), f"{resource_type} should have skip_resource_mapping=True"


class TestMappingResources:
    """Verify mapping resources have resource_mapping_key set."""

    @pytest.mark.parametrize("resource_type", MAPPING_RESOURCES)
    def test_mapping_resource_has_key(self, config, resource_type):
        """Each mapping resource must have resource_mapping_key configured."""
        resource = config.resources[resource_type]
        assert (
            resource.resource_config.resource_mapping_key is not None
        ), f"{resource_type} should have resource_mapping_key set"

    @pytest.mark.parametrize("resource_type", MAPPING_RESOURCES)
    def test_mapping_resource_has_skip_false(self, config, resource_type):
        """Each mapping resource must have skip_resource_mapping=False."""
        resource = config.resources[resource_type]
        assert (
            resource.resource_config.skip_resource_mapping is False
        ), f"{resource_type} should have skip_resource_mapping=False"
