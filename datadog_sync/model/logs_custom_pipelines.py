# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from typing import Optional

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig


class LogsCustomPipelines(BaseResource):
    resource_type = "logs_custom_pipelines"
    resource_config = ResourceConfig(
        concurrent=False,
        base_path="/api/v1/logs/config/pipelines",
        excluded_attributes=["id", "type", "is_read_only"],
    )
    # Additional LogsCustomPipelines specific attributes

    def get_resources(self, client) -> list:
        try:
            resp = client.get(self.resource_config.base_path).json()
        except HTTPError as e:
            self.config.logger.error("error importing logs_custom_pipelines %s", e)
            return []

        return resp

    def import_resource(self, resource) -> None:
        if resource["is_read_only"]:
            return

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
            self.config.logger.error("error creating logs_custom_pipeline: %s", e.response.text)
            return
        self.resource_config.destination_resources[_id] = resp

    def update_resource(self, _id, resource) -> None:
        destination_client = self.config.destination_client

        try:
            resp = destination_client.put(
                self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}", resource
            ).json()
        except HTTPError as e:
            self.config.logger.error("error creating logs_custom_pipeline: %s", e.response.text)
            return
        self.resource_config.destination_resources[_id] = resp

    def connect_id(self, key, r_obj, resource_to_connect) -> None:
        super(LogsCustomPipelines, self).connect_id(key, r_obj, resource_to_connect)
