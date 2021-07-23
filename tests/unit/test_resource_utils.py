import pytest
from unittest.mock import MagicMock, call

from datadog_sync import models
from datadog_sync.utils.configuration import get_import_order, get_resources
from datadog_sync.utils.resource_utils import find_attr
from datadog_sync.utils.base_resource import BaseResource


@pytest.fixture(scope="class")
def str_to_class():
    return dict([(cls.resource_type, cls) for name, cls in models.__dict__.items() if isinstance(cls, type)])


def test_find_attr():
    keys_list = "attribute"
    resource_to_connect = "test"
    r_obj = {"attribute": "value"}
    connect_func = MagicMock()

    find_attr(keys_list, resource_to_connect, r_obj, connect_func)

    connect_func.assert_called_once()
    connect_func.assert_called_with("attribute", {"attribute": "value"}, "test")


def test_find_nested_attr():
    keys_list = "test.attribute"
    resource_to_connect = "test"
    r_obj = {"test": {"attribute": "value"}}
    connect_func = MagicMock()

    find_attr(keys_list, resource_to_connect, r_obj, connect_func)

    connect_func.assert_called_once()
    connect_func.assert_called_with("attribute", {"attribute": "value"}, "test")


def test_find_nested_list_attr():
    keys_list = "test.attribute"
    resource_to_connect = "test"
    r_obj = {"test": [{"attribute": "value"}, {"attribute": "value2"}]}
    connect_func = MagicMock()

    find_attr(keys_list, resource_to_connect, r_obj, connect_func)

    assert connect_func.call_args_list == [
        call("attribute", {"attribute": "value"}, "test"),
        call("attribute", {"attribute": "value2"}, "test"),
    ]
    assert connect_func.call_count == 2


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
    resources = [cls for cls in models.__dict__.values() if isinstance(cls, type) and isinstance(cls, type)]

    order_list = get_import_order(resources, str_to_class)

    assert validate_order_list(order_list, resources)


def test_get_import_order_users(str_to_class):
    resources = [
        models.Users,
    ]

    order_list = get_import_order(resources, str_to_class)

    assert validate_order_list(order_list, resources)


def test_get_import_synthetics_tests(str_to_class):
    resources = [
        models.SyntheticsTests,
    ]

    order_list = get_import_order(resources, str_to_class)

    assert validate_order_list(order_list, resources)


def test_get_import_monitors(str_to_class):
    resources = [
        models.Monitors,
    ]

    order_list = get_import_order(resources, str_to_class)

    assert validate_order_list(order_list, resources)


def test_get_import_dashboards_lists(str_to_class):
    resources = [
        models.DashboardLists,
    ]

    order_list = get_import_order(resources, str_to_class)

    assert validate_order_list(order_list, resources)


def test_get_import_service_level_objectives(str_to_class):
    resources = [
        models.ServiceLevelObjectives,
    ]

    order_list = get_import_order(resources, str_to_class)

    assert validate_order_list(order_list, resources)


def test_get_resources_no_args(config):
    result, _ = get_resources(config, "")
    result_resources = [r[0] for r in result.items()]

    all_resources = [
        cls.resource_type for cls in models.__dict__.values() if isinstance(cls, type) and issubclass(cls, BaseResource)
    ]

    assert sorted(result_resources) == sorted(all_resources)


def test_get_resources_with_args(config):
    result, _ = get_resources(config, "monitors,downtimes,service_level_objectives")
    result_resources = [r[0] for r in result.items()]

    resources_arg = [
        "roles",
        "synthetics_private_locations",
        "synthetics_tests",
        "monitors",
        "downtimes",
        "service_level_objectives",
    ]

    assert sorted(result_resources) == sorted(resources_arg)
