# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import pytest

from datadog_sync.models import MetricTagConfigurations
from tests.integration.helpers import BaseResourcesTestClass


# We skip this test for now as it is also ran by test_cli.py
# TODO: introduce submitting unique distribution metrics from tests and creating this resource
@pytest.mark.skip
class TestMetricConfigurationResources(BaseResourcesTestClass):
    resource_type = MetricTagConfigurations.resource_type
    field_to_update = "attributes.tags."
