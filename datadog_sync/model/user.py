import json

from datadog_api_client.v2 import ApiException
from datadog_api_client.v2.api import users_api

from datadog_sync.model.base_resource import BaseResource
from datadog_sync.constants import RESOURCE_STATE_PATH
from datadog_sync.utils.retry import request_with_retry


RESOURCE_NAME = "user"
RESOURCE_FILTER = "Type=user;Name=disabled;Value=false"


class User(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_NAME, RESOURCE_FILTER)

    def post_import_processing(self):
        destination_user_obj = self.get_destination_users()

        file_path = RESOURCE_STATE_PATH.format(self.resource_name)
        with open(file_path, "r") as f:
            data = json.load(f)
        for resource in data["modules"][0]["resources"]:
            user_email = data["modules"][0]["resources"][resource]["primary"][
                "attributes"
            ]["email"]
            if user_email in destination_user_obj:
                data["modules"][0]["resources"][resource]["primary"][
                    "id"
                ] = destination_user_obj[user_email]
                data["modules"][0]["resources"][resource]["primary"]["attributes"][
                    "id"
                ] = destination_user_obj[user_email]

        with open(file_path, "w") as f:
            json.dump(data, f, indent=4)

    def get_destination_users(self):
        destination_client = self.ctx.obj.get("destination_client_v2")
        destination_users = []
        destination_user_obj = {}

        page_size = 1000
        page_number = 0
        remaining = 1
        r_retry = request_with_retry(users_api.UsersApi(destination_client).list_users)
        while remaining > 0:
            resp = r_retry(
                page_size=page_size, page_number=page_number, filter_status="Active"
            )
            destination_users.extend(resp["data"])
            remaining = int(resp["meta"]["page"]["total_count"]) - (
                page_size * (page_number + 1)
            )
            page_number += 1

        for user in destination_users:
            destination_user_obj[user["attributes"]["email"]] = user["id"]

        return destination_user_obj
