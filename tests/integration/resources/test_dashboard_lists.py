# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import DashboardLists


class TestDashboardListsResources(BaseResourcesTestClass):
    resource_type = DashboardLists.resource_type
    dependencies = list(DashboardLists.resource_config.resource_connections.keys())
    field_to_update = "name"
    force_missing_deps = True
