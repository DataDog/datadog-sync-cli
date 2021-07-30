# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import re
from typing import Optional

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import ResourceConnectionError


class Monitors(BaseResource):
    resource_type = "monitors"
    resource_config = ResourceConfig(
        resource_connections={"monitors": ["query"], "roles": ["restricted_roles"]},
        base_path="/api/v1/monitor",
        excluded_attributes=[
            "id",
            "matching_downtimes",
            "creator",
            "created",
            "deleted",
            "org_id",
            "created_at",
            "modified",
            "overall_state",
            "overall_state_modified",
        ],
    )
    # Additional Monitors specific attributes

    def get_resources(self, client) -> list:
        try:
            resp = client.get(self.resource_config.base_path).json()
        except HTTPError as e:
            self.config.logger.error("error importing monitors %s", e)
            return []

        return resp

    def import_resource(self, resource) -> None:
        if resource["type"] == "synthetics alert":
            return
        self.resource_config.source_resources[str(resource["id"])] = resource

    def pre_resource_action_hook(self, resource) -> None:
        pass

    def pre_apply_hook(self, resources) -> Optional[list]:
        simple_monitors = {}
        composite_monitors = {}

        for _id, monitor in self.resource_config.source_resources.items():
            if monitor["type"] == "composite":
                composite_monitors[_id] = monitor
            else:
                simple_monitors[_id] = monitor
        return [simple_monitors, composite_monitors]

    def create_resource(self, _id, resource) -> None:
        destination_client = self.config.destination_client

        try:
            resp = destination_client.post(self.resource_config.base_path, resource).json()
        except HTTPError as e:
            self.config.logger.error("error creating monitor: %s", e.response.text)
            return
        self.resource_config.destination_resources[_id] = resp

    def update_resource(self, _id, resource) -> None:
        destination_client = self.config.destination_client

        try:
            resp = destination_client.put(
                self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}", resource
            ).json()
        except HTTPError as e:
            self.config.logger.error("error creating monitor: %s", e.response.text)
            return
        self.resource_config.destination_resources[_id] = resp

    def connect_id(self, key, r_obj, resource_to_connect) -> None:
        resources = self.config.resources[resource_to_connect].resource_config.destination_resources
        if r_obj.get("type") == "composite" and key == "query":
            ids = re.findall("[0-9]+", r_obj[key])
            for _id in ids:
                if _id in resources:
                    new_id = f"{resources[_id]['id']}"
                    r_obj[key] = re.sub(_id + r"([^#]|$)", new_id + "# ", r_obj[key])
                else:
                    raise ResourceConnectionError(resource_to_connect, _id=_id)
            r_obj[key] = (r_obj[key].replace("#", "")).strip()
