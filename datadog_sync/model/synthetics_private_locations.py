# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import re

from typing import TYPE_CHECKING, List, Dict, Optional, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig, TaggingConfig
from datadog_sync.utils.resource_utils import SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class SyntheticsPrivateLocations(BaseResource):
    resource_type = "synthetics_private_locations"
    resource_config = ResourceConfig(
        resource_connections={"roles": ["metadata.restricted_roles"]},
        base_path="/api/v1/synthetics/private-locations",
        excluded_attributes=[
            "id",
            "modifiedAt",
            "createdAt",
            "createdBy",
            "secrets",
            "config",
            "result_encryption",
        ],
        tagging_config=TaggingConfig(path="tags"),
    )
    # Additional SyntheticsPrivateLocations specific attributes
    base_locations_path: str = "/api/v1/synthetics/locations"
    pl_id_regex: re.Pattern = re.compile("^pl:.*")

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.base_locations_path)

        return resp["locations"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        source_client = self.config.source_client
        import_id = _id or resource["id"]

        if not self.pl_id_regex.match(import_id):
            raise SkipResource(import_id, self.resource_type, "Managed location.")

        pl = await source_client.get(self.resource_config.base_path + f"/{import_id}")
        self.config.storage.data[self.resource_type].source[import_id] = pl

        return import_id, pl

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client

        resp = await destination_client.post(self.resource_config.base_path, resource)

        pl = resp["private_location"]
        pl["config"] = resp.get("config")
        pl["result_encryption"] = resp.get("result_encryption")

        return _id, pl

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.config.storage.data[self.resource_type].destination[_id]['id']}",
            resource,
        )

        r = self.config.storage.data[self.resource_type].destination[_id]
        r.update(resp)
        return _id, r

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.storage.data[self.resource_type].destination[_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(SyntheticsPrivateLocations, self).connect_id(key, r_obj, resource_to_connect)
