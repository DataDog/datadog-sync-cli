# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from collections import defaultdict
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class HostTags(BaseResource):
    resource_type = "host_tags"
    resource_config = ResourceConfig(
        base_path="/api/v1/tags/hosts",
    )
    # Additional HostTags specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        import_hosts = defaultdict(list)
        for tag, hosts in resp["tags"].items():
            for host in hosts:
                import_hosts[host].append(tag)

        return [{k: v} for k, v in import_hosts.items()]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            return  # This should never occur. No resource depends on it.

        host = list(resource.keys())[0]
        tags = list(resource.values())[0]

        return host, tags

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        return await self.update_resource(_id, resource)

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        body = {"tags": resource}
        resp = await destination_client.put(self.resource_config.base_path + f"/{_id}", body)

        return _id, resp["tags"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(self.resource_config.base_path + f"/{_id}")

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass
