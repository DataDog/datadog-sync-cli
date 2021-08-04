# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import copy
from typing import Optional, List, Dict

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import check_diff
from datadog_sync.utils.custom_client import paginated_request, CustomClient


class Roles(BaseResource):
    resource_type = "roles"
    resource_config = ResourceConfig(
        base_path="/api/v2/roles",
        excluded_attributes=["id", "attributes.created_at", "attributes.modified_at", "attributes.user_count"],
    )
    # Additional Roles specific attributes
    source_permissions = None
    destination_permissions = None
    destination_roles_mapping = None
    permissions_base_path = "/api/v2/permissions"

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = paginated_request(client.get)(self.resource_config.base_path)

        return resp

    def import_resource(self, resource: Dict) -> None:
        self.resource_config.source_resources[resource["id"]] = resource

    def pre_apply_hook(self, resources: Dict[str, Dict]) -> Optional[list]:
        self.destination_roles_mapping = self.get_destination_roles_mapping()
        return None

    def pre_resource_action_hook(self, resource: Dict) -> None:
        self.remap_permissions(resource)

    def create_resource(self, _id, resource):
        if resource["attributes"]["name"] in self.destination_roles_mapping:
            role_copy = copy.deepcopy(resource)
            role_copy.update(self.destination_roles_mapping[resource["attributes"]["name"]])

            self.resource_config.destination_resources[_id] = role_copy
            if check_diff(self.resource_config, resource, role_copy):
                self.update_resource(_id, resource)
            return

        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = destination_client.post(self.resource_config.base_path, payload)

        self.resource_config.destination_resources[_id] = resp.json()["data"]

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        payload = {"data": resource}

        try:
            resp = destination_client.patch(
                self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}", payload
            )
        except HTTPError as e:
            raise e

        self.resource_config.destination_resources[_id] = resp.json()["data"]

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> None:
        pass

    def remap_permissions(self, resource):
        if self.config.source_client.host != self.config.destination_client.host:
            if not (self.source_permissions and self.destination_permissions):
                self.source_permissions, self.destination_permissions = self.get_permissions()

            if "permissions" in resource["relationships"]:
                for permission in resource["relationships"]["permissions"]["data"]:
                    if permission["id"] in self.source_permissions:
                        permission["id"] = self.destination_permissions[self.source_permissions[permission["id"]]]

    def get_permissions(self):
        source_permission_obj = {}
        destination_permission_obj = {}

        source_client = self.config.source_client
        destination_client = self.config.destination_client
        try:
            source_permissions = source_client.get(self.permissions_base_path).json()["data"]
            destination_permissions = destination_client.get(self.permissions_base_path).json()["data"]
        except HTTPError as e:
            self.config.logger.error("error getting permissions: %s", e.response.text)
            return

        for permission in source_permissions:
            source_permission_obj[permission["id"]] = permission["attributes"]["name"]
        for permission in destination_permissions:
            destination_permission_obj[permission["attributes"]["name"]] = permission["id"]

        return source_permission_obj, destination_permission_obj

    def get_destination_roles_mapping(self):
        destination_client = self.config.destination_client
        destination_roles_mapping = {}

        # Destination roles mapping
        try:
            destination_roles_resp = paginated_request(destination_client.get)(self.resource_config.base_path)
        except HTTPError as e:
            self.config.logger.error("error retrieving roles: %s", e.response.text)
            return destination_roles_mapping

        for role in destination_roles_resp:
            destination_roles_mapping[role["attributes"]["name"]] = role

        return destination_roles_mapping
