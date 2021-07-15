from tests.integration.helpers import BaseResourceTestClass
from datadog_sync.models import Dashboards


class TestDashboardsResource(BaseResourceTestClass):
    resource_type = Dashboards.resource_type
