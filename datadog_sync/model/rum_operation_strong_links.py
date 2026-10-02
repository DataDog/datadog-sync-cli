# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class RUMOperationStrongLinks(BaseResource):
    """RUM operation strong links.

    Strong links are keyed by the composite (operation_id, feature_id). The
    create payload requires ``application_id`` and ``operation_name`` which are
    NOT in the response, so ``pre_resource_action_hook`` derives them from the
    parent source operation (it runs before ``connect_resources`` remaps
    ``operation_id``). Only ``status`` is updatable, so update sends only
    status and the composite key is read from state.destination.

    ``application_id`` and ``operation_name`` are create-only (not in the
    response), so they are excluded from diffs via ``deep_diff_config`` rather
    than ``excluded_attributes`` (they must survive ``prep_resource`` so create
    can read them).
    """

    resource_type = "rum_operation_strong_links"
    resource_config = ResourceConfig(
        base_path="/api/v2/rum/operations/strong_links",
        excluded_attributes=[
            "id",
            "attributes.created_at",
            "attributes.updated_at",
        ],
        resource_connections={
            "rum_operations": ["attributes.operation_id"],
            "rum_applications": ["attributes.application_id"],
        },
        deep_diff_config={
            "ignore_order": True,
            # application_id and operation_name are create-only (not in the
            # response), so a source-vs-destination diff would always flag them.
            # description, tags, feature_id, operation_id are in the response
            # but NOT updatable via PUT (only status is), so including them in
            # the diff would cause a non-converging update loop.
            "exclude_regex_paths": [
                r".*\['application_id'\]",
                r".*\['operation_name'\]",
                r".*\['description'\]",
                r".*\['tags'\]",
                r".*\['feature_id'\]",
                r".*\['operation_id'\]",
            ],
        },
        skip_resource_mapping=True,
    )
    # Additional RUMOperationStrongLinks specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        # The strong_links list endpoint requires at least one of operation_id
        # or feature_id as a query parameter (the OpenAPI spec marks them
        # optional, but the API returns 400 without one). Iterate over
        # rum_operations and fetch strong links per operation_id.
        #
        # For the source client (import), state.source["rum_operations"] is
        # typically NOT populated yet because all resource types are discovered
        # in parallel. So we fetch operations directly from the API via the
        # search endpoint instead of relying on state.
        #
        # For the destination client (apply), state.destination["rum_operations"]
        # is populated after rum_operations is synced (apply runs after import).
        is_source = client is self.config.source_client

        if is_source:
            # Import discovery: fetch operations from the API since state
            # isn't populated yet (all types discover in parallel).
            ops_resp = await client.get("/api/v2/rum/operations/search")
            operation_ids = [op["id"] for op in ops_resp.get("data", [])]
        else:
            # Destination apply: use state (rum_operations already synced).
            state_map = getattr(self.config.state, "destination", {})
            operations = state_map.get("rum_operations", {}) if hasattr(state_map, "get") else {}
            operation_ids = list(operations.keys())

        all_strong_links: List[Dict] = []
        for op_id in operation_ids:
            resp = await client.get(
                self.resource_config.base_path,
                params={"operation_id": op_id},
            )
            all_strong_links.extend(resp.get("data", []))
        return all_strong_links

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        # No single-resource GET endpoint; the list endpoint is the only read.
        # The normal import flow supplies a full resource from get_resources.
        if not resource:
            raise Exception(
                f"rum_operation_strong_links import requires a resource body "
                f"(no GET-by-id endpoint); got _id={_id!r}"
            )

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        # Derive application_id and operation_name from the parent source
        # operation. Runs BEFORE connect_resources remaps operation_id, so the
        # operation_id here is still the source id (state.source is keyed by it).
        attrs = resource.setdefault("attributes", {})
        op_id = attrs.get("operation_id")
        if not op_id:
            raise SkipResource(
                _id,
                self.resource_type,
                f"Missing operation_id; cannot derive application_id and " f"operation_name for create payload.",
            )
        source_op = self.config.state.source.get("rum_operations", {}).get(op_id)
        if not source_op:
            raise SkipResource(
                _id,
                self.resource_type,
                f"Parent rum_operations {op_id!r} not found in source state; "
                f"cannot derive application_id and operation_name for create payload. "
                f"Ensure rum_operations is imported and synced before this resource.",
            )
        src_attrs = source_op.get("attributes", {})
        if "application_id" in src_attrs:
            attrs["application_id"] = src_attrs["application_id"]
        if "name" in src_attrs:
            attrs["operation_name"] = src_attrs["name"]

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        # create data has no id (server-assigned)
        resource.pop("id", None)

        # Destination reconciliation: skip_resource_mapping=True means the
        # pre-apply listing phase is skipped. Before POSTing, search for an
        # existing strong link with the same operation_id + feature_id at the
        # destination and adopt it via update instead of creating a duplicate
        # (409 Conflict).
        attrs = resource.get("attributes", {})
        op_id = attrs.get("operation_id", "")
        feature_id = attrs.get("feature_id", "")
        if op_id:
            try:
                existing = await destination_client.get(
                    self.resource_config.base_path,
                    params={"operation_id": op_id},
                )
                for sl in existing.get("data", []):
                    sl_attrs = sl.get("attributes", {})
                    if sl_attrs.get("operation_id") == op_id and sl_attrs.get("feature_id") == feature_id:
                        self.config.state.destination[self.resource_type][_id] = sl
                        return await self.update_resource(_id, resource)
            except CustomClientHTTPError as e:
                if e.status_code != 404:
                    raise

        payload = {"data": resource}
        # Ensure the API type is correct: the API expects "strong_links",
        # not the sync-cli resource_type "rum_operation_strong_links".
        resource["type"] = "strong_links"
        resp = await destination_client.post(self.resource_config.base_path, payload)
        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        dest_state = self.config.state.destination[self.resource_type][_id]
        dest_attrs = dest_state["attributes"]
        dest_op_id = dest_attrs["operation_id"]
        feature_id = dest_attrs["feature_id"]
        # only status is updatable
        payload = {
            "data": {
                "type": "strong_links",
                "attributes": {"status": resource["attributes"].get("status")},
            }
        }
        resp = await destination_client.put(
            f"{self.resource_config.base_path}/{dest_op_id}/{feature_id}",
            payload,
        )
        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        dest_state = self.config.state.destination[self.resource_type][_id]
        dest_attrs = dest_state["attributes"]
        dest_op_id = dest_attrs["operation_id"]
        feature_id = dest_attrs["feature_id"]
        await destination_client.delete(f"{self.resource_config.base_path}/{dest_op_id}/{feature_id}")
