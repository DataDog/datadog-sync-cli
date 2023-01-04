# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import pytest

from tests.conftest import get_record_mode
from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import Monitors


class TestMonitorsResources(BaseResourcesTestClass):
    resource_type = Monitors.resource_type
    field_to_update = "name"
