from datadog_sync.utils.resource_utils import replace, replace_ids
from datadog_sync import models
from datadog_sync.cli import get_import_order
import pytest


@pytest.fixture(scope="class")
def str_to_class():
    return dict([(cls.resource_type, cls) for name, cls in models.__dict__.items() if isinstance(cls, type)])


def test_replace_one_level_key():
    r_obj = {"key_example": "1"}
    connection_resources_obj = {
        "resource_name": {
            "1": {
                "id": 2,
            },
        }
    }
    r_obj_expected = {"key_example": "2"}
    replace("key_example", "origin", r_obj, "resource_name", connection_resources_obj)
    assert r_obj == r_obj_expected


def test_replace_multiple_levels_key():
    r_obj = {"a": {"b": {"c": "1"}}}

    connection_resources_obj = {
        "resource_name": {
            "1": {
                "id": 2,
            },
        }
    }

    r_obj_expected = {"a": {"b": {"c": "2"}}}

    replace("a.b.c", "origin", r_obj, "resource_name", connection_resources_obj)

    assert r_obj == r_obj_expected


def test_replace_multiple_levels_key_containing_an_array():
    r_obj = {"a": {"b": [{"c": "1"}, {"c": "2"}, {"c": "3"}]}}

    connection_resources_obj = {
        "resource_name": {
            "1": {
                "id": 2,
            },
            "2": {
                "id": 3,
            },
            "3": {
                "id": 4,
            },
        }
    }

    r_obj_expected = {"a": {"b": [{"c": "2"}, {"c": "3"}, {"c": "4"}]}}

    replace("a.b.c", "origin", r_obj, "resource_name", connection_resources_obj)

    assert r_obj == r_obj_expected


def test_replace_ids_empty_resource():
    r_obj = {}
    r_obj_expected = {}
    replace_ids("key_example", "origin", r_obj, "resource_name", {})
    assert r_obj == r_obj_expected


def test_replace_ids_composite_monitors():
    r_obj = {"query": "11111111 && 33333333 || ( !11111111 && !33333333 )", "type": "composite"}
    connection_resources_obj = {
        "monitors": {
            "11111111": {
                "id": 2222222,
            },
            "33333333": {
                "id": 4444444,
            },
        }
    }
    r_obj_expected = {"query": "2222222 && 4444444 || ( !2222222 && !4444444 )", "type": "composite"}
    replace_ids("query", "origin", r_obj, "monitors", connection_resources_obj)
    assert r_obj == r_obj_expected


def test_replace_composite_monitors():
    r_obj = {"query": "11111111 && 33333333 || ( !11111111 && !33333333 )", "type": "composite"}
    connection_resources_obj = {
        "monitors": {
            "11111111": {
                "id": 2222222,
            },
            "33333333": {
                "id": 4444444,
            },
        }
    }
    r_obj_expected = {"query": "2222222 && 4444444 || ( !2222222 && !4444444 )", "type": "composite"}
    replace("query", "origin", r_obj, "monitors", connection_resources_obj)
    assert r_obj == r_obj_expected


def test_replace_ids_composite_monitors_with_single_id():
    r_obj = {"query": "1 && 1", "type": "composite"}
    connection_resources_obj = {
        "monitors": {
            "1": {
                "id": 2,
            }
        }
    }
    r_obj_expected = {"query": "2 && 2", "type": "composite"}
    replace_ids("query", "origin", r_obj, "monitors", connection_resources_obj)
    assert r_obj == r_obj_expected

    r_obj = {"query": "1", "type": "composite"}
    connection_resources_obj = {
        "monitors": {
            "1": {
                "id": 2,
            }
        }
    }
    r_obj_expected = {"query": "2", "type": "composite"}
    replace_ids("query", "origin", r_obj, "monitors", connection_resources_obj)
    assert r_obj == r_obj_expected


def test_replace_ids_composite_monitors_with_overlapping_ids():
    r_obj = {"query": "1 && 2 || ( !1 && !2 )", "type": "composite"}
    connection_resources_obj = {
        "monitors": {
            "1": {
                "id": 2,
            },
            "2": {
                "id": 3,
            },
        }
    }
    r_obj_expected = {"query": "2 && 3 || ( !2 && !3 )", "type": "composite"}
    replace_ids("query", "origin", r_obj, "monitors", connection_resources_obj)
    assert r_obj == r_obj_expected


def validate_order_list(order_list, resources):
    # checks that no dependency comes after the current resource in the order_list
    for resource in resources:
        if resource.resource_type not in order_list or not resource.resource_connections:
            continue

        resource_index = order_list.index(resource.resource_type)

        if len([dep for dep in resource.resource_connections if order_list.index(dep) > resource_index]) != 0:
            return False

    return len(order_list) == len(set(order_list))


def test_get_import_order_all_resources(str_to_class):
    resources = [
        models.Roles(None),
        models.Users(None),
        models.SyntheticsPrivateLocations(None),
        models.SyntheticsTests(None),
        models.SyntheticsGlobalVariables(None),
        models.Monitors(None),
        models.Downtimes(None),
        models.Dashboards(None),
        models.DashboardLists(None),
        models.ServiceLevelObjectives(None),
        models.LogsCustomPipelines(None),
        # models.IntegrationsAWS(None),
    ]

    order_list = get_import_order(resources, str_to_class)

    assert validate_order_list(order_list, resources)


def test_get_import_order_users(str_to_class):
    resources = [
        models.Users(None),
    ]

    order_list = get_import_order(resources, str_to_class)

    assert validate_order_list(order_list, resources)


def test_get_import_synthetics_tests(str_to_class):
    resources = [
        models.SyntheticsTests(None),
    ]

    order_list = get_import_order(resources, str_to_class)

    assert validate_order_list(order_list, resources)


def test_get_import_monitors(str_to_class):
    resources = [
        models.Monitors(None),
    ]

    order_list = get_import_order(resources, str_to_class)

    assert validate_order_list(order_list, resources)


def test_get_import_dashboards_lists(str_to_class):
    resources = [
        models.DashboardLists(None),
    ]

    order_list = get_import_order(resources, str_to_class)

    assert validate_order_list(order_list, resources)


def test_get_import_service_level_objectives(str_to_class):
    resources = [
        models.ServiceLevelObjectives(None),
    ]

    order_list = get_import_order(resources, str_to_class)

    assert validate_order_list(order_list, resources)
