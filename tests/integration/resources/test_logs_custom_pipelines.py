from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import LogsCustomPipelines


class TestLogsCustomPipelinesResources(BaseResourcesTestClass):
    resource_type = LogsCustomPipelines.resource_type
