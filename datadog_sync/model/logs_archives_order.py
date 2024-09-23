# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple
from copy import deepcopy

from deepdiff.operator import BaseOperator

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class LogsArchivesOrderIdsComparator(BaseOperator):
    def match(self, level):
        if "archive_ids" in level.t1 and "archive_ids" in level.t2:
            # make copy so we do not mutate the original
            level.t1 = deepcopy(level.t1)
            level.t2 = deepcopy(level.t2)

            # If we are at the top level, modify the list to exclude extra archives in destination.
            t1 = set(level.t1["archive_ids"])
            t2 = set(level.t2["archive_ids"])
            d_ignore = t1 - t2

            level.t1["archive_ids"] = [_id for _id in level.t1["archive_ids"] if _id not in d_ignore]
        return True

    def give_up_diffing(self, level, diff_instance) -> bool:
        return False


class LogsArchivesOrder(BaseResource):
    resource_type = "logs_archives_order"
    resource_config = ResourceConfig(
        concurrent=False,
        base_path="/api/v2/logs/config/archive-order",
        resource_connections={
            "logs_archives": ["data.attributes.archive_ids"],
        },
        deep_diff_config={
            "ignore_order": False,
            "custom_operators": [LogsArchivesOrderIdsComparator()],
        },
    )
    # Additional LogsArchivesOrder specific attributes
    destination_archives_order: Dict[str, Dict] = dict()
    default_id: str = "logs-archives-order"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return [resp]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = await source_client.get(self.resource_config.base_path)

        return self.default_id, resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        self.destination_archives_order = await self.get_destination_archives_order()

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        if not self.destination_archives_order:
            raise Exception("Failed to retrieve destination orgs logs archive order")

        self.config.state.destination[self.resource_type][_id] = self.destination_archives_order
        return await self.update_resource(_id, resource)

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_resources = (
            self.destination_archives_order or self.config.state.destination[self.resource_type][_id]
        )
        ids_to_omit = set(resource["data"]["attributes"]["archive_ids"]) - set(
            destination_resources["data"]["attributes"]["archive_ids"]
        )

        extra_ids_to_include = [
            _id
            for _id in destination_resources["data"]["attributes"]["archive_ids"]
            if _id not in resource["data"]["attributes"]["archive_ids"]
        ]

        resource["data"]["attributes"]["archive_ids"] = [
            _id for _id in resource["data"]["attributes"]["archive_ids"] if _id not in ids_to_omit
        ]
        resource["data"]["attributes"]["archive_ids"] = (
            resource["data"]["attributes"]["archive_ids"] + extra_ids_to_include
        )

        destination_client = self.config.destination_client
        resp = await destination_client.put(self.resource_config.base_path, resource)

        return _id, resp

    async def delete_resource(self, _id: str) -> None:
        self.config.logger.warning("logs_archives_order cannot deleted. Removing resource from config only.")

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(LogsArchivesOrder, self).connect_id(key, r_obj, resource_to_connect)

    async def get_destination_archives_order(self):
        destination_client = self.config.destination_client
        resp = await self.get_resources(destination_client)

        return resp[0]
