# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


# Endpoint for RUM retention quota config (1:1 with application, scoped by app id).
_RETENTION_QUOTA_PATH = "/api/v2/rum/config/retention-quota/application"


class RUMApplications(BaseResource):
    resource_type = "rum_applications"
    resource_config = ResourceConfig(
        base_path="/api/v2/rum/applications",
        excluded_attributes=[
            "id",
            "attributes.api_key_id",
            "attributes.application_id",
            "attributes.client_token",
            "attributes.created_at",
            "attributes.created_by_handle",
            "attributes.apm_rum_flat_sampling_replay_sample_rate",
            "attributes.hash",
            "attributes.is_active",
            "attributes.ootb_metrics_installed",
            "attributes.org_id",
            "attributes.updated_at",
            "attributes.updated_by_handle",
            "attributes.product_scales.product_analytics_retention_scale.last_modified_at",
            "attributes.product_scales.product_analytics_retention_scale.state",
            "attributes.product_scales.rum_event_processing_scale.last_modified_at",
            "attributes.remote_config_id",
            "attributes.short_name",
            # _retention_quota runtime fields (excluded from diffs/payloads)
            "_retention_quota.attributes.org_id",
            "_retention_quota.attributes.updated_at",
            "_retention_quota.attributes.updated_by",
        ],
        deep_diff_config={
            "ignore_order": True,
            # _retention_quota.id is the app id (remapped by connect_resources);
            # keep it in the resource for create/update but exclude from diff
            # to avoid a perpetual diff on the id field.
            "exclude_regex_paths": [r".*\['_retention_quota'\]\['id'\]"],
        },
        skip_resource_mapping=True,
    )
    # Additional RUM Applications specific attributes

    async def _fetch_retention_quota(self, client: CustomClient, app_id: str) -> Optional[Dict]:
        """Fetch retention quota config for an application. Returns None on 404
        (no quota configured). Re-raises on other errors."""
        try:
            resp = await client.get(f"{_RETENTION_QUOTA_PATH}/{app_id}")
            return resp.get("data")
        except CustomClientHTTPError as e:
            if e.status_code == 404:
                return None
            raise

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        # the list endpoint doesn't return the whole resource, so pull them individually
        resources = []
        for partial_resource in resp["data"]:
            partial_resource_id = partial_resource["id"]
            whole_resource = (await client.get(self.resource_config.base_path + f"/{partial_resource_id}"))["data"]
            # Fetch retention quota config (1:1 with application). 404 = no quota set.
            quota = await self._fetch_retention_quota(client, partial_resource_id)
            if quota is not None:
                whole_resource["_retention_quota"] = quota
            resources.append(whole_resource)

        return resources

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (await source_client.get(self.resource_config.base_path + f"/{_id}"))["data"]
            # Fetch retention quota for this specific app
            quota = await self._fetch_retention_quota(source_client, _id)
            if quota is not None:
                resource["_retention_quota"] = quota

        resource = cast(dict, resource)
        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def _sync_retention_quota(self, dest_app_id: str, resource: Dict) -> None:
        """Create/update/delete the retention quota at the destination based
        on the source state. Called after create/update of the application."""
        destination_client = self.config.destination_client
        source_quota = resource.get("_retention_quota")

        # Check if destination already has a quota
        dest_quota = await self._fetch_retention_quota(destination_client, dest_app_id)

        if source_quota is not None:
            # Source has a quota -> PUT (create or update)
            quota_payload = {
                "data": {
                    "id": dest_app_id,
                    "type": "rum_quota_config",
                    "attributes": source_quota.get("attributes", {}),
                }
            }
            # Strip runtime-only fields from the payload
            attrs = quota_payload["data"]["attributes"]
            attrs.pop("org_id", None)
            attrs.pop("updated_at", None)
            attrs.pop("updated_by", None)
            await destination_client.put(f"{_RETENTION_QUOTA_PATH}/{dest_app_id}", quota_payload)
        elif dest_quota is not None:
            # Source has no quota but dest does -> DELETE
            try:
                await destination_client.delete(f"{_RETENTION_QUOTA_PATH}/{dest_app_id}")
            except CustomClientHTTPError as e:
                if e.status_code != 404:
                    raise

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        # Pop the quota before POSTing the application (it goes to a separate endpoint)
        retention_quota = resource.pop("_retention_quota", None)
        resource["type"] = "rum_application_create"
        payload = {"data": resource}
        post_resp = await destination_client.post(self.resource_config.base_path, payload)
        data = post_resp["data"]
        dest_app_id = data["id"]

        # After creating the app, sync the retention quota if source had one
        if retention_quota is not None:
            resource["_retention_quota"] = retention_quota  # re-attach for state
            try:
                quota_payload = {
                    "data": {
                        "id": dest_app_id,
                        "type": "rum_quota_config",
                        "attributes": retention_quota.get("attributes", {}),
                    }
                }
                attrs = quota_payload["data"]["attributes"]
                attrs.pop("org_id", None)
                attrs.pop("updated_at", None)
                attrs.pop("updated_by", None)
                await destination_client.put(f"{_RETENTION_QUOTA_PATH}/{dest_app_id}", quota_payload)
            except CustomClientHTTPError as e:
                self.config.logger.warning(f"Failed to sync retention quota for app {dest_app_id}: {e}")

        # Store _retention_quota in the returned data so it persists in
        # state.destination. Without this, the destination state never has
        # _retention_quota, so the diff can't detect quota changes or removals.
        if retention_quota is not None:
            data["_retention_quota"] = retention_quota

        return _id, data

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        destination_id = self.config.state.destination[self.resource_type][_id]["id"]

        # if the resource doesn't exist at the destination then create it
        existing_resources = await self.get_resources(destination_client)
        existing_resource_ids = [r["id"] for r in existing_resources]
        if destination_id not in existing_resource_ids:
            self.config.logger.debug(f"{destination_id} not found, creating it")
            return await self.create_resource(_id, resource)

        # Pop the quota before PATCHing the application (it goes to a separate endpoint)
        retention_quota = resource.pop("_retention_quota", None)

        # resource exists so we can update it
        resource["type"] = "rum_application_update"
        resource["id"] = destination_id
        payload = {"data": resource}
        resp = await destination_client.patch(
            self.resource_config.base_path + "/" + destination_id,
            payload,
        )
        data = resp["data"]

        # After updating the app, sync the retention quota. Always call this
        # (not just when there's a quota change) so quota removal is handled:
        # if source has no quota but dest does, _sync_retention_quota DELETEs it.
        await self._sync_retention_quota(
            destination_id, {"_retention_quota": retention_quota} if retention_quota else {}
        )

        # Store _retention_quota in the returned data so it persists in
        # state.destination for future diff comparisons.
        if retention_quota is not None:
            data["_retention_quota"] = retention_quota
        elif "_retention_quota" in data:
            data.pop("_retention_quota", None)

        return _id, data

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        dest_state = self.config.state.destination[self.resource_type][_id]
        dest_app_id = dest_state["id"]

        # Delete retention quota first (ignore 404 if no quota exists)
        try:
            await destination_client.delete(f"{_RETENTION_QUOTA_PATH}/{dest_app_id}")
        except CustomClientHTTPError as e:
            if e.status_code != 404:
                self.config.logger.warning(f"Failed to delete retention quota for app {dest_app_id}: {e}")

        # Then delete the application
        await destination_client.delete(self.resource_config.base_path + f"/{dest_app_id}")
