# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from typing import Optional, List, Dict

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient


class LogsIndexes(BaseResource):
    resource_type = "logs_indexes"
    resource_config = ResourceConfig(
        base_path="/api/v1/logs/config/indexes",
        concurrent=False,
        excluded_attributes=[
            "is_rate_limited",
        ],
        non_nullable_attr=["daily_limit"],
    )
    # Additional LogsIndexes specific attributes
    destination_logs_indexes: Dict[str, Dict] = dict()

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(self.resource_config.base_path).json()
        return resp["indexes"]

    def import_resource(self, resource: Dict) -> None:
        if not resource.get("daily_limit"):
            resource["disable_daily_limit"] = True
        self.resource_config.source_resources[resource["name"]] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self, resources: Dict[str, Dict]) -> Optional[list]:
        self.destination_logs_indexes = self.get_destination_logs_indexes()
        return None

    def create_resource(self, _id: str, resource: Dict) -> None:
        if _id in self.destination_logs_indexes:
            self.resource_config.destination_resources[
                _id
            ] = self.destination_logs_indexes[_id]
            self.update_resource(_id, resource)
            return

        destination_client = self.config.destination_client
        resp = destination_client.post(self.resource_config.base_path, resource).json()
        if not resp.get("daily_limit"):
            resp["disable_daily_limit"] = True

        self.resource_config.destination_resources[_id] = resp

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        # Can't update name so remove it
        resource.pop("name")
        resp = destination_client.put(
            self.resource_config.base_path
            + f"/{self.resource_config.destination_resources[_id]['name']}",
            resource,
        ).json()

        self.resource_config.destination_resources[_id].update(resp)
        if not self.resource_config.destination_resources[_id].get("daily_limit"):
            self.resource_config.destination_resources[_id][
                "disable_daily_limit"
            ] = True
        else:
            self.resource_config.destination_resources[_id].pop(
                "disable_daily_limit", None
            )

    def delete_resource(self, _id: str) -> None:
        self.config.logger.info("logs index deletion is not supported")
        pass

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> None:
        super(LogsIndexes, self).connect_id(key, r_obj, resource_to_connect)

    def get_destination_logs_indexes(self) -> Dict[str, Dict]:
        destination_global_variable_obj = {}
        destination_client = self.config.destination_client

        resp = self.get_resources(destination_client)
        for variable in resp:
            destination_global_variable_obj[variable["name"]] = variable

        return destination_global_variable_obj
