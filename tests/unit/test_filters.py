# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

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
    ],
)
def test_filters(_filter, r_type, r_obj, expected):
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
