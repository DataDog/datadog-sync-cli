# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import LogsIndexesOrderNameComparator

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class LogsIndexesOrder(BaseResource):
    resource_type = "logs_indexes_order"
    resource_config = ResourceConfig(
        concurrent=False,
        base_path="/api/v1/logs/config/index-order",
        resource_connections={
            "logs_indexes": ["index_names"],
        },
        deep_diff_config={
            "ignore_order": False,
            "custom_operators": [LogsIndexesOrderNameComparator()],
        },
    )
    # Additional LogsIndexesOrder specific attributes
    destination_indexes_order: Dict[str, Dict] = dict()
    default_id: str = "logs-index-order"
    logs_indexes_path: str = "/api/v1/logs/config/indexes"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        indexes_resp = await client.get(self.logs_indexes_path)
        index_order_resp = await client.get(self.resource_config.base_path)

        valid_indexes = [index["name"] for index in indexes_resp["indexes"]]
        valid_indexes_order = [index for index in index_order_resp["index_names"] if index in valid_indexes]

        return [{"index_names": valid_indexes_order}]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = await source_client.get(self.resource_config.base_path)

        return self.default_id, resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        self.destination_indexes_order = await self.get_destination_indexes_order()

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        if not self.destination_indexes_order:
            raise Exception("Failed to retrieve destination orgs logs index order")

        self.config.storage.data[self.resource_type].destination[_id] = self.destination_indexes_order
        return await self.update_resource(_id, resource)

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_resources = (
            self.destination_indexes_order or self.config.storage.data[self.resource_type].destination[_id]
        )
        self.handle_additional_indexes(resource, destination_resources)

        destination_client = self.config.destination_client
        resp = await destination_client.put(self.resource_config.base_path, resource)

        return _id, resp

    async def delete_resource(self, _id: str) -> None:
        pass

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        logs_indexes = self.config.resources["logs_indexes"].resource_config.destination_resources

        failed_connections = []
        for i, name in enumerate(r_obj[key]):
            if name in logs_indexes:
                r_obj[key][i] = logs_indexes[name]["name"]
            else:
                failed_connections.append(name)

        return failed_connections

    async def get_destination_indexes_order(self):
        destination_client = self.config.destination_client
        resp = await self.get_resources(destination_client)

        return resp[0]

    @staticmethod
    def handle_additional_indexes(resource, destination_resource) -> None:
        # Logs index order requires all logs indexes in the destination org to be included in the payload
        # Additional indexes in the source org which need to be removed from the payload
        ids_to_omit = set(resource["index_names"]) - set(destination_resource["index_names"])
        resource["index_names"] = [_id for _id in resource["index_names"] if _id not in ids_to_omit]

        # Add back additional indexes present in the destination org while retaining the relative ordering
        # of the additional indexes
        extra_ids_to_include = [
            _id for _id in destination_resource["index_names"] if _id not in resource["index_names"]
        ]
        resource["index_names"] = resource["index_names"] + extra_ids_to_include
