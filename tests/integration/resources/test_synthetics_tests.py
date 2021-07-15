from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import SyntheticsTests


class TestSyntheticsTestsResources(BaseResourcesTestClass):
    resource_type = SyntheticsTests.resource_type
    field_to_update = "message"
