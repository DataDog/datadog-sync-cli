# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

from datadog_sync.model.monitors import Monitors
from datadog_sync.utils.filter import process_filters


@pytest.mark.parametrize(
    "_filter, r_type, r_obj, expected",
    [
        (["Type=r_test;Name=attr;Value=exists;Operator=SubString"], "r_test", {"attr": "attr exists"}, True),
        (["Type=r_test;Name=attr;Value=exists"], "r_test", {"test": "attr Exists"}, False),
        (["Type=r_test_two;Name=test;Value=exists"], "r_test_two", {"test": ["attr", "exists"]}, True),
        (["Type=r_test_two;Name=test;Value=exists"], "r_test_two", {"test": ["attr", "false"]}, False),
        (["Type=r_test_two;Name=test;Value=1"], "r_test_two", {"test": ["attr", 1]}, True),
        (["Type=r_test_two;Name=test;Value=1;Operator=NonExistent"], "r_test_two", {"test": ["attr", 1]}, True),
        (["Type=r_test_two;Name=test;Value=1"], "r_test_two", {"test": ["attr", 123]}, False),
        (["Type=r_test_two;Name=test;Value=1;Operator=SubString"], "r_test_two", {"test": ["attr", 123]}, True),
        (["Type=r_test;Name=test.nested;Value=123"], "r_test", {"test": [{"nested": 123}]}, True),
        (["Type=r_test;Name=test.nested.list;Value=123"], "r_test", {"test": [{"nested": [{"list": 123}]}]}, True),
        (["Type=r_test;Name=test.nested.list;Value=123"], "r_test", {"test": [{"nested": [{"list": 1234}]}]}, False),
        (["Type=r_test;Name=test.nested.list;Value=123"], "r_test", {"test": [{"nested": [{"list": 1234}, {"list": 123}]}]}, True),
        (["Type=r_test;Name=test.nested;Value=123"], "r_test", {"test": [{"nested": ["1234", "123"]}]}, True),
        (["Type=r_test;Name=test.nested;Value=123"], "r_test", {"test": [{"nested": ["1234", "12345"]}]}, False),
        (["Type=r_test;Name=test.nested.deep;Value=sub;Operator=SubString"], "r_test", {"test": [{"nested": {"deep": "substring"}}]}, True),
        (["Type=r_test;Name=test.nested.deep;Value=sub;"], "r_test", {"test": [{"nested": {"deep": "substring"}}]}, False),
        (["Type=r_test;Name=test.non.exist;Value=sub;"], "r_test", {"test": []}, False),
    ],
)
def test_filters_is_match(_filter, r_type, r_obj, expected):
    filters = process_filters(_filter)

    assert filters[r_type][0].is_match(r_obj) == expected


@pytest.mark.parametrize(
    "_filter",
    [
        (["Type=r_test;Name=attr;Value=exists;Operator:SubString"]),
        (["Type=r_test;Name=attr;Value=exists;Operator"]),
        (["Type=;Name=attr;Value=exists;Operator"]),
        (["Type="]),
    ],
)
def test_invalid_filter(caplog, _filter):
    process_filters(_filter)
    assert "invalid filter" in caplog.text


@pytest.mark.parametrize(
    "_filter, r_type, r_obj, expected",
    [
        (["Type=Monitors;Name=tags;Value=test:true"], Monitors, {"tags": ["test:true"]}, True),
        (["Type=Monitors;Name=tags;Value=test:true"], Monitors, {"tags": ["test:true", "second:true"]}, True),
        (
            ["Type=Monitors;Name=tags;Value=test:true", "Type=Monitors;Name=tags;Value=second:true"],
            Monitors,
            {"tags": ["test:true", "second:true"]},
            True,
        ),
        (
            ["Type=Monitors;Name=name;Value=RandomName", "Type=Monitors;Name=tags;Value=second:true"],
            Monitors,
            {"tags": ["test:true", "second:true"], "name": "RandomName"},
            True,
        ),
        (
            ["Type=Monitors;Name=tags;Value=test:true", "Type=Monitors;Name=tags;Value=second:false"],
            Monitors,
            {"tags": ["test:true", "second:true"]},
            False,
        ),
        (
            ["Type=Monitors;Name=tags;Value=test:true", "Type=Monitors;Name=tags;Value=second:false"],
            Monitors,
            {"tags": ["test:true"]},
            False,
        ),
        (
            ["Type=Monitors;Name=name;Value=RandomName", "Type=Monitors;Name=tags;Value=second:false"],
            Monitors,
            {"tags": ["test:true"], "name": "RandomName"},
            False,
        ),
    ],
)
def test_filters(config, _filter, r_type, r_obj, expected):
    config.filters = process_filters(_filter)
    config.filter_operator = "AND"
    resource = r_type(config)

    assert resource.filter(r_obj) == expected
