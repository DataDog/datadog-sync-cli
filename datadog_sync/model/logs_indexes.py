# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
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
    logs_indexes_order_url: str = "/api/v1/logs/config/index-order"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return resp["indexes"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = await source_client.get(self.resource_config.base_path + f"/{_id}")

        resource = cast(dict, resource)
        if not resource.get("daily_limit"):
            resource["disable_daily_limit"] = True

        return resource["name"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        self.destination_logs_indexes = await self.get_destination_logs_indexes()

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        if _id in self.destination_logs_indexes:
            self.resource_config.destination_resources[_id] = self.destination_logs_indexes[_id]
            return await self.update_resource(_id, resource)

        destination_client = self.config.destination_client
        resp = destination_client.post(self.resource_config.base_path, resource)
        if not resp.get("daily_limit"):
            resp["disable_daily_limit"] = True

        return _id, resp

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        # Can't update name so remove it
        resource.pop("name")
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['name']}",
            resource,
        )

        self.resource_config.destination_resources[_id].update(resp)
        if not self.resource_config.destination_resources[_id].get("daily_limit"):
            self.resource_config.destination_resources[_id]["disable_daily_limit"] = True
        else:
            self.resource_config.destination_resources[_id].pop("disable_daily_limit", None)

        return _id, self.resource_config.destination_resources[_id]

    async def delete_resource(self, _id: str) -> None:
        index_name = self.resource_config.destination_resources[_id]["name"]
        index_order = await self.config.destination_client.get(self.logs_indexes_order_url)
        if index_name in index_order["index_names"]:
            self.config.logger.warning(
                f"logs index deletion is not supported. Moving index '{_id}' to end of index order list."
            )
            index_order["index_names"].remove(index_name)
            index_order["index_names"].append(index_name)
            await self.config.destination_client.put(self.logs_indexes_order_url, index_order)

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass

    async def get_destination_logs_indexes(self) -> Dict[str, Dict]:
        destination_global_variable_obj = {}
        destination_client = self.config.destination_client

        resp = await self.get_resources(destination_client)
        for variable in resp:
            destination_global_variable_obj[variable["name"]] = variable

        return destination_global_variable_obj
