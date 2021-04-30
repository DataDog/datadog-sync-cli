from datadog_api_client.v2 import ApiException
from datadog_api_client.v2.api import users_api, roles_api

from datadog_sync.model.base_resource import BaseResource


USER_ROLE_FILTER = "@DatadogSync"
RESOURCE_NAME = "user"


class User(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_NAME)

    def get_resources(self):
        client = self.ctx.obj.get("source_client_v2")
        try:
            # get USER_ROLE_FILTER role id
            res = roles_api.RolesApi(client).list_roles(filter=USER_ROLE_FILTER)
            if len(res["data"]) > 0:
                role_id = res["data"][0]["id"]
            else:
                return

            res = users_api.UsersApi(client).list_users(filter_status="active")
            for user in res["data"]:
                for role in user["relationships"]["roles"]["data"]:
                    if role["id"] == role_id:
                        self.ids.append(user["id"])
                        continue
        except ApiException as e:
            print("Error retrieving monitors", e.body)
            pass
