# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class RUMRetentionFiltersOrder(BaseResource):
    """Per-application RUM retention filter order.

    The order endpoint is PATCH-only (no GET/DELETE). The source order is
    captured from the ordered list returned by
    ``/api/v2/rum/applications/{app_id}/retention_filters``. The resource is
    keyed by application id; ``id`` (the app id) and ``data[*].id`` (filter ids)
    are remapped via ``resource_connections`` before apply. Both must survive
    ``prep_resource`` (so create/update can read them), so they are excluded
    from diffs via ``deep_diff_config.exclude_regex_paths`` rather than
    ``excluded_attributes`` (same pattern as rum_retention_filters'
    ``_application_id`` and users.py's ``handle``/``service_account``).

    Order is applied as a separate resource (synced after the filters exist at
    the destination) because a ``pre_apply_hook`` runs before apply, when the
    destination filters do not yet exist to be ordered.
    """

    resource_type = "rum_retention_filters_order"
    resource_config = ResourceConfig(
        base_path="/api/v2/rum/applications",
        resource_connections={
            "rum_applications": ["id"],
            "rum_retention_filters": ["data.id"],
        },
        concurrent=False,
        deep_diff_config={
            "ignore_order": False,  # order IS the resource
        },
        skip_resource_mapping=True,
    )
    # Additional RUMRetentionFiltersOrder specific attributes
    _applications_path = "/api/v2/rum/applications"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        apps = (await client.get(self._applications_path))["data"]
        resources: List[Dict] = []
        for app in apps:
            app_id = app["id"]
            resp = await client.get(f"{self._applications_path}/{app_id}/retention_filters")
            resources.append(
                {
                    "id": app_id,
                    "data": [{"id": f["id"], "type": "retention_filters"} for f in resp["data"]],
                }
            )
        return resources

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            # No GET endpoint; rebuild from the list response.
            source_client = self.config.source_client
            resp = await source_client.get(f"{self._applications_path}/{_id}/retention_filters")
            resource = {
                "id": _id,
                "data": [{"id": f["id"], "type": "retention_filters"} for f in resp["data"]],
            }

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        app_id = resource["id"]
        payload = {"data": resource["data"]}
        resp = await destination_client.patch(
            f"{self._applications_path}/{app_id}/relationships/retention_filters",
            payload,
        )
        # state stores the resource keyed by source app id; carry the app id + ordered ids
        data = {"id": app_id, "data": resp["data"]}
        return _id, data

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        app_id = resource["id"]
        payload = {"data": resource["data"]}
        resp = await destination_client.patch(
            f"{self._applications_path}/{app_id}/relationships/retention_filters",
            payload,
        )
        data = {"id": app_id, "data": resp["data"]}
        return _id, data

    async def delete_resource(self, _id: str) -> None:
        self.config.logger.warning("rum_retention_filters_order cannot be deleted. Removing resource from state only.")
