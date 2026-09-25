# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

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
            "exclude_regex_paths": [r".*\['application_id'\]", r".*\['operation_name'\]"],
        },
        skip_resource_mapping=True,
    )
    # Additional RUMOperationStrongLinks specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return resp["data"]

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
            return
        source_op = self.config.state.source.get("rum_operations", {}).get(op_id)
        if source_op:
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
        payload = {"data": resource}
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
                "type": self.resource_type,
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
