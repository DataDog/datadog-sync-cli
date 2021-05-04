from datadog_api_client.v1 import ApiException
from datadog_api_client.v1.api import dashboard_lists_api
from datadog_api_client.v2.api import dashboard_lists_api as dashboard_lists_api_v2

from datadog_sync.model.base_resource import BaseResource


DASHBOARD_LIST_FILTER = "@DatadogSync"
RESOURCE_NAME = "dashboard"


class Dashboard(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_NAME)
