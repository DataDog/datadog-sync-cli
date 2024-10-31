# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import Downtimes

@pytest.mark.skip(reason="The v1 downtimes are deprecated")
class TestDowntimesResources(BaseResourcesTestClass):
    resource_type = Downtimes.resource_type
    field_to_update = "message"
