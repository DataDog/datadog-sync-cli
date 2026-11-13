# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import asyncio
import copy
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.dashboard_lists import DashboardLists
from datadog_sync.utils.resource_utils import (
    CustomClientHTTPError,
    ResourceConnectionError,
)


def _make_dashboard_lists() -> DashboardLists:
    config = MagicMock()
    config.state = MagicMock()
    config.state.destination = defaultdict(dict)
    config.state.source = defaultdict(dict)
    config.skip_failed_resource_connections = False
    config.logger = MagicMock()
    return DashboardLists(config)


class _SignatureCheckedLogger:
    def __init__(self):
        self.info_calls = []

    def info(self, msg: str, *arg, _id: str = "", resource_type: str = "") -> None:
        self.info_calls.append({"msg": msg, "args": arg, "_id": _id, "resource_type": resource_type})


def test_pre_resource_action_hook_drops_integration_dashboards_before_apply():
    dashboard_lists = _make_dashboard_lists()
    dashboard_lists.config.state.destination["dashboards"]["dash-src"] = {"id": "dash-dst"}
    resource = {
        "id": 510887,
        "dashboards": [
            {"id": "dash-src", "type": "custom_timeboard"},
            {"id": "62", "type": "integration_timeboard"},
            {"id": "30516", "type": "integration_timeboard"},
        ],
    }

    asyncio.run(dashboard_lists.pre_resource_action_hook("510887", resource))
    dashboard_lists.connect_resources("510887", resource)

    assert resource["dashboards"] == [
        {"id": "dash-dst", "type": "custom_timeboard"},
    ]


def test_drop_integration_dashboards_uses_supported_logger_kwargs():
    dashboard_lists = _make_dashboard_lists()
    logger = _SignatureCheckedLogger()
    dashboard_lists.config.logger = logger
    resource = {
        "id": 510887,
        "dashboards": [
            {"id": "dash-src", "type": "custom_timeboard"},
            {"id": "62", "type": "integration_timeboard"},
            {"id": "30516", "type": "integration_timeboard"},
        ],
    }

    asyncio.run(dashboard_lists.pre_resource_action_hook("510887", resource))

    assert resource["dashboards"] == [{"id": "dash-src", "type": "custom_timeboard"}]
    assert logger.info_calls == [
        {
            "msg": "dropping integration dashboards from dashboard list before sync; "
            "integration dashboard IDs are not portable across orgs; "
            "dropped_dashboard_ids=30516,62",
            "args": (),
            "_id": "510887",
            "resource_type": "dashboard_lists",
        }
    ]


def test_connect_resources_ignores_unmapped_integration_dashboards():
    dashboard_lists = _make_dashboard_lists()
    dashboard_lists.config.state.destination["dashboards"]["dash-src"] = {"id": "dash-dst"}
    resource = {
        "id": 510887,
        "dashboards": [
            {"id": "dash-src", "type": "custom_timeboard"},
            {"id": "30516", "type": "integration_timeboard"},
        ],
    }

    dashboard_lists.connect_resources("510887", resource)

    assert resource["dashboards"] == [
        {"id": "dash-dst", "type": "custom_timeboard"},
        {"id": "30516", "type": "integration_timeboard"},
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


def test_update_dash_list_items_drops_integration_dashboards_from_payload():
    dashboard_lists = _make_dashboard_lists()
    dashboard_lists.config.state.destination["dashboards"]["dash-src"] = {"id": "dash-dst"}
    dashboard_lists.config.destination_client.put = AsyncMock(
        return_value={"dashboards": [{"id": "dash-dst", "type": "custom_timeboard"}]}
    )
    dashboard_list = {}

    asyncio.run(
        dashboard_lists.update_dash_list_items(
            "dst-list",
            [
                {"id": "dash-dst", "type": "custom_timeboard"},
                {"id": "30516", "type": "integration_timeboard"},
            ],
            dashboard_list,
        )
    )

    dashboard_lists.config.destination_client.put.assert_awaited_once_with(
        dashboard_lists.dash_list_items_path.format("dst-list"),
        {"dashboards": [{"id": "dash-dst", "type": "custom_timeboard"}]},
    )
    assert dashboard_list == {"dashboards": [{"id": "dash-dst", "type": "custom_timeboard"}]}


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


def _not_found_error(message: str) -> CustomClientHTTPError:
    resp = MagicMock()
    resp.status = 404
    resp.message = "Not Found"
    return CustomClientHTTPError(resp, message=message)


async def _sync_once(dashboard_lists: DashboardLists, _id: str, source_resource: dict) -> None:
    """Mirror the create-vs-update dispatch in ResourcesHandler._apply_resource_cb."""
    resource = copy.deepcopy(source_resource)
    dashboard_lists.connect_resources(_id, resource)
    if _id in dashboard_lists.config.state.destination[dashboard_lists.resource_type]:
        await dashboard_lists._update_resource(_id, resource)
    else:
        await dashboard_lists._create_resource(_id, resource)


class TestDashboardListsCreateRecordsState:
    def test_members_put_failure_keeps_list_in_state(self):
        dashboard_lists = _make_dashboard_lists()
        dashboard_lists.config.state.destination["dashboards"]["dash-src"] = {"id": "dash-dst"}
        client = dashboard_lists.config.destination_client
        client.post = AsyncMock(return_value={"id": 900, "name": "my-list"})
        client.put = AsyncMock(side_effect=_not_found_error("custom_timeboard dash-dst not found"))

        with pytest.raises(CustomClientHTTPError):
            asyncio.run(
                dashboard_lists._create_resource(
                    "42",
                    {"name": "my-list", "dashboards": [{"id": "dash-dst", "type": "custom_timeboard"}]},
                )
            )

        assert dashboard_lists.config.state.destination["dashboard_lists"]["42"] == {
            "id": 900,
            "name": "my-list",
            "dashboards": [],
        }

    def test_second_sync_after_members_failure_updates_instead_of_creating(self):
        dashboard_lists = _make_dashboard_lists()
        dashboard_lists.config.state.destination["dashboards"]["dash-src"] = {"id": "dash-dst"}
        client = dashboard_lists.config.destination_client
        client.post = AsyncMock(return_value={"id": 900, "name": "my-list"})
        source = {"name": "my-list", "dashboards": [{"id": "dash-src", "type": "custom_timeboard"}]}
        items_path = dashboard_lists.dash_list_items_path.format(900)

        client.put = AsyncMock(side_effect=_http_error(500))
        with pytest.raises(CustomClientHTTPError):
            asyncio.run(_sync_once(dashboard_lists, "42", source))

        members = {"dashboards": [{"id": "dash-dst", "type": "custom_timeboard"}]}

        async def fake_put(path, body):
            if path == items_path:
                return members
            return {"id": 900, "name": "my-list", "dashboards": None}

        client.put = AsyncMock(side_effect=fake_put)
        asyncio.run(_sync_once(dashboard_lists, "42", source))

        client.post.assert_awaited_once()
        client.put.assert_any_await("/api/v1/dashboard/lists/manual/900", {"name": "my-list"})
        client.put.assert_any_await(items_path, members)
        state = dashboard_lists.config.state.destination["dashboard_lists"]["42"]
        assert state["id"] == 900
        assert state["dashboards"] == members["dashboards"]

    def test_create_success_returns_list_with_members(self):
        dashboard_lists = _make_dashboard_lists()
        dashboard_lists.config.state.destination["dashboards"]["dash-src"] = {"id": "dash-dst"}
        client = dashboard_lists.config.destination_client
        client.post = AsyncMock(return_value={"id": 900, "name": "my-list"})
        members = {"dashboards": [{"id": "dash-dst", "type": "custom_timeboard"}]}
        client.put = AsyncMock(return_value=members)

        asyncio.run(
            dashboard_lists._create_resource(
                "42", {"name": "my-list", "dashboards": [{"id": "dash-dst", "type": "custom_timeboard"}]}
            )
        )

        assert dashboard_lists.config.state.destination["dashboard_lists"]["42"] == {
            "id": 900,
            "name": "my-list",
            **members,
        }


class _WarningCapturingLogger(_SignatureCheckedLogger):
    def __init__(self):
        super().__init__()
        self.warning_calls = []

    def warning(self, msg: str, *arg, _id: str = "", resource_type: str = "") -> None:
        self.warning_calls.append({"msg": msg, "args": arg, "_id": _id, "resource_type": resource_type})

    def debug(self, msg: str, *arg, _id: str = "", resource_type: str = "") -> None:
        pass


def test_update_dash_list_items_drops_unresolved_dashboards_and_warns():
    dashboard_lists = _make_dashboard_lists()
    dashboard_lists.config.skip_failed_resource_connections = True
    logger = _WarningCapturingLogger()
    dashboard_lists.config.logger = logger
    dashboard_lists.config.state.destination["dashboards"]["dash-src"] = {"id": "dash-dst"}
    client = dashboard_lists.config.destination_client
    client.put = AsyncMock(return_value={"dashboards": [{"id": "dash-dst", "type": "custom_timeboard"}]})
    resource = {
        "dashboards": [
            {"id": "dash-src", "type": "custom_timeboard"},
            {"id": "unsynced-b", "type": "custom_timeboard"},
            {"id": "unsynced-a", "type": "custom_screenboard"},
        ]
    }
    dashboard_lists.connect_resources("42", resource)

    asyncio.run(dashboard_lists.update_dash_list_items("dst-list", resource["dashboards"], {}))

    client.put.assert_awaited_once_with(
        dashboard_lists.dash_list_items_path.format("dst-list"),
        {"dashboards": [{"id": "dash-dst", "type": "custom_timeboard"}]},
    )
    assert logger.warning_calls == [
        {
            "msg": "dropping dashboards not yet synced to destination from dashboard list members; "
            "dropped_dashboard_ids=unsynced-a,unsynced-b",
            "args": (),
            "_id": "dst-list",
            "resource_type": "dashboard_lists",
        }
    ]


def test_update_dash_list_items_no_warning_when_all_resolved():
    dashboard_lists = _make_dashboard_lists()
    dashboard_lists.config.state.destination["dashboards"]["dash-src"] = {"id": "dash-dst"}
    dashboard_lists.config.destination_client.put = AsyncMock(return_value={"dashboards": []})

    asyncio.run(
        dashboard_lists.update_dash_list_items("dst-list", [{"id": "dash-dst", "type": "custom_timeboard"}], {})
    )

    dashboard_lists.config.logger.warning.assert_not_called()


class TestDashboardListsUpdateMissingList:
    def test_list_put_404_recreates_list(self):
        dashboard_lists = _make_dashboard_lists()
        dashboard_lists.config.state.destination["dashboards"]["dash-src"] = {"id": "dash-dst"}
        dashboard_lists.config.state.destination["dashboard_lists"]["42"] = {
            "id": 111,
            "name": "old-name",
            "dashboards": [],
        }
        client = dashboard_lists.config.destination_client
        members = {"dashboards": [{"id": "dash-dst", "type": "custom_timeboard"}]}

        async def fake_put(path, body):
            if path == "/api/v1/dashboard/lists/manual/111":
                raise _not_found_error("Manual Dashboard List with id 111 not found")
            return members

        client.put = AsyncMock(side_effect=fake_put)
        client.post = AsyncMock(return_value={"id": 222, "name": "my-list"})

        asyncio.run(
            dashboard_lists._update_resource(
                "42", {"name": "my-list", "dashboards": [{"id": "dash-dst", "type": "custom_timeboard"}]}
            )
        )

        client.post.assert_awaited_once_with("/api/v1/dashboard/lists/manual", {"name": "my-list"})
        client.put.assert_any_await(dashboard_lists.dash_list_items_path.format(222), members)
        state = dashboard_lists.config.state.destination["dashboard_lists"]["42"]
        assert state["id"] == 222
        assert state["dashboards"] == members["dashboards"]

    def test_list_put_non_404_propagates_without_recreate(self):
        dashboard_lists = _make_dashboard_lists()
        dashboard_lists.config.state.destination["dashboard_lists"]["42"] = {"id": 111, "dashboards": []}
        client = dashboard_lists.config.destination_client
        client.put = AsyncMock(side_effect=_http_error(500))
        client.post = AsyncMock()

        with pytest.raises(CustomClientHTTPError):
            asyncio.run(dashboard_lists._update_resource("42", {"name": "my-list", "dashboards": []}))

        client.post.assert_not_awaited()
        assert dashboard_lists.config.state.destination["dashboard_lists"]["42"]["id"] == 111
