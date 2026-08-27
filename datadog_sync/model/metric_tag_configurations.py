# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from copy import deepcopy
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import (
    FAILURE_CLASS_DESTINATION_METRIC_MISSING,
    CustomClientHTTPError,
    SkipResource,
)

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


def _error_body(error: CustomClientHTTPError) -> str:
    return (error.response_body or "").lower()


def _is_missing_metric_error(error: CustomClientHTTPError) -> bool:
    return error.status_code == 400 and "metric that does not exist" in _error_body(error)


def _is_existing_tag_config_conflict(error: CustomClientHTTPError) -> bool:
    return error.status_code == 409 and "patch" in _error_body(error)


def _is_missing_metadata_type_error(error: CustomClientHTTPError) -> bool:
    body = _error_body(error)
    return error.status_code == 400 and "metadata type must be set prior to configuring tags" in body


def _metric_type_from_tag_configuration(resource: Dict) -> Optional[str]:
    metric_type = resource.get("attributes", {}).get("metric_type")
    if not isinstance(metric_type, str):
        return None
    metric_type = metric_type.strip().lower()
    if not metric_type or metric_type == "distribution":
        return None
    return metric_type


class MetricTagConfigurations(BaseResource):
    resource_type = "metric_tag_configurations"
    resource_config = ResourceConfig(
        base_path="/api/v2/metrics",
        excluded_attributes=["attributes.created_at", "attributes.modified_at"],
        resource_mapping_key="id",
    )
    # Additional MetricTagConfigurations specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path, params={"filter[configured]": "true"})

        return resp["data"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (await source_client.get(self.resource_config.base_path + f"/{_id}/tags"))["data"]

        resource = cast(dict, resource)
        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def _set_destination_metric_metadata_type(self, _id: str, resource: Dict) -> bool:
        metric_type = _metric_type_from_tag_configuration(resource)
        if metric_type is None:
            return False

        await self.config.destination_client.put(f"/api/v1/metrics/{_id}", {"type": metric_type})
        return True

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        if _id in self._existing_resources_map:
            self.config.state.destination[self.resource_type][_id] = self._existing_resources_map[_id]
            return await self.update_resource(_id, resource)

        destination_client = self.config.destination_client
        payload = {"data": resource}
        path = self.resource_config.base_path + f"/{self.config.state.source[self.resource_type][_id]['id']}/tags"
        try:
            resp = await destination_client.post(path, payload)
        except CustomClientHTTPError as e:
            if _is_missing_metric_error(e):
                raise SkipResource(
                    _id,
                    self.resource_type,
                    "Metric not present on destination; tag configuration cannot attach.",
                    failure_class=FAILURE_CLASS_DESTINATION_METRIC_MISSING,
                    reason=FAILURE_CLASS_DESTINATION_METRIC_MISSING,
                    outcome_details={"metric_name": _id, "operation": "tag_configuration_create"},
                )
            if _is_missing_metadata_type_error(e) and await self._set_destination_metric_metadata_type(_id, resource):
                try:
                    resp = await destination_client.post(path, payload)
                except CustomClientHTTPError as retry_e:
                    if not _is_existing_tag_config_conflict(retry_e):
                        raise

                    existing = await destination_client.get(path)
                    self.config.state.destination[self.resource_type][_id] = existing["data"]
                    return await self.update_resource(_id, resource)
                return _id, resp["data"]
            if not _is_existing_tag_config_conflict(e):
                raise

            existing = await destination_client.get(path)
            self.config.state.destination[self.resource_type][_id] = existing["data"]
            return await self.update_resource(_id, resource)

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        update_resource = deepcopy(resource)
        if "attributes" in update_resource:
            update_resource["attributes"].pop("metric_type", None)
        payload = {"data": update_resource}
        path = self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}/tags"
        try:
            resp = await destination_client.patch(path, payload)
        except CustomClientHTTPError as e:
            if _is_missing_metric_error(e):
                raise SkipResource(
                    _id,
                    self.resource_type,
                    "Metric not present on destination; tag configuration cannot attach.",
                    failure_class=FAILURE_CLASS_DESTINATION_METRIC_MISSING,
                    reason=FAILURE_CLASS_DESTINATION_METRIC_MISSING,
                    outcome_details={"metric_name": _id, "operation": "tag_configuration_update"},
                )
            if _is_missing_metadata_type_error(e) and await self._set_destination_metric_metadata_type(_id, resource):
                resp = await destination_client.patch(path, payload)
                return _id, resp["data"]
            raise

        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}/tags"
        )
