import pytest

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import Users


class TestUsersResources(BaseResourcesTestClass):
    resource_type = Users.resource_type
    field_to_update = "name"

    # FIXME: reintroduce after nested attribute update support
    @pytest.mark.skip(reason="nested attribute update is not currently supported in tests")
    def test_resource_update_sync(self):
        pass
