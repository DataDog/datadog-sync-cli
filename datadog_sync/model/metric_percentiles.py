# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from typing import Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.resource_utils import SkipResource


class MetricPercentiles(BaseResource):
    resource_type = "metric_percentiles"
    resource_config = ResourceConfig(
        base_path="/metric/distribution/summary_aggr",
        excluded_attributes=["key"],
    )
    # Additional MetricPercentiles specific attributes
    metrics_summaries_get_path = "/metric/distribution/list_summaries"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.metrics_summaries_get_path)

        return resp

    async def import_resource(self, _: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        # This resource is not a dependency of any other resource. Hence it is
        # safe to ignore the _id parameter and rely solely on resource.

        if not resource.get("include_percentiles"):
            raise SkipResource(resource["metric_name"], self.resource_type, "Metric does not have percentiles config")

        # metric_name => metric
        metric_name = resource.pop("metric_name")
        resource["metric"] = metric_name

        return metric_name, resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        return await self.update_resource(_id, resource)

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        await destination_client.post(self.resource_config.base_path, resource)

        return _id, resource

    async def delete_resource(self, _id: str) -> None:
        pass

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass
