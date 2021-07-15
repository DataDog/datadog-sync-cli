from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import Dashboards


class TestDashboardsResources(BaseResourcesTestClass):
    resource_type = Dashboards.resource_type
