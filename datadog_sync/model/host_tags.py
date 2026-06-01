# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.constants import LOGGER_NAME
from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient

log = logging.getLogger(LOGGER_NAME)


class HostTags(BaseResource):
    resource_type = "host_tags"
    resource_config = ResourceConfig(
        base_path="/api/v1/tags/hosts",
        skip_resource_mapping=True,
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
        try:
            resp = await destination_client.put(self.resource_config.base_path + f"/{_id}", body)
        except CustomClientHTTPError as e:
            if e.status_code == 404:
                # Source orgs frequently carry ephemeral hosts (GKE node pools,
                # autoscaled VMs) that no longer exist on destination. 404 here
                # means "host gone — nothing to tag" and is the correct skip
                # signal, not a sync failure. Other status codes (4xx/5xx) still
                # propagate so the retry layer and failure accounting engage.
                log.info(f"[host_tags - {_id}] skipping: host no longer exists on destination")
                raise SkipResource(
                    _id,
                    self.resource_type,
                    f"host no longer exists on destination ({_id})",
                ) from None
            raise

        return _id, resp["tags"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(self.resource_config.base_path + f"/{_id}")
