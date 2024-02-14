# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

from datadog_sync.model.monitors import Monitors
from datadog_sync.utils.base_resource import TaggingConfig
from datadog_sync.utils.filter import process_filters

@pytest.mark.parametrize(
    "path, r_obj, expected",
    [
        (
            "tags",
            {"tags": ["test:true"]},
            {"tags": ["test:true", "managed_by:datadog-sync"]},
        ),
        (
            "tags",
            {"tags": []},
            {"tags": ["managed_by:datadog-sync"]},
        ),
        (
            "tags",
            {},
            {"tags": ["managed_by:datadog-sync"]},
        ),
        (
            "nested.tags",
            {"nested":{"tags": ["test:true"]}},
            {"nested":{"tags": ["test:true", "managed_by:datadog-sync"]}},
        ),
        (
            "nested.tags",
            {"nested":{"tags": []}},
            {"nested":{"tags": ["managed_by:datadog-sync"]}},
        ),
        (
            "nested.tags",
            {"nested":{}},
            {"nested":{"tags": ["managed_by:datadog-sync"]}},
        ),
        (
            "nested.missing.tags",
            {"nested":{}},
            {"nested":{}},
        ),
    ],
)
def test_tagging_config(path, r_obj, expected):
    c = TaggingConfig(path)
    c.add_default_tags(r_obj)

    assert r_obj == expected
