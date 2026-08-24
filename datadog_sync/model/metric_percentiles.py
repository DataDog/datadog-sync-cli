# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from typing import Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource


def _error_body(error: CustomClientHTTPError) -> str:
    return (error.response_body or "").lower()


def _is_metric_not_found_error(error: CustomClientHTTPError) -> bool:
    return error.status_code in (400, 404, 500) and "metric not found" in _error_body(error)


class MetricPercentiles(BaseResource):
    resource_type = "metric_percentiles"
    resource_config = ResourceConfig(
        base_path="/metric/distribution/summary_aggr",
        excluded_attributes=["key"],
        skip_resource_mapping=True,
    )
    # Additional MetricPercentiles specific attributes
    metrics_summaries_get_path = "/metric/distribution/list_summaries"
    metrics_metadata_get_path = "/api/v1/metrics"
    enable_percentiles_path = "/metric/distribution/summary_aggr/percentiles/enable"
    disable_percentiles_path = "/metric/distribution/summary_aggr/percentiles/disable"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        params = {
            "window": 14 * 86400,  # 14 days
        }
        resp = await client.get(self.metrics_summaries_get_path, params=params)

        return resp

    async def import_resource(self, _: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        # The bulk-toggle endpoints only accept metric_names; group_by, aggr_mode,
        # summary_type, groups_negated, and source cannot be applied via this resource.
        # Narrow the stored shape so diffs only reflect what we can actually sync.
        metric_name = resource["metric_name"]
        return metric_name, {
            "metric": metric_name,
            "include_percentiles": bool(resource.get("include_percentiles")),
        }

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        return await self.update_resource(_id, resource)

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        # The destination write goes through one of the two bulk-toggle endpoints:
        #   PATCH /metric/distribution/summary_aggr/percentiles/enable
        #   PATCH /metric/distribution/summary_aggr/percentiles/disable
        # Both take {"metric_names": [...]}. Legacy sync-cli POSTed to
        # /metric/distribution/summary_aggr, which is not a registered route and
        # returns 403 empty-body at the OBO auth layer.
        destination_client = self.config.destination_client
        try:
            await destination_client.get(self.metrics_metadata_get_path + f"/{_id}")
        except CustomClientHTTPError as e:
            if e.status_code == 404:
                raise SkipResource(
                    _id,
                    self.resource_type,
                    "Metric not present on destination; percentiles cannot attach.",
                )
            raise

        path = self.enable_percentiles_path if resource.get("include_percentiles") else self.disable_percentiles_path
        try:
            await destination_client.patch(path, {"metric_names": [_id]})
        except CustomClientHTTPError as e:
            if _is_metric_not_found_error(e):
                raise SkipResource(
                    _id,
                    self.resource_type,
                    "Metric not present on destination; percentiles cannot attach.",
                )
            raise

        return _id, resource

    async def delete_resource(self, _id: str) -> None:
        pass
