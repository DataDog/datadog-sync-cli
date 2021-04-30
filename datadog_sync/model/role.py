import json

from datadog_api_client.v2 import ApiException
from datadog_api_client.v2.api import roles_api

from datadog_sync.model.base_resource import BaseResource

from datadog_sync.constants import (
    RESOURCE_OUTPUT_PATH,
)

RESOURCE_NAME = "role"


class Role(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_NAME)

    def get_resources(self):
        source_client = self.ctx.obj.get("source_client_v2")
        try:
            res = roles_api.RolesApi(source_client).list_roles()
            for role in res["data"]:
                if role["attributes"]["user_count"] > 0:
                    self.ids.append(role["id"])
        except ApiException as e:
            print("Error retrieving roles", e.body)
            pass

    def post_import_processing(self):
        source_role_obj = {}
        source_permission_obj = {}
        destination_role_obj = {}
        destination_permission_obj = {}

        source_client = self.ctx.obj.get("source_client_v2")
        destination_client = self.ctx.obj.get("destination_client_v2")
        try:
            source_roles = roles_api.RolesApi(source_client).list_roles()["data"]
            destination_roles = roles_api.RolesApi(destination_client).list_roles()[
                "data"
            ]
            for role in source_roles:
                source_role_obj[role["id"]] = role["attributes"]["name"]
            for role in destination_roles:
                destination_role_obj[role["attributes"]["name"]] = role["id"]

            if self.ctx.obj.get("source_api_url") != self.ctx.obj.get(
                "destination_api_url"
            ):
                source_permissions = roles_api.RolesApi(
                    source_client
                ).list_permissions()["data"]
                destination_permissions = roles_api.RolesApi(
                    destination_client
                ).list_permissions()["data"]
                for permission in source_permissions:
                    source_permission_obj[permission["id"]] = permission["attributes"][
                        "name"
                    ]
                for permission in destination_permissions:
                    destination_permission_obj[
                        permission["attributes"]["name"]
                    ] = permission["id"]
        except ApiException as e:
            print("Error retrieving roles", e.body)

        # If the source and destinations are not in the same region, we need to remap permission ID's
        remap_permission = len(destination_permission_obj) != 0
        if remap_permission:
            file_path = (
                self.ctx.obj.get("root_path")
                + RESOURCE_OUTPUT_PATH.format(self.resource_name)
                + "/"
                + self.resource_name
                + ".tf.json"
            )
            with open(file_path, "r") as f:
                data = json.load(f)

            for resource in data["resource"]["datadog_role"]:
                if "permission" in data["resource"]["datadog_role"][resource]:
                    for permission in data["resource"]["datadog_role"][resource][
                        "permission"
                    ]:
                        permission["id"] = destination_permission_obj[
                            source_permission_obj[permission["id"]]
                        ]

            with open(file_path, "w") as f:
                json.dump(data, f, indent=4)

        # Update existing Role IDS in state file if roles share the same name
        file_path = (
            self.ctx.obj.get("root_path")
            + RESOURCE_OUTPUT_PATH.format(self.resource_name)
            + "/terraform.tfstate"
        )
        with open(file_path, "r") as f:
            data = json.load(f)
        for resource in data["modules"][0]["resources"]:
            source_id = data["modules"][0]["resources"][resource]["primary"]["id"]
            if source_id in source_role_obj:
                data["modules"][0]["resources"][resource]["primary"][
                    "id"
                ] = destination_role_obj[source_role_obj[source_id]]
                data["modules"][0]["resources"][resource]["primary"]["attributes"][
                    "id"
                ] = destination_role_obj[source_role_obj[source_id]]

        with open(file_path, "w") as f:
            json.dump(data, f, indent=4)
