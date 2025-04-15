# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest
from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import RestrictionPolicies


class TestRestrictionPoliciesResources(BaseResourcesTestClass):
    resource_type = RestrictionPolicies.resource_type
    dependencies = list(RestrictionPolicies.resource_config.resource_connections.keys())
    field_to_update = "attributes.name"
    force_missing_deps = True

    @pytest.mark.skip(reason="Difficult to test without creating another user/role/teams to test with.")
    def test_resource_update_sync(self):
        pass
