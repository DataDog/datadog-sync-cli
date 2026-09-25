# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class RUMRetentionFilters(BaseResource):
    """RUM retention filters, parent-scoped under a RUM application.

    The application id is not part of the filter body, so the model injects a
    synthetic ``_application_id`` during enumeration and remaps it (source app
    id -> destination app id) via ``resource_connections`` before apply.
    ``prep_resource`` runs after ``connect_resources`` and removes
    ``excluded_attributes``, so ``_application_id`` is deliberately NOT in
    ``excluded_attributes`` (it must survive prep so create/update can read it)
    and is instead kept out of diffs via ``deep_diff_config.exclude_regex_paths``.

    The model unifies the generic ``retention_filters`` type and the
    ``exclusion_filters`` type, which live on separate sub-paths
    (``/retention_filters`` vs ``/retention_filters/exclusion``) and are
    distinguished by ``data.type``.
    """

    resource_type = "rum_retention_filters"
    resource_config = ResourceConfig(
        base_path="/api/v2/rum/applications",
        excluded_attributes=[
            "id",
        ],
        resource_connections={
            "rum_applications": ["_application_id"],
        },
        deep_diff_config={
            "ignore_order": True,
            "exclude_regex_paths": [r".*\['_application_id'\]"],
        },
        skip_resource_mapping=True,
    )
    # Additional RUMRetentionFilters specific attributes
    _applications_path = "/api/v2/rum/applications"

    def _subpath(self, resource: Dict) -> str:
        if resource.get("type") == "exclusion_filters":
            return "/retention_filters/exclusion"
        return "/retention_filters"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        apps = (await client.get(self._applications_path))["data"]
        resources: List[Dict] = []
        for app in apps:
            app_id = app["id"]
            # generic retention filters
            resp = await client.get(f"{self._applications_path}/{app_id}/retention_filters")
            for f in resp["data"]:
                f["_application_id"] = app_id
                resources.append(f)
            # exclusion filters (separate sub-path)
            excl = await client.get(f"{self._applications_path}/{app_id}/retention_filters/exclusion")
            for f in excl["data"]:
                f["_application_id"] = app_id
                resources.append(f)
        return resources

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            # The {rf_id} GET is parent-scoped; without the app id we must search.
            # This path is only used by --id-file (not allowlisted for this type),
            # so the cost is acceptable. The normal import flow supplies a full
            # resource from get_resources (passthrough).
            source_client = self.config.source_client
            apps = (await source_client.get(self._applications_path))["data"]
            resource = None
            for app in apps:
                app_id = app["id"]
                resp = await source_client.get(f"{self._applications_path}/{app_id}/retention_filters")
                for f in resp["data"]:
                    if f["id"] == _id:
                        f["_application_id"] = app_id
                        resource = f
                        break
                if resource:
                    break
                excl = await source_client.get(f"{self._applications_path}/{app_id}/retention_filters/exclusion")
                for f in excl["data"]:
                    if f["id"] == _id:
                        f["_application_id"] = app_id
                        resource = f
                        break
                if resource:
                    break
            if resource is None:
                raise Exception(f"rum_retention_filter {_id} not found in any application")

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        app_id = resource.pop("_application_id", None)
        # create data has no id (server-assigned)
        resource.pop("id", None)
        subpath = self._subpath(resource)
        payload = {"data": resource}
        resp = await destination_client.post(f"{self._applications_path}/{app_id}{subpath}", payload)
        data = resp["data"]
        # re-attach so state.destination can build future update/delete URLs
        data["_application_id"] = app_id
        return _id, data

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        app_id = resource.pop("_application_id", None)
        destination_state = self.config.state.destination[self.resource_type][_id]
        destination_id = destination_state["id"]
        # prefer the destination app id recorded at create time; fall back to the
        # (already-remapped) resource value if state lacks it
        dest_app_id = destination_state.get("_application_id", app_id)
        resource["id"] = destination_id
        subpath = self._subpath(resource)
        payload = {"data": resource}
        resp = await destination_client.patch(
            f"{self._applications_path}/{dest_app_id}{subpath}/{destination_id}",
            payload,
        )
        data = resp["data"]
        data["_application_id"] = dest_app_id
        return _id, data

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_state = self.config.state.destination[self.resource_type][_id]
        destination_id = destination_state["id"]
        dest_app_id = destination_state["_application_id"]
        subpath = self._subpath(destination_state)
        await destination_client.delete(f"{self._applications_path}/{dest_app_id}{subpath}/{destination_id}")
