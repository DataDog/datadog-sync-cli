# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest
from unittest.mock import MagicMock, call

from datadog_sync import models
from datadog_sync.utils.resource_utils import find_attr


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
        if resource.resource_type not in order_list or not resource.resource_config.resource_connections:
            continue

        resource_index = order_list.index(resource.resource_type)

        if (
            len(
                [dep for dep in resource.resource_config.resource_connections if order_list.index(dep) > resource_index]
            )
            != 0
        ):
            return False

    return len(order_list) == len(set(order_list))
