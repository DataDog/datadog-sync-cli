# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import LogsCustomPipelines


@pytest.mark.skip(reason="Deprecated")
class TestLogsCustomPipelinesResources(BaseResourcesTestClass):
    resource_type = LogsCustomPipelines.resource_type
    field_to_update = "name"
