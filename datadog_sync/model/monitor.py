from datadog_api_client.v1 import ApiException
from datadog_api_client.v1.api import monitors_api

from datadog_sync.model.base_resource import BaseResource


MONITOR_FILTER_TAG = "datadog:sync"
RESOURCE_NAME = "monitor"


class Monitor(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_NAME)

    def get_resources(self):
        client = self.ctx.obj.get("source_client_v1")
        try:
            res = monitors_api.MonitorsApi(client).list_monitors(
                monitor_tags=MONITOR_FILTER_TAG
            )
            for monitor in res:
                self.ids.append(monitor["id"])
        except ApiException as e:
            print("Error retrieving monitors", e.body)
            pass
