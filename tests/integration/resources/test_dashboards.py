# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from datadog_sync.models import Dashboards
from tests.integration.helpers import BaseResourcesTestClass


class TestDashboardsResources(BaseResourcesTestClass):
    @staticmethod
    def compute_import_changes(resource_count, num_of_skips):
        return resource_count + num_of_skips

    resource_type = Dashboards.resource_type
    dependencies = list(Dashboards.resource_config.resource_connections.keys())
    field_to_update = "title"
    force_missing_deps = True
