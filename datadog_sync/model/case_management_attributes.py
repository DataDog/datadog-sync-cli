# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


# Fields the create API accepts (CustomAttributeConfigAttributesCreate).
CREATE_ATTRIBUTE_FIELDS = ("description", "display_name", "is_multi", "key", "type")

# Fields the update API accepts (CustomAttributeConfigUpdateAttributes).
# map_from / type_data are update-only: neither documented read endpoint
# (org-wide list / per-type list) returns them — per the published API client
# models — so they are never present in source state in practice. They are
# still forwarded when present so hand-curated or future-API state converges.
UPDATE_ATTRIBUTE_FIELDS = ("description", "display_name", "type", "type_data", "map_from")


class CaseManagementAttributes(BaseResource):
    """Case management custom attribute configs
    (``/api/v2/cases/types/{case_type_id}/custom_attributes``).

    API shape (verified against the published API schema):

    - Discovery is the org-wide list
      ``GET /api/v2/cases/types/custom_attributes`` (single response; items
      carry ``attributes.case_type_id``). The CRUD paths are scoped under the
      parent case type. No per-id GET exists.
    - Read payloads expose ``{case_type_id, description, display_name,
      is_multi, key, type}`` only. The update-only fields ``map_from`` /
      ``type_data`` (e.g. SELECT options) are NOT readable through either
      documented read endpoint, so they cannot be synced — see the README
      limitation note.

    Identity/adoption is ``attributes.key``, which is unique org-wide
    (pinned identity contract — user-confirmed against the case management
    API team). Every mapping hit additionally validates the parent: the
    matched destination attribute must belong to the same (remapped)
    destination case type as the source attribute; a same-key hit under a
    different parent is treated as no-match, so a duplicate-key anomaly can
    never cross-parent update.

    Payloads are strict allowlists (see CREATE_ATTRIBUTE_FIELDS /
    UPDATE_ATTRIBUTE_FIELDS): the create body never carries ``id``,
    ``attributes.case_type_id`` (path concerns) or update-only fields; the
    update body never carries ``key`` / ``is_multi`` / ``case_type_id``
    (not accepted by the update model).

    Error contract: malformed source data at create raises ``SkipResource``
    (per-resource skip; the sync continues — disaster-recovery intent). A
    missing ``state.destination`` entry in update/delete raises ``KeyError``
    (framework invariant violation — loud, counted per-resource failure).
    """

    resource_type = "case_management_attributes"
    resource_config = ResourceConfig(
        base_path="/api/v2/cases/types/custom_attributes",
        resource_connections={
            "case_management_types": ["attributes.case_type_id"],
        },
        excluded_attributes=[
            "id",
        ],
        resource_mapping_key="attributes.key",
    )

    @staticmethod
    def _attribute_payload(resource: Dict, fields: Tuple[str, ...]) -> Dict:
        """Strict allowlist payload builder.

        ``attributes.case_type_id`` and ``id`` are never sent (the parent id
        is a path parameter; the config id is assigned by the API), and only
        the fields the target API accepts are forwarded.
        """
        source_attributes = resource.get("attributes") or {}
        attributes = {name: source_attributes[name] for name in fields if name in source_attributes}
        return {"data": {"type": "custom_attribute", "attributes": attributes}}

    @staticmethod
    def _resolved_case_type_id(resource: Dict) -> Optional[str]:
        """The (already connection-remapped) parent case type id, or None."""
        attributes = resource.get("attributes")
        if not isinstance(attributes, dict):
            return None
        case_type_id = attributes.get("case_type_id")
        if not isinstance(case_type_id, str) or not case_type_id:
            return None
        return case_type_id

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return resp["data"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if resource is not None:
            return resource["id"], resource

        # No per-id GET exists and nothing depends on case_management_attributes,
        # so the force-missing-dependency import path never targets this
        # resource. Refuse _id-only import with the exact contract rather than
        # a generic raise (get_resources_by_ids would classify a generic
        # exception as a silent "permanent" errored id).
        raise NotImplementedError(
            "case_management_attributes has no per-id GET endpoint; "
            "import via the org-wide list (GET /api/v2/cases/types/custom_attributes)"
        )

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client

        key = self.get_resource_mapping_key(resource)
        matched = self._existing_resources_map.get(key) if key else None
        if matched is not None:
            # Parent-consistency check: the matched destination attribute must
            # belong to the same (already remapped) destination case type as
            # the source attribute. A same-key hit under a different parent is
            # treated as no-match — never a cross-parent update.
            matched_parent = (matched.get("attributes") or {}).get("case_type_id")
            source_parent = self._resolved_case_type_id(resource)
            if matched_parent == source_parent:
                self.config.state.destination[self.resource_type][_id] = matched
                return await self.update_resource(_id, resource)
            self.config.logger.warning(
                "destination attribute with key %r exists under a different case type; "
                "treating as no-match and creating under the mapped parent",
                key,
                resource_type=self.resource_type,
                _id=_id,
            )

        case_type_id = self._resolved_case_type_id(resource)
        if case_type_id is None:
            raise SkipResource(
                _id,
                self.resource_type,
                "Attribute is missing a usable attributes.case_type_id (malformed source data).",
            )

        payload = self._attribute_payload(resource, CREATE_ATTRIBUTE_FIELDS)
        resp = await destination_client.post(
            f"/api/v2/cases/types/{case_type_id}/custom_attributes",
            payload,
        )

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        # KeyError on a missing entry is intentional: the apply flow only
        # routes here with the entry present, so its absence is a framework
        # invariant violation that must surface loudly.
        destination_resource = self.config.state.destination[self.resource_type][_id]
        dest_case_type_id = destination_resource["attributes"]["case_type_id"]
        dest_attribute_id = destination_resource["id"]

        payload = self._attribute_payload(resource, UPDATE_ATTRIBUTE_FIELDS)
        resp = await destination_client.put(
            f"/api/v2/cases/types/{dest_case_type_id}/custom_attributes/{dest_attribute_id}",
            payload,
        )

        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_resource = self.config.state.destination[self.resource_type][_id]
        dest_case_type_id = destination_resource["attributes"]["case_type_id"]
        dest_attribute_id = destination_resource["id"]

        await destination_client.delete(
            f"/api/v2/cases/types/{dest_case_type_id}/custom_attributes/{dest_attribute_id}"
        )
