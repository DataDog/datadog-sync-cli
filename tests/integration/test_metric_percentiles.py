# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import MetricPercentiles


class TestMetricPercentilesResources(BaseResourcesTestClass):
    resource_type = MetricPercentiles.resource_type
    field_to_update = "group_by"
