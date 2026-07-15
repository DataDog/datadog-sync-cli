# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import logging
from typing import Optional, List, Dict, Tuple

from datadog_sync.constants import LOGGER_NAME
from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource

log = logging.getLogger(LOGGER_NAME)


class MetricsMetadata(BaseResource):
    resource_type = "metrics_metadata"
    resource_config = ResourceConfig(
        base_path="/api/v1/metrics",
        excluded_attributes=["integration"],
        skip_resource_mapping=True,
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

        # Distribution metrics cannot be written via PUT /api/v1/metrics/{name}
        # — the destination rejects type=distribution with a 400 at the DB
        # layer. Skip early so the run does not burn retries on a request
        # that will never succeed. Skip fires before the destination probe
        # below to avoid a wasted GET.
        #
        # Case-tolerant match: the destination canonicalizes to lowercase but
        # normalize defensively so an upstream drift in the source-side casing
        # cannot silently re-enable the 400 loop.
        metric_type = (resource.get("type") or "").strip().lower()
        if metric_type == "distribution":
            log.debug(f"[metrics_metadata - {_id}] skipping: distribution type not writable via public PUT")
            raise SkipResource(
                _id,
                self.resource_type,
                "distribution type is rejected by the destination metrics_metadata endpoint; "
                "skipping public PUT",
            )

        # metrics_metadata can only attach to a metric that already exists on
        # destination. Legacy dogweb returns 400 {"errors":["error updating metric
        # metadata"]} when the metric is missing; that failure adds ~25-30min of
        # wall-clock per dispatch on DR replicas where large fractions of the
        # source's custom metrics haven't yet been ingested to the destination.
        # Probe existence first via GET /api/v1/metrics/{name} (same base_path
        # sync-cli PUTs to below); skip on 404. Other errors from the probe
        # propagate to the existing retry layer.
        # NOTE: use the v1 path, not self.metrics_get_path (=/api/v2/metrics),
        # because /api/v2/metrics/{name} is not a registered route in dd-source
        # governance (only /api/v2/metrics list + subresources like /tags exist).
        # The v1 handler returns 404 via GetResolvedMetricWithLegacyErrorHandling
        # when the metric is not resolvable in the destination org.
        try:
            await destination_client.get(self.resource_config.base_path + f"/{_id}")
        except CustomClientHTTPError as e:
            if e.status_code == 404:
                log.debug(f"[metrics_metadata - {_id}] skipping: metric not present on destination")
                raise SkipResource(
                    _id,
                    self.resource_type,
                    "Metric not present on destination; metadata cannot attach.",
                )
            raise

        resp = await destination_client.put(self.resource_config.base_path + f"/{_id}", resource)

        return _id, resp

    async def delete_resource(self, _id: str) -> None:
        pass
