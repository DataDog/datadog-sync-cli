# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from typing import Optional

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import ResourceConnectionError


class ServiceLevelObjectives(BaseResource):
    resource_type = "service_level_objectives"
    resource_config = ResourceConfig(
        resource_connections={"monitors": ["monitor_ids"], "synthetics_tests": []},
        base_path="/api/v1/slo",
        excluded_attributes=[
            "root['creator']",
            "root['id']",
            "root['created_at']",
            "root['modified_at']",
        ],
    )
    # Additional ServiceLevelObjectives specific attributes

    def get_resources(self, client) -> list:
        try:
            resp = client.get(self.resource_config.base_path).json()
        except HTTPError as e:
            self.config.logger.error("error importing slo %s", e)
            return []

        return resp["data"]

    def import_resource(self, resource) -> None:
        self.resource_config.source_resources[resource["id"]] = resource

    def pre_resource_action_hook(self, resource) -> None:
        pass

    def pre_apply_hook(self, resources) -> Optional[list]:
        pass

    def create_resource(self, _id, resource) -> None:
        destination_client = self.config.destination_client

        try:
            resp = destination_client.post(self.resource_config.base_path, resource).json()
        except HTTPError as e:
            self.config.logger.error("error creating slo: %s", e.response.text)
            return

        self.resource_config.destination_resources[_id] = resp["data"][0]

    def update_resource(self, _id, resource) -> None:
        destination_client = self.config.destination_client

        try:
            resp = destination_client.put(
                self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}", resource
            ).json()
        except HTTPError as e:
            self.config.logger.error("error creating slo: %s", e.response.text)
            return
        self.resource_config.destination_resources[_id] = resp["data"][0]

    def connect_id(self, key, r_obj, resource_to_connect) -> None:
        monitors = self.config.resources["monitors"].resource_config.destination_resources
        synthetics_tests = self.config.resources["synthetics_tests"].resource_config.destination_resources

        for i, obj in enumerate(r_obj[key]):
            _id = str(obj)
            # Check if resource exists in monitors
            if _id in monitors:
                r_obj[key][i] = monitors[_id]["id"]
                continue
            # Fall back on Synthetics and check
            found = False
            for k, v in synthetics_tests.items():
                if k.endswith(_id):
                    r_obj[key][i] = v["monitor_id"]
                    found = True
                    break
            if not found:
                raise ResourceConnectionError(resource_to_connect, _id=_id)
