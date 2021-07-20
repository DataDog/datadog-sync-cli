import pytest

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import Roles


class TestRolesResources(BaseResourcesTestClass):
    resource_type = Roles.resource_type
    field_to_update = "name"

    @pytest.mark.skip(reason="We cannot run this test as the test org does not have RBAC enabled")
    def test_resource_update_sync(self):
        pass
