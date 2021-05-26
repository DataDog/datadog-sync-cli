import json

from datadog_api_client.v2.api import roles_api

from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.utils.retry import request_with_retry
from datadog_sync.constants import (
    RESOURCE_STATE_PATH,
    RESOURCE_FILE_PATH,
)

RESOURCE_TYPE = "role"


class Role(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_TYPE)

    def post_import_processing(self):
        source_role_obj, destination_role_obj = self.get_roles()

        self.remap_permissions()

        # Update existing Role IDs in state file if roles share the same name.
        # If the role names are the same, we can assume they are equal.
        file_path = RESOURCE_STATE_PATH.format(self.resource_type)
        with open(file_path, "r") as f:
            data = json.load(f)
        for resource in data["modules"][0]["resources"]:
            source_id = data["modules"][0]["resources"][resource]["primary"]["id"]
            if source_id in source_role_obj and source_role_obj[source_id] in destination_role_obj:
                data["modules"][0]["resources"][resource]["primary"]["id"] = destination_role_obj[
                    source_role_obj[source_id]
                ]
                data["modules"][0]["resources"][resource]["primary"]["attributes"]["id"] = destination_role_obj[
                    source_role_obj[source_id]
                ]

        with open(file_path, "w") as f:
            json.dump(data, f, indent=4)

    def get_roles(self):
        source_client = self.ctx.obj.get("source_client_v2")
        destination_client = self.ctx.obj.get("destination_client_v2")

        source_role_obj = {}
        destination_role_obj = {}
        source_roles = []
        destination_roles = []

        page_size = 100
        page_number = 0
        remaining = 1
        r_retry = request_with_retry(roles_api.RolesApi(source_client).list_roles)
        while remaining > 0:
            resp = r_retry(page_size=page_size, page_number=page_number)
            source_roles.extend(resp["data"])
            remaining = int(resp["meta"]["page"]["total_count"]) - (page_size * (page_number + 1))
            page_number += 1

        # Reset counter for subsequent requests
        remaining = 1
        page_number = 0
        r_retry = request_with_retry(roles_api.RolesApi(destination_client).list_roles)
        while remaining > 0:
            resp = r_retry(page_size=page_size, page_number=page_number)
            destination_roles.extend(resp["data"])
            remaining = int(resp["meta"]["page"]["total_count"]) - (page_size * (page_number + 1))
            page_number += 1

        for role in source_roles:
            source_role_obj[role["id"]] = role["attributes"]["name"]
        for role in destination_roles:
            destination_role_obj[role["attributes"]["name"]] = role["id"]

        return source_role_obj, destination_role_obj

    def remap_permissions(self):
        source_client = self.ctx.obj.get("source_client_v2")
        destination_client = self.ctx.obj.get("destination_client_v2")

        source_permission_obj = {}
        destination_permission_obj = {}

        # If the source and destinations are not in the same region, we need to remap permission ID's
        if self.ctx.obj.get("source_api_url") != self.ctx.obj.get("destination_api_url"):
            source_permissions = roles_api.RolesApi(source_client).list_permissions()["data"]
            destination_permissions = roles_api.RolesApi(destination_client).list_permissions()["data"]
            for permission in source_permissions:
                source_permission_obj[permission["id"]] = permission["attributes"]["name"]
            for permission in destination_permissions:
                destination_permission_obj[permission["attributes"]["name"]] = permission["id"]

            file_path = RESOURCE_FILE_PATH.format(self.resource_type)
            with open(file_path, "r") as f:
                data = json.load(f)

            for resource in data["resource"]["datadog_role"]:
                if "permission" in data["resource"]["datadog_role"][resource]:
                    for permission in data["resource"]["datadog_role"][resource]["permission"]:
                        if permission["id"] in source_permission_obj:
                            permission["id"] = destination_permission_obj[source_permission_obj[permission["id"]]]

            with open(file_path, "w") as f:
                json.dump(data, f, indent=4)
