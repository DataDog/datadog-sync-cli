from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import ServiceLevelObjectives


class TestServiceLevelObjectivesResources(BaseResourcesTestClass):
    resource_type = ServiceLevelObjectives.resource_type
