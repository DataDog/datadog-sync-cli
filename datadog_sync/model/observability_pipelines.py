# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import PaginationConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


def _op_remaining_func(idx, resp, page_size, page_number):
    """remaining_func for the OP list response.

    The OP API returns total count as ``meta.totalCount`` (camelCase), not the
    default ``meta.page.total_count`` used by most other v2 endpoints. If ``meta``
    or ``totalCount`` is absent, return a negative value so pagination stops
    after the current page (the ``resp_len < page_size`` break in
    ``paginated_request`` already handles the last-page case).
    """
    meta = resp.get("meta") or {}
    total = meta.get("totalCount", 0)
    return total - page_size * (page_number + 1)


class ObservabilityPipelines(BaseResource):
    resource_type = "observability_pipelines"
    resource_config = ResourceConfig(
        base_path="/api/v2/obs-pipelines/pipelines",
        excluded_attributes=["id"],
        resource_mapping_key="id",
    )
    # The OP list endpoint paginates with page[size]/page[number] (matching the
    # default param names) but returns total count as meta.totalCount, not the
    # default meta.page.total_count. Use a custom remaining_func so multi-page
    # responses don't raise KeyError on the missing meta.page key.
    pagination_config = PaginationConfig(
        page_size=100,
        page_size_param="page[size]",
        page_number_param="page[number]",
        remaining_func=_op_remaining_func,
    )

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.paginated_request(client.get)(
            self.resource_config.base_path, pagination_config=self.pagination_config
        )

        return resp

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (await source_client.get(self.resource_config.base_path + f"/{_id}"))["data"]
        resource = cast(dict, resource)

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        if _id in self._existing_resources_map:
            self.config.state.destination[self.resource_type][_id] = self._existing_resources_map[_id]
            return await self.update_resource(_id, resource)

        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = await destination_client.post(self.resource_config.base_path, payload)

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource["id"] = self.config.state.destination[self.resource_type][_id]["id"]
        payload = {"data": resource}
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            payload,
        )

        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )
