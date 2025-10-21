# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from typing import Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.resource_utils import SkipResource


class MetricsMetadata(BaseResource):
    resource_type = "metrics_metadata"
    resource_config = ResourceConfig(
        base_path="/api/v1/metrics",
        excluded_attributes=["integration"],
    )
    # Additional MetricsMetadata specific attributes
    metrics_get_path = "/api/v2/metrics"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        params = {
            "filter[configured]": "false",
            "window[seconds]": 14 * 86400,  # 14 days
        }
        resp = await client.get(self.metrics_get_path, params=params)

        return resp["data"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        source_client = self.config.source_client
        metric_name = _id or resource["id"]

        resource = await source_client.get(self.resource_config.base_path + f"/{metric_name}")
        if all(value is None for value in resource.values()):
            raise SkipResource(metric_name, self.resource_type, "Metric has no metadata.")

        return metric_name, resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        return await self.update_resource(_id, resource)

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resp = await destination_client.put(self.resource_config.base_path + f"/{_id}", resource)

        return _id, resp

    async def delete_resource(self, _id: str) -> None:
        pass
