# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from collections import defaultdict
from unittest.mock import MagicMock

from datadog_sync.model.powerpacks import Powerpacks
from datadog_sync.utils.resource_utils import prep_resource


def _make_powerpacks():
    config = MagicMock()
    config.state = MagicMock()
    config.state.source = defaultdict(dict)
    config.state.destination = defaultdict(dict)
    config.skip_failed_resource_connections = False
    config.logger = MagicMock()
    return Powerpacks(config)


def test_group_widget_monitor_and_slo_ids_are_remapped():
    powerpacks = _make_powerpacks()
    powerpacks.config.state.destination["monitors"]["src-monitor"] = {
        "id": "dst-monitor"
    }
    powerpacks.config.state.destination["monitors"]["src-nested-monitor"] = {
        "id": "dst-nested-monitor"
    }
    powerpacks.config.state.destination["monitors"]["src-direct-monitor"] = {
        "id": "dst-direct-monitor"
    }
    powerpacks.config.state.destination["service_level_objectives"]["src-slo"] = {
        "id": "dst-slo"
    }
    powerpacks.config.state.destination["service_level_objectives"][
        "src-nested-slo"
    ] = {"id": "dst-nested-slo"}
    powerpacks.config.state.destination["service_level_objectives"][
        "src-direct-slo"
    ] = {"id": "dst-direct-slo"}
    resource = {
        "id": "src-powerpack",
        "type": "powerpack",
        "attributes": {
            "group_widget": {
                "definition": {
                    "alert_id": "src-direct-monitor",
                    "slo_id": "src-direct-slo",
                    "widgets": [
                        {
                            "definition": {
                                "type": "alert_graph",
                                "alert_id": "src-monitor",
                            }
                        },
                        {"definition": {"type": "slo", "slo_id": "src-slo"}},
                        {
                            "definition": {
                                "type": "group",
                                "widgets": [
                                    {
                                        "definition": {
                                            "type": "alert_graph",
                                            "alert_id": "src-nested-monitor",
                                        }
                                    },
                                    {
                                        "definition": {
                                            "type": "slo",
                                            "slo_id": "src-nested-slo",
                                        }
                                    },
                                ],
                            }
                        },
                    ],
                }
            }
        },
    }

    powerpacks.connect_resources("src-powerpack", resource)

    definition = resource["attributes"]["group_widget"]["definition"]
    assert definition["alert_id"] == "dst-direct-monitor"
    assert definition["slo_id"] == "dst-direct-slo"
    widgets = definition["widgets"]
    assert widgets[0]["definition"]["alert_id"] == "dst-monitor"
    assert widgets[1]["definition"]["slo_id"] == "dst-slo"
    nested_widgets = widgets[2]["definition"]["widgets"]
    assert nested_widgets[0]["definition"]["alert_id"] == "dst-nested-monitor"
    assert nested_widgets[1]["definition"]["slo_id"] == "dst-nested-slo"


def test_top_level_id_and_relationships_are_removed_but_widget_ids_remain():
    resource = {
        "id": "src-powerpack",
        "type": "powerpack",
        "attributes": {
            "group_widget": {
                "definition": {
                    "widgets": [
                        {"id": 12345, "definition": {"type": "timeseries"}},
                    ],
                }
            }
        },
        "relationships": {"author": {"data": {"type": "users", "id": "src-user"}}},
    }

    prep_resource(Powerpacks.resource_config, resource)

    assert "id" not in resource
    assert "relationships" not in resource
    assert (
        resource["attributes"]["group_widget"]["definition"]["widgets"][0]["id"]
        == 12345
    )
