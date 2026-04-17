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


class Notebooks(BaseResource):
    resource_type = "notebooks"
    resource_config = ResourceConfig(
        base_path="/api/v1/notebooks",
        excluded_attributes=[
            "id",
            "attributes.cells.id",
            "attributes.created",
            "attributes.modified",
            "attributes.author",
            "attributes.metadata",
        ],
        non_nullable_attr=["attributes.schema_version"],
        null_values={
            "schema_version": [0],
        },
        skip_resource_mapping=True,
    )
    # Additional Notebooks specific attributes
    pagination_config = PaginationConfig(
        page_size=100,
        page_size_param="count",
        page_number_param="start",
        remaining_func=lambda idx, resp, page_size, page_number: (resp["meta"]["page"]["total_count"])
        - (page_size * (idx + 1)),
        page_number_func=lambda idx, page_size, page_number: page_size * (idx + 1),
    )

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.paginated_request(client.get)(
            self.resource_config.base_path, params={"include_cells": "true"}, pagination_config=self.pagination_config
        )

        return resp

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (await source_client.get(self.resource_config.base_path + f"/{_id}"))["data"]

        resource = cast(dict, resource)
        self.handle_special_case_attr(resource)

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = await destination_client.post(self.resource_config.base_path, payload)
        self.handle_special_case_attr(resp["data"])

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            payload,
        )
        self.handle_special_case_attr(resp["data"])

        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    # Server-managed AI usage tag keys injected by the Notebooks API on every write.
    # These reflect per-org interaction history (MCP vs human), not notebook content,
    # and must be stripped to avoid non-converging diffs during sync.
    _ai_usage_tag_keys = frozenset({"ai_generated", "ai_edited", "human_edited"})

    @staticmethod
    def handle_special_case_attr(resource):
        # Handle template_variables attribute
        if "template_variables" in resource["attributes"] and not resource["attributes"]["template_variables"]:
            resource["attributes"].pop("template_variables")

        # Strip server-managed AI usage tags
        tags = resource["attributes"].get("tags")
        if tags:
            resource["attributes"]["tags"] = [
                t for t in tags if t.split(":")[0] not in Notebooks._ai_usage_tag_keys
            ]
