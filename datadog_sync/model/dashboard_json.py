from datadog_sync.utils.base_resource import BaseResource


RESOURCE_NAME = "dashboard_json"


class Dashboard(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_NAME)
