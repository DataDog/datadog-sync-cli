import pytest

from datadog_sync.utils.filter import Filter


@pytest.mark.parametrize(
    "_filter, r_type, r_obj, expected",
    [
        (["Type=r_test;Name=attr;Value=exists;Operator=SubString"], "r_test", {"attr": "attr exists"}, True),
        (["Type=r_test;Name=attr;Value=exists"], "r_test", {"test": "attr Exists"}, False),
        (["Type=r_test_two;Name=attr;Value=exists"], "r_test", {"test": "attr exists"}, True),
    ],
)
def test_filter(_filter, r_type, r_obj, expected):
    filter_instance = Filter(_filter)

    assert filter_instance.is_applicable(r_type, r_obj) == expected


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
    Filter(_filter)
    assert "invalid filter" in caplog.text
