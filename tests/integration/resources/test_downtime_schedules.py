# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import DowntimeSchedules


class TestDowntimeSchedulesResources(BaseResourcesTestClass):
    resource_type = DowntimeSchedules.resource_type
    dependencies = list(DowntimeSchedules.resource_config.resource_connections.keys())
    field_to_update = "attributes.message"
    force_missing_deps = True
