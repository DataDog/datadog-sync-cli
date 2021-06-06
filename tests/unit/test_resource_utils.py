from datadog_sync.utils.resource_utils import replace, replace_ids


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
    replace("key_example", r_obj, "resource_name", connection_resources_obj)
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

    replace("a.b.c", r_obj, "resource_name", connection_resources_obj)

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

    replace("a.b.c", r_obj, "resource_name", connection_resources_obj)

    assert r_obj == r_obj_expected


def test_replace_ids_simple_monitors():
    r_obj = {}
    r_obj_expected = {}
    replace_ids("query", r_obj, "monitors", {})
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
    replace_ids("query", r_obj, "monitors", connection_resources_obj)
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
    replace("query", r_obj, "monitors", connection_resources_obj)
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
    replace_ids("query", r_obj, "monitors", connection_resources_obj)
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
    replace_ids("query", r_obj, "monitors", connection_resources_obj)
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
    replace_ids("query", r_obj, "monitors", connection_resources_obj)
    assert r_obj == r_obj_expected
