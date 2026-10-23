# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).

from datadog_sync.models import ObservabilityPipelines
from tests.integration.helpers import BaseResourcesTestClass

import pytest


@pytest.mark.skip(reason="Cannot delete these from destination easily")
class TestObservabilityPipelines(BaseResourcesTestClass):
    resource_type = ObservabilityPipelines.resource_type
    field_to_update = "attributes.name"
