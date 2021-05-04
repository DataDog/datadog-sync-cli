from datadog_api_client.v1 import ApiException
from datadog_api_client.v1.api import monitors_api

from datadog_sync.model.base_resource import BaseResource


RESOURCE_NAME = "monitor"


class Monitor(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_NAME)
