# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from datadog_sync.models import Integrations
from tests.integration.helpers import BaseResourcesTestClass


class TestIntegrationsResources(BaseResourcesTestClass):
    resource_type = Integrations.resource_type
    dependencies = []  # AWS integrations typically have no dependencies on other resources
    field_to_update = "role_name"  # Field that can be safely updated for testing
    force_missing_deps = False  # No dependencies to force

    @staticmethod
    def compute_cleanup_changes(resource_count, num_of_skips):
        """AWS integrations cleanup logic"""
        return resource_count

    @staticmethod
    def compute_import_changes(resource_count, num_of_skips):
        """AWS integrations import logic"""
        return resource_count