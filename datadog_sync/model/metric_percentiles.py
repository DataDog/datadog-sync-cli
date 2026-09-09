# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from typing import Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.resource_utils import (
    FAILURE_CLASS_DESTINATION_METRIC_MISSING,
    CustomClientHTTPError,
    SkipResource,
)


# The bulk toggle endpoints always return 200, even for a metric they decline to
# touch (wrong summary_aggr source, unresolvable name, etc.) - those come back in
# the response body's "unsuccessful" list with no per-metric reason. This failure
# class distinguishes that case from FAILURE_CLASS_DESTINATION_METRIC_MISSING.
FAILURE_CLASS_DESTINATION_METRIC_NOT_CONFIGURABLE = "destination_metric_not_configurable"

# /api/v2/metrics window[seconds] and page[size] limits (governance app).
_WINDOW_SECONDS_14D = 14 * 86400
_PAGE_SIZE = 10000


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
    metrics_list_path = "/api/v2/metrics"
    enable_percentiles_path = "/metric/distribution/summary_aggr/percentiles/enable"
    disable_percentiles_path = "/metric/distribution/summary_aggr/percentiles/disable"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        # The legacy /metric/distribution/list_summaries endpoint (mcnulty) leaks
        # internal summary_aggr fields (e.g. summary_aggr.key) that we never used -
        # include_percentiles is the only field this resource actually needs. The
        # public governance API (/api/v2/metrics) doesn't attach include_percentiles
        # to the response for metrics whose summary_aggr aggr_mode is still at its
        # unconfigured default, but its filter[include_percentiles] facet queries the
        # raw stored boolean directly and isn't affected by that gap. So we recover
        # the same information via list membership: one call per boolean value.
        metrics: Dict[str, Dict] = {}
        for include_percentiles in (True, False):
            cursor = None
            while True:
                params = {
                    "filter[metric_type]": "distribution",
                    "filter[include_percentiles]": "true" if include_percentiles else "false",
                    "window[seconds]": _WINDOW_SECONDS_14D,
                    "page[size]": _PAGE_SIZE,
                }
                if cursor is not None:
                    params["page[cursor]"] = cursor

                resp = await client.get(self.metrics_list_path, params=params)
                for item in resp["data"]:
                    metrics[item["id"]] = {
                        "metric_name": item["id"],
                        "include_percentiles": include_percentiles,
                    }

                cursor = (resp.get("meta") or {}).get("pagination", {}).get("next_cursor")
                if not cursor:
                    break

        return list(metrics.values())

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
        path = self.enable_percentiles_path if resource.get("include_percentiles") else self.disable_percentiles_path
        operation = "percentiles_enable" if resource.get("include_percentiles") else "percentiles_disable"
        try:
            resp = await destination_client.patch(path, {"metric_names": [_id]})
        except CustomClientHTTPError as e:
            if _is_metric_not_found_error(e):
                raise SkipResource(
                    _id,
                    self.resource_type,
                    "Metric not present on destination; percentiles cannot attach.",
                    failure_class=FAILURE_CLASS_DESTINATION_METRIC_MISSING,
                    reason=FAILURE_CLASS_DESTINATION_METRIC_MISSING,
                    outcome_details={"metric_name": _id, "operation": operation},
                )
            raise

        # A 200 here doesn't mean the toggle actually applied - the destination
        # silently declines metrics it can't configure (e.g. a summary_aggr source
        # that isn't percentile-configurable) by putting them in "unsuccessful"
        # instead of raising. Without this check that no-op reads as a success.
        if _id in (resp or {}).get("unsuccessful", []):
            raise SkipResource(
                _id,
                self.resource_type,
                "Destination declined to toggle percentiles for this metric "
                "(ineligible summary_aggr source or unresolved metric name).",
                failure_class=FAILURE_CLASS_DESTINATION_METRIC_NOT_CONFIGURABLE,
                reason=FAILURE_CLASS_DESTINATION_METRIC_NOT_CONFIGURABLE,
                outcome_details={"metric_name": _id, "operation": operation},
            )

        return _id, resource

    async def delete_resource(self, _id: str) -> None:
        pass
