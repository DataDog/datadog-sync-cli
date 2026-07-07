# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

from datadog_sync.models import MetricTagConfigurations
from tests.integration.helpers import BaseResourcesTestClass


class TestMetricConfigurationResources(BaseResourcesTestClass):
    resource_type = MetricTagConfigurations.resource_type
    field_to_update = "attributes.tags"

    @pytest.mark.skip(reason="This test is flakey")
    def test_resource_update_sync(self):
        pass

    @pytest.mark.skip(reason="This test is flakey")
    def test_resource_update_sync_per_file(self):
        pass

    @pytest.mark.skip(reason="This test is flakey")
    def test_resource_sync(self, runner, caplog):
        pass

    @pytest.mark.skip(reason="This test is flakey")
    def test_resource_sync_per_file(self, runner, caplog):
        pass
