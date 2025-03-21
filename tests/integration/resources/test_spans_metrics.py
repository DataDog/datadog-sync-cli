# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import SpansMetrics

import pytest


@pytest.mark.skip(reason="Cannot delete these from destination easily")
class TestSpansMetrics(BaseResourcesTestClass):
    resource_type = SpansMetrics.resource_type
    field_to_update = "attributes.filter.query"
