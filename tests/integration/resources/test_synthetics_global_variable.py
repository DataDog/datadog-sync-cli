from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import SyntheticsGlobalVariables


class TestSyntheticsGlobalVariablesResources(BaseResourcesTestClass):
    resource_type = SyntheticsGlobalVariables.resource_type
    field_to_update = "description"
