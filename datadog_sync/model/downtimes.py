# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from typing import Optional

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig


class Downtimes(BaseResource):
    resource_type = "downtimes"
    resource_config = ResourceConfig(
        resource_connections={"monitors": ["monitor_id"]},
        non_nullable_attr=["recurrence.until_date", "recurrence.until_occurrences"],
        base_path="/api/v1/downtime",
        excluded_attributes=[
            "root['id']",
            "root['updater_id']",
            "root['created']",
            "root['org_id']",
            "root['modified']",
            "root['creator_id']",
            "root['active']",
        ],
    )
    # Additional Downtimes specific attributes

    def get_resources(self, client) -> list:
        try:
            resp = client.get(self.resource_config.base_path).json()
        except HTTPError as e:
            self.config.logger.error("error importing downtimes %s", e)
            return []

        return resp

    def import_resource(self, resource) -> None:
        self.resource_config.source_resources[str(resource["id"])] = resource

    def pre_resource_action_hook(self, resource) -> None:
        pass

    def pre_apply_hook(self, resources) -> Optional[list]:
        pass

    def create_resource(self, _id, resource) -> None:
        destination_client = self.config.destination_client

        try:
            resp = destination_client.post(self.resource_config.base_path, resource).json()
        except HTTPError as e:
            self.config.logger.error("error creating downtime: %s", e.response.text)
            return

        self.resource_config.destination_resources[_id] = resp

    def update_resource(self, _id, resource) -> None:
        destination_client = self.config.destination_client
        try:
            resp = destination_client.put(
                self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}", resource
            ).json()
        except HTTPError as e:
            self.config.logger.error("error creating downtime: %s", e.response.text)
            return

        self.resource_config.destination_resources[_id] = resp

    def connect_id(self, key, r_obj, resource_to_connect) -> None:
        super(Downtimes, self).connect_id(key, r_obj, resource_to_connect)
