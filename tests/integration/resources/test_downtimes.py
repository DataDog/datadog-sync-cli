from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import Downtimes


class TestDowntimesResources(BaseResourcesTestClass):
    resource_type = Downtimes.resource_type
    field_to_update = "message"
