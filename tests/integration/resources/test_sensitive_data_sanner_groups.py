# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import SensitiveDataScannerGroups


@pytest.mark.skip(reason="The sensitive data scanner makes testing it difficult")
class TestSensitiveDataScannerGroupsResources(BaseResourcesTestClass):
    resource_type = SensitiveDataScannerGroups.resource_type
    field_to_update = "attributes.name"
