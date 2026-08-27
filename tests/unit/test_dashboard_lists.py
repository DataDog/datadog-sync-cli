# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from collections import defaultdict
from unittest.mock import MagicMock

import pytest

from datadog_sync.model.dashboard_lists import DashboardLists
from datadog_sync.utils.resource_utils import ResourceConnectionError


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
