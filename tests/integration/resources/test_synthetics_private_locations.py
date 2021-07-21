from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import SyntheticsPrivateLocations


class TestSyntheticsPrivateLocationsResources(BaseResourcesTestClass):
    resource_type = SyntheticsPrivateLocations.resource_type
    field_to_update = "name"
