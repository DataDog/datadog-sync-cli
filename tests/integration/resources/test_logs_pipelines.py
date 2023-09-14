# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import LogsPipelines


class TestLogsPipelinesResourcesCustomFilter(BaseResourcesTestClass):
    resource_type = LogsPipelines.resource_type
    field_to_update = "name"
    filter = "Type=logs_pipelines;Name=is_read_only;Value=false"


class TestLogsPipelinesResourcesIntegrationFilter(BaseResourcesTestClass):
    resource_type = LogsPipelines.resource_type
    field_to_update = ""
    filter = "Type=logs_pipelines;Name=is_read_only;Value=true"

    @pytest.mark.skip(reason="resource is only updated by default")
    def test_resource_update_sync(self):
        pass
