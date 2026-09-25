# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class RUMConfig(BaseResource):
    """RUM config (singleton org setting).

    RUM config is a singleton (no DELETE, no per-id path). Only
    ``enforced_application_tags`` is configurable; all other attributes are
    server-managed and excluded from diffs. ``create_resource`` checks whether
    the destination singleton already exists and delegates to ``update_resource``
    if so (mirroring ``logs_archives_order``); ``delete_resource`` is a no-op.
    """

    resource_type = "rum_config"
    resource_config = ResourceConfig(
        base_path="/api/v2/rum/config",
        excluded_attributes=[
            "id",
            "attributes.disabled",
            "attributes.enforced_application_tags_updated_at",
            "attributes.enforced_application_tags_updated_by",
            "attributes.ootb_metrics_version",
            "attributes.ootb_metrics_version_installed_at",
            "attributes.retention_filters_enabled",
            "attributes.retention_filters_enabled_updated_at",
            "attributes.retention_filters_enabled_updated_by",
        ],
        concurrent=False,
        skip_resource_mapping=True,
    )
    # Additional RUMConfig specific attributes
    default_id: str = "rum-config"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return [resp["data"]]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        # Singleton: always keyed by the default id.
        return self.default_id, resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def _existing_destination(self) -> Optional[Dict]:
        destination_client = self.config.destination_client
        try:
            resp = await destination_client.get(self.resource_config.base_path)
            return resp["data"]
        except Exception as e:
            self.config.logger.debug(f"rum_config: destination singleton not present: {e}")
            return None

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        # If the destination singleton already exists, hydrate state and delegate
        # to update (mirrors logs_archives_order). Otherwise POST a new one.
        existing = await self._existing_destination()
        if existing is not None:
            self.config.state.destination[self.resource_type][_id] = existing
            return await self.update_resource(_id, resource)

        destination_client = self.config.destination_client
        payload = {
            "data": {
                "type": self.resource_type,
                "attributes": {"enforced_application_tags": resource["attributes"]["enforced_application_tags"]},
            }
        }
        resp = await destination_client.post(self.resource_config.base_path, payload)
        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        payload = {
            "data": {
                "type": self.resource_type,
                "attributes": {"enforced_application_tags": resource["attributes"]["enforced_application_tags"]},
            }
        }
        resp = await destination_client.patch(self.resource_config.base_path, payload)
        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        self.config.logger.warning("rum_config cannot be deleted. Removing resource from state only.")
