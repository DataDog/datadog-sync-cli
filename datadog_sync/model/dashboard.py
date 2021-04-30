from datadog_api_client.v1 import ApiException
from datadog_api_client.v1.api import dashboard_lists_api
from datadog_api_client.v2.api import dashboard_lists_api as dashboard_lists_api_v2

from datadog_sync.model.base_resource import BaseResource


DASHBOARD_LIST_FILTER = "@DatadogSync"
RESOURCE_NAME = "dashboard"


class Dashboard(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_NAME)

    def get_resources(self):
        client_v1 = self.ctx.obj.get("source_client_v1")
        client_v2 = self.ctx.obj.get("source_client_v2")
        try:
            res = dashboard_lists_api.DashboardListsApi(
                client_v1
            ).list_dashboard_lists()
            for dashboard_list in res["dashboard_lists"]:
                if DASHBOARD_LIST_FILTER in dashboard_list["name"]:
                    dash_list_items = dashboard_lists_api_v2.DashboardListsApi(
                        client_v2
                    ).get_dashboard_list_items(dashboard_list["id"])

                    for dashboard in dash_list_items["dashboards"]:
                        self.ids.append(dashboard["id"])
        except ApiException as e:
            print("Error retrieving dashboard list", e.body)
            pass
