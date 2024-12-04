# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import Users


class TestUsersResources(BaseResourcesTestClass):
    @staticmethod
    def compute_changes(resource_count, num_of_skips):
        """Add the skips to the resource count"""
        return resource_count + num_of_skips

    resource_type = Users.resource_type
    field_to_update = "attributes.name"
    resources_to_preserve_filter = "Type=users;Name=attributes.status;Value=Active"
