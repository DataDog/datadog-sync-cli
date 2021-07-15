from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import Users


class TestUsersResources(BaseResourcesTestClass):
    resource_type = Users.resource_type
