from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import DashboardLists


class TestDashboardListsResources(BaseResourcesTestClass):
    resource_type = DashboardLists.resource_type
