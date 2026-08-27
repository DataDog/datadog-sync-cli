# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.dashboard_lists import DashboardLists
from datadog_sync.utils.resource_utils import CustomClientHTTPError, ResourceConnectionError


def _make_dashboard_lists() -> DashboardLists:
    config = MagicMock()
    config.state = MagicMock()
    config.state.destination = defaultdict(dict)
    config.state.source = defaultdict(dict)
    config.skip_failed_resource_connections = False
    config.logger = MagicMock()
    return DashboardLists(config)


def test_connect_resources_maps_custom_dashboards_and_keeps_integration_dashboards():
    dashboard_lists = _make_dashboard_lists()
    dashboard_lists.config.state.destination["dashboards"]["dash-src"] = {"id": "dash-dst"}
    resource = {
        "id": 510887,
        "dashboards": [
            {"id": "dash-src", "type": "custom_timeboard"},
            {"id": "62", "type": "integration_timeboard"},
        ],
    }

    dashboard_lists.connect_resources("510887", resource)

    assert resource["dashboards"] == [
        {"id": "dash-dst", "type": "custom_timeboard"},
        {"id": "62", "type": "integration_timeboard"},
    ]


def test_extract_source_ids_ignores_integration_dashboards():
    dashboard_lists = _make_dashboard_lists()

    assert (
        dashboard_lists.extract_source_ids(
            "id",
            {"id": "62", "type": "integration_timeboard"},
            "dashboards",
        )
        is None
    )


def test_extract_source_ids_keeps_custom_dashboards():
    dashboard_lists = _make_dashboard_lists()

    assert dashboard_lists.extract_source_ids(
        "id",
        {"id": "dash-src", "type": "custom_timeboard"},
        "dashboards",
    ) == ["dash-src"]


def test_connect_resources_still_fails_missing_custom_dashboards():
    dashboard_lists = _make_dashboard_lists()
    resource = {
        "id": 510887,
        "dashboards": [{"id": "dash-src", "type": "custom_timeboard"}],
    }

    with pytest.raises(ResourceConnectionError):
        dashboard_lists.connect_resources("510887", resource)


def _http_error(status: int) -> CustomClientHTTPError:
    resp = MagicMock()
    resp.status = status
    resp.message = "Error"
    return CustomClientHTTPError(resp, message=f"{status} Internal Server Error")


class TestDashboardListsItemsFetchError:
    """The secondary fetch of dashboard-list items (GET
    /api/v2/dashboard/lists/manual/{id}/dashboards) must propagate
    CustomClientHTTPError so the per-resource import worker counts it as a
    failure and writes no incomplete source state.

    Swallowing the error and persisting ``dashboards=[]`` would let a later
    sync (or migrate) interpret a transient read failure as an intentionally
    empty list and clear destination membership — destructive data loss
    from a transient API error.  Re-raising ensures the resource is retried
    on the next import run with no partial state written.
    """

    def test_500_on_items_fetch_propagates(self):
        """A 500 on the items endpoint must propagate as
        CustomClientHTTPError, not be swallowed.  The per-resource worker
        classifies it as http_5xx (transient) — counted as failure, logged
        at WARNING, no exit-code poisoning, no incomplete state written."""
        dashboard_lists = _make_dashboard_lists()
        dashboard_lists.config.source_client.get = AsyncMock(side_effect=_http_error(500))

        with pytest.raises(CustomClientHTTPError):
            asyncio.run(dashboard_lists.import_resource(resource={"id": "42", "name": "my-list"}))

    def test_503_on_items_fetch_propagates(self):
        """Any 5xx propagates — same treatment as 500."""
        dashboard_lists = _make_dashboard_lists()
        dashboard_lists.config.source_client.get = AsyncMock(side_effect=_http_error(503))

        with pytest.raises(CustomClientHTTPError):
            asyncio.run(dashboard_lists.import_resource(resource={"id": "42", "name": "my-list"}))

    def test_items_fetch_success_populates_dashboards(self):
        """Happy path: items endpoint returns dashboard IDs that are
        attached to the list resource."""
        dashboard_lists = _make_dashboard_lists()

        async def fake_get(path, **kwargs):
            if path == dashboard_lists.dash_list_items_path.format("42"):
                return {"dashboards": [{"id": "dash-1", "type": "custom_timeboard"}]}
            return {"id": "42", "name": "my-list"}

        dashboard_lists.config.source_client.get = AsyncMock(side_effect=fake_get)

        _id, resource = asyncio.run(dashboard_lists.import_resource(resource={"id": "42", "name": "my-list"}))

        assert resource["dashboards"] == [{"id": "dash-1", "type": "custom_timeboard"}]
