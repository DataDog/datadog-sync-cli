# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import PaginationConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource

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
        # The lightweight LIST endpoint omits attributes.cells. Filters that
        # reference cells.* are list-unsafe and are deferred to the post-GET
        # pass in base_resource._import_resource (which evaluates --filter
        # against the full body, raising FilteredResource on rejection).
        # Metadata filters like --filter Type=notebooks;Name=attributes.name
        # continue to short-circuit at LIST-time on the cheap per-page
        # response. Without this, a positive filter on attributes.cells.*
        # would silently no-op against the cell-less LIST item (missing path
        # → False at filter.py:_is_match_helper).
        list_omitted_attr_prefixes=["attributes.cells"],
    )
    # Additional Notebooks specific attributes
    pagination_config = PaginationConfig(
        page_size=100,
        page_size_param="count",
        page_number_param="start",
        remaining_func=lambda idx, resp, page_size, page_number: (
            resp["meta"]["page"]["total_count"]
        )
        - (page_size * (idx + 1)),
        page_number_func=lambda idx, page_size, page_number: page_size * (idx + 1),
    )

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        # LIST without include_cells: returns notebook metadata only. The per-page
        # response with include_cells=true grows with cell count and dominates the
        # discovery-phase wall-clock on populated orgs (one user's full cell payload
        # per page). import_resource() now fetches each notebook individually below,
        # matching the dashboards pattern, which parallelises cleanly under
        # --max-workers and bounds the per-request payload to a single notebook.
        resp = await client.paginated_request(client.get)(
            self.resource_config.base_path, pagination_config=self.pagination_config
        )

        return resp

    async def import_resource(
        self, _id: Optional[str] = None, resource: Optional[Dict] = None
    ) -> Tuple[str, Dict]:
        source_client = self.config.source_client
        import_id = _id if _id is not None else (resource or {}).get("id")
        if import_id is None:
            raise ValueError("import_resource requires either _id or resource['id']")

        # Short-circuit when the caller already supplied a full body (cells
        # present). This is the --id-file path: get_resources_by_ids
        # (base_resource.py) already did the per-id GET and stored the body
        # in tmp_storage; the queue handler then calls _import_resource with
        # that body. Without this guard we would GET each notebook a second
        # time, doubling rate-limit pressure on id-file runs. Detection is by
        # cells presence — the lightweight LIST never has cells, so a
        # resource with attributes.cells came from a per-id GET.
        if (
            resource is not None
            and isinstance(resource.get("attributes"), dict)
            and "cells" in resource["attributes"]
        ):
            resource = cast(dict, resource)
            self.handle_special_case_attr(resource)
            return str(resource["id"]), resource

        try:
            resource = (
                await source_client.get(
                    self.resource_config.base_path + f"/{import_id}"
                )
            )["data"]
        except CustomClientHTTPError as err:
            # 403: notebook is in the LIST but restricted from per-id reads. Skip
            # rather than hard-fail so a single ACL'd notebook does not poison the
            # whole import run. Mirrors dashboards.import_resource.
            # 404: notebook was deleted between LIST enumeration and the per-id GET.
            # Skip — there is nothing to import.
            if err.status_code == 403:
                raise SkipResource(
                    import_id, self.resource_type, "No access to restricted notebook"
                )
            if err.status_code == 404:
                raise SkipResource(
                    import_id,
                    self.resource_type,
                    "Notebook deleted between list and fetch",
                )
            raise

        resource = cast(dict, resource)
        self.handle_special_case_attr(resource)

        # State writes go through str(_id) (base_resource._import_resource); the
        # API returns id as an int for notebooks. Cast here so callers and tests
        # see a consistent string contract, matching dashboards.
        return str(resource["id"]), resource

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
            self.resource_config.base_path
            + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            payload,
        )
        self.handle_special_case_attr(resp["data"])

        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path
            + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    # Server-managed AI usage tag keys injected by the Notebooks API on every write.
    # These reflect per-org interaction history (MCP vs human), not notebook content,
    # and must be stripped to avoid non-converging diffs during sync.
    _ai_usage_tag_keys = frozenset({"ai_generated", "ai_edited", "human_edited"})

    @staticmethod
    def handle_special_case_attr(resource):
        # Handle template_variables attribute
        if (
            "template_variables" in resource["attributes"]
            and not resource["attributes"]["template_variables"]
        ):
            resource["attributes"].pop("template_variables")

        # Strip server-managed AI usage tags
        tags = resource["attributes"].get("tags")
        if tags:
            resource["attributes"]["tags"] = [
                t for t in tags if t.split(":")[0] not in Notebooks._ai_usage_tag_keys
            ]
