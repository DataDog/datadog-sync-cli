# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from copy import deepcopy
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource, check_diff, prep_resource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient

# Substrings we treat as "destination refuses this update because the sync
# identity cannot edit this dashboard in place". Match must be specific
# enough that a generic 403 (missing scope, RBAC failure, edge/WAF)
# does not fall through to the clone path.
_READ_ONLY_MARKERS = (
    "This dashboard is read-only",
    "user does not have permission to edit",
)


class Dashboards(BaseResource):
    resource_type = "dashboards"
    resource_config = ResourceConfig(
        resource_connections={
            "monitors": ["widgets.definition.alert_id", "widgets.definition.widgets.definition.alert_id"],
            "powerpacks": ["widgets.definition.powerpack_id", "widgets.definition.widgets.definition.powerpack_id"],
            "service_level_objectives": ["widgets.definition.slo_id", "widgets.definition.widgets.definition.slo_id"],
            "roles": ["restricted_roles"],
        },
        base_path="/api/v1/dashboard",
        excluded_attributes=[
            "id",
            "author_handle",
            "author_name",
            "url",
            "created_at",
            "modified_at",
            "is_read_only",
            "notify_list",
        ],
        skip_resource_mapping=True,
        # The LIST endpoint omits widgets. Filters that reference widgets.*
        # are list-unsafe and are deferred to the post-GET pass in
        # base_resource._import_resource (which evaluates --filter against
        # the full body, raising FilteredResource on rejection). Metadata
        # filters like --filter Type=dashboards;Name=title continue to
        # short-circuit at LIST-time on the cheap LIST response. Without
        # this, a positive filter on widgets.* would silently no-op against
        # the widget-less LIST item.
        list_omitted_attr_prefixes=["widgets"],
    )
    # Additional Dashboards specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return resp["dashboards"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        source_client = self.config.source_client
        import_id = _id or resource["id"]

        # Short-circuit when the caller already supplied a full body (widgets
        # present). The --id-file path uses get_resources_by_ids which calls
        # import_resource(_id=...) and stores the result in tmp_storage; the
        # queue handler then calls _import_resource(resource=full_body) which
        # would re-GET each dashboard without this guard. Detection is by
        # widgets presence — the LIST endpoint returns dashboard metadata
        # without widgets, so a resource carrying widgets came from a per-id
        # GET. Pre-existing latent issue in this model; addressed alongside
        # the notebooks lightweight-LIST change.
        if resource is not None and "widgets" in resource:
            resource = cast(dict, resource)
            return import_id, resource

        try:
            resource = await source_client.get(self.resource_config.base_path + f"/{import_id}")
        except CustomClientHTTPError as err:
            if err.status_code == 403:
                raise SkipResource(import_id, self.resource_type, "No access to restricted dashboard")
            raise err

        resource = cast(dict, resource)
        return import_id, resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resp = await destination_client.post(self.resource_config.base_path, resource)

        return _id, resp

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        destination_id = self.config.state.destination[self.resource_type][_id]["id"]
        try:
            resp = await destination_client.put(
                self.resource_config.base_path + f"/{destination_id}",
                resource,
            )
        except CustomClientHTTPError as e:
            if e.status_code == 403 and self._is_read_only_conflict(e):
                return await self._handle_read_only_conflict(_id, resource, destination_id)
            raise

        return _id, resp

    @staticmethod
    def _is_read_only_conflict(err: CustomClientHTTPError) -> bool:
        # PUT returns 403 with one of the messages in _READ_ONLY_MARKERS when
        # the destination copy cannot be edited in place. The markers are
        # deliberately specific to avoid swallowing unrelated 403s (RBAC,
        # missing scope, edge/WAF).
        body = str(err)
        return any(marker in body for marker in _READ_ONLY_MARKERS)

    async def _handle_read_only_conflict(
        self, _id: str, resource: Dict, destination_id: str
    ) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        destination_state = self.config.state.destination[self.resource_type][_id]
        description = destination_state.get("description") or ""
        is_suggested_preset = "[[suggested_dashboards]]" in description

        if is_suggested_preset and self._suggested_preset_matches(resource, destination_state):
            # Datadog auto-provisions "suggested" dashboards when an integration
            # is installed. If source content matches the preset byte-for-byte
            # in the sync-relevant fields, the update is a no-op — skipping is
            # safe. If the customer forked the preset, fall through to clone.
            raise SkipResource(
                _id,
                self.resource_type,
                "Destination is an unmodified Datadog-suggested dashboard preset; source content matches.",
            )

        clone_resp = await destination_client.post(self.resource_config.base_path, resource)
        new_destination_id = clone_resp.get("id") if isinstance(clone_resp, dict) else None
        self.config.logger.warning(
            f"dashboard {_id}: destination copy is read-only for the sync identity; "
            f"cloned as a new dashboard. old dst id={destination_id} new dst id={new_destination_id}. "
            f"The old copy is left in place on destination and is no longer referenced by sync state.",
        )
        return _id, clone_resp

    @classmethod
    def _suggested_preset_matches(cls, source: Dict, destination_state: Dict) -> bool:
        # Use the same predicate the sync handler uses to decide whether an
        # update is needed at all (resources_handler._apply_resource_cb). A
        # partial field enumeration would miss dashboard-shape fields like
        # layout_type / reflow_type / restricted_roles and cause a
        # false-match skip that drops customer edits on the floor. If the
        # handler saw a diff (that's why update_resource was invoked), and
        # check_diff still sees a diff after prep, the customer forked the
        # preset — fall through to clone.
        destination_copy = deepcopy(destination_state)
        prep_resource(cls.resource_config, destination_copy)
        return not check_diff(cls.resource_config, source, destination_copy)

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(Dashboards, self).connect_id(key, r_obj, resource_to_connect)
