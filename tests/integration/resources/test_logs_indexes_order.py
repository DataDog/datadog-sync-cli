# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import LogsIndexesOrder


@pytest.mark.skip(reason="You cannot recreate an index with the same name as a deleted index")
class TestLogsIndexesOrder(BaseResourcesTestClass):
    resource_type = LogsIndexesOrder.resource_type
    dependencies = list(LogsIndexesOrder.resource_config.resource_connections.keys())
    force_missing_deps = True

    @pytest.mark.skip(reason="resource is only updated by default")
    def test_resource_update_sync(self):
        pass

    @pytest.mark.skip(reason="resource is only updated by default")
    def test_resource_update_sync_per_file(self):
        pass


@pytest.mark.parametrize(
    "resource, destination_resource, expected",
    [
        (
            {"index_names": ["index1", "index2", "index3"]},
            {"index_names": ["index3", "index2", "index4"]},
            {"index_names": ["index2", "index3", "index4"]},
        ),
        (
            {"index_names": ["index1"]},
            {"index_names": ["index3", "index1", "index4"]},
            {"index_names": ["index1", "index3", "index4"]},
        ),
        (
            {"index_names": ["index1", "index2", "index3"]},
            {"index_names": ["index3", "index1"]},
            {"index_names": ["index1", "index3"]},
        ),
        (
            {"index_names": ["index1", "index2", "index3"]},
            {"index_names": ["index1"]},
            {"index_names": ["index1"]},
        ),
        (
            {"index_names": ["index1"]},
            {"index_names": ["index5", "index1", "index3", "index4"]},
            {"index_names": ["index1", "index5", "index3", "index4"]},
        ),
    ],
)
def test_handle_index_diff(resource, destination_resource, expected):
    LogsIndexesOrder.handle_additional_indexes(resource, destination_resource)

    assert resource == expected
