from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import Monitors


class TestMonitorsResources(BaseResourcesTestClass):
    resource_type = Monitors.resource_type
