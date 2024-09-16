# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple
from copy import deepcopy

from aiohttp import ClientResponseError
from deepdiff.operator import BaseOperator

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class SensitiveDataScannerGroupsOrderIdsComparator(BaseOperator):
    def match(self, level):
        if "groups" in level.t1 and "groups" in level.t2:
            # make copy so we do not mutate the original
            level.t1 = deepcopy(level.t1)
            level.t2 = deepcopy(level.t2)

            # If we are at the top level, modify the list to exclude extra archives in destination.
            t1 = set(level.t1["groups"])
            t2 = set(level.t2["groups"])
            d_ignore = t1 - t2

            level.t1["groups"] = [_id for _id in level.t1["groups"] if _id not in d_ignore]
        return True

    def give_up_diffing(self, level, diff_instance) -> bool:
        return False


class SensitiveDataScannerGroupsOrder(BaseResource):
    resource_type = "sensitive_data_scanner_groups_order"
    resource_config = ResourceConfig(
        concurrent=False,
        base_path="/api/v2/sensitive-data-scanner/config",
        resource_connections={
            "sensitive_data_scanner_groups": ["groups"],
        },
        deep_diff_config={
            "ignore_order": False,
            "custom_operators": [SensitiveDataScannerGroupsOrderIdsComparator()],
        },
        excluded_attributes=[
            "id",
        ],
    )
    # Additional SensitiveDataScannerGroupsOrder specific attributes
    destination_sensitive_data_scanner_group_order: Dict[str, Dict] = dict()
    default_id: str = "sensitive-data-scanner-group-order"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        order = {
            "id": resp["data"]["id"],
            "groups": [r["id"] for r in resp.get("included", []) if r["type"] == "sensitive_data_scanner_group"],
        }

        return [order]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        return self.default_id, resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        destination_client = self.config.destination_client
        self.destination_sensitive_data_scanner_group_order = (await self.get_resources(destination_client))[0]

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        if not self.destination_sensitive_data_scanner_group_order:
            raise Exception("Failed to retrieve destination orgs sensitive data scanner group order")

        self.config.state.destination[self.resource_type][_id] = self.destination_sensitive_data_scanner_group_order
        return await self.update_resource(_id, resource)

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_resources = (
            self.destination_sensitive_data_scanner_group_order,
            self.config.state.destination[self.resource_type][_id],
        )
        ids_to_omit = set(resource["groups"]) - set(destination_resources["groups"])

        extra_ids_to_include = [_id for _id in destination_resources["groups"] if _id not in resource["groups"]]
        resource["groups"] = [_id for _id in resource["groups"] if _id not in ids_to_omit]
        resource["groups"] = resource["groups"] + extra_ids_to_include
        groups = [{"id": r, "type": "sensitive_data_scanner_group"} for r in resource["groups"]]

        payload = {
            "data": {
                "type": "sensitive_data_scanner_configuration",
                "id": destination_resources["id"],
                "relationships": {"groups": {"data": groups}},
            },
            "meta": {},
        }
        resource["id"] = destination_resources["id"]

        destination_client = self.config.destination_client
        retry_count = 0
        while retry_count < 3:
            try:
                await destination_client.patch(self.resource_config.base_path, payload)
                break
            except ClientResponseError as e:
                if e.status == 400 and "specified version is out of date" in e.message:
                    retry_count += 1
                    continue
                else:
                    raise e

        return _id, resource

    async def delete_resource(self, _id: str) -> None:
        self.config.logger.warning(
            "sensitive_data_scanner_groups_order cannot deleted. Removing resource from config only."
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(SensitiveDataScannerGroupsOrder, self).connect_id(key, r_obj, resource_to_connect)
