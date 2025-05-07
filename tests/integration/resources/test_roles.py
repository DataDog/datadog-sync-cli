# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import Roles


class TestRolesResources(BaseResourcesTestClass):
    resource_type = Roles.resource_type
    field_to_update = "attributes.name"
    resources_to_preserve_filter = "Type=roles;Name=attributes.user_count;Value=[^0]+;Operator=SubString"

    @pytest.mark.skip(reason="We cannot run this test as the test org does not have RBAC enabled")
    def test_resource_update_sync(self):
        pass

    @pytest.mark.skip(reason="We cannot run this test as the test org does not have RBAC enabled")
    def test_resource_update_sync_per_file(self):
        pass
