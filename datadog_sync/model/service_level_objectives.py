# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from __future__ import annotations
import logging
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.constants import LOGGER_NAME
from datadog_sync.utils.base_resource import BaseResource, ResourceConfig, ResourceConnectionResult, TaggingConfig
from datadog_sync.utils.resource_utils import (
    FAILURE_CLASS_DESTINATION_METRIC_MISSING,
    CustomClientHTTPError,
    SkipResource,
    find_attr,
)

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


log = logging.getLogger(LOGGER_NAME)

# Matches a metric query term: ``<aggr>:<metric_name>{``. The aggregation
# (sum/avg/min/max/count/last/...) is followed by a colon, the metric name
# (charset [a-zA-Z0-9_.]), optional whitespace, and the opening brace of the
# scope filter. Arithmetic (``-``, ``+``, ``*``, ``/``) and post-query
# modifiers (``.as_count()``, ``.rollup(...)``) sit outside the match, so a
# single finditer pass over a numerator/denominator string yields every
# distinct metric referenced by the SLO.
_METRIC_NAME_RE = re.compile(r"\b[a-zA-Z]+\s*:\s*([a-zA-Z0-9_.]+)\s*\{")


def _extract_metric_names(resource: Dict) -> List[str]:
    """Return the distinct metric names referenced by a metric SLO's queries.

    A metric SLO validates its numerator/denominator queries against the
    destination org's metric catalog; every metric referenced must exist there
    before the SLO can be created or updated. The names are pulled from both
    the v1 ``query`` (numerator/denominator) and the v2
    ``sli_specification.count.queries`` shapes so the destination can probe
    each one for existence regardless of which representation the source
    carried.
    """
    names: List[str] = []
    seen: set = set()

    def _scan(query_str: object) -> None:
        if not isinstance(query_str, str):
            return
        for m in _METRIC_NAME_RE.finditer(query_str):
            name = m.group(1)
            if name and name not in seen:
                seen.add(name)
                names.append(name)

    query = resource.get("query")
    if isinstance(query, dict):
        _scan(query.get("numerator", ""))
        _scan(query.get("denominator", ""))

    sli_spec = resource.get("sli_specification")
    if isinstance(sli_spec, dict):
        count_spec = sli_spec.get("count")
        if isinstance(count_spec, dict):
            for q in count_spec.get("queries", []) or []:
                if isinstance(q, dict):
                    _scan(q.get("query", ""))

    return names


class ServiceLevelObjectives(BaseResource):
    resource_type = "service_level_objectives"
    resource_config = ResourceConfig(
        resource_connections={"monitors": ["monitor_ids"], "synthetics_tests": []},
        base_path="/api/v1/slo",
        excluded_attributes=["creator", "id", "created_at", "modified_at"],
        tagging_config=TaggingConfig(path="tags"),
        skip_resource_mapping=True,
    )
    # Additional ServiceLevelObjectives specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return resp["data"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (await source_client.get(self.resource_config.base_path + f"/{_id}"))["data"]
        resource = cast(dict, resource)
        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        if resource.get("type") == "metric":
            query = resource.get("query")
            if not isinstance(query, dict):
                return
            for field in ("numerator", "denominator"):
                query_str = query.get(field, "")
                if query_str and ".as_count()" not in query_str:
                    raise SkipResource(
                        _id,
                        self.resource_type,
                        f"Deprecated resource configuration: Metric SLO query '{field}' "
                        "is missing the .as_count() modifier. Update the source SLO query before syncing.",
                    )

    async def pre_apply_hook(self) -> None:
        pass

    async def _probe_destination_metrics_or_skip(self, _id: str, resource: Dict, operation: str) -> None:
        """Probe the destination for every metric a metric SLO references.

        A metric SLO's numerator/denominator queries reference metrics that
        must exist on the destination org before the SLO can be created or
        updated. If the destination rejects the write because a referenced
        metric is absent, sync-cli previously failed silently — no per-resource
        error, no typed outcome — leaving the SLO absent from the destination
        bucket with no log trail (see the metric-SLO silent-failure report).

        Probe each referenced metric via GET /api/v1/metrics/{name} (the same
        v1 path metrics_metadata uses). On 404, collect the missing names and
        raise a typed SkipResource carrying all of them in ``metric_names``
        (comma-joined) so callers can materialize every missing metric before
        retrying the SLO. Other probe errors
        propagate to the existing retry layer.

        Only ``type=metric`` SLOs are probed; monitor and time-slice SLOs have
        no metric dependencies. Runs in the create/update write paths (after
        the diff check) so no-diff syncs issue no probe calls.
        """
        if resource.get("type") != "metric":
            return

        metric_names = _extract_metric_names(resource)
        if not metric_names:
            return

        destination_client = self.config.destination_client
        missing: List[str] = []
        for name in metric_names:
            try:
                await destination_client.get(f"/api/v1/metrics/{name}")
            except CustomClientHTTPError as e:
                if e.status_code == 404:
                    log.debug(f"[slo - {_id}] referenced metric {name!r} not present on destination")
                    missing.append(name)
                    continue
                raise

        if missing:
            raise SkipResource(
                _id,
                self.resource_type,
                f"Referenced metric(s) not present on destination: {', '.join(missing)}",
                failure_class=FAILURE_CLASS_DESTINATION_METRIC_MISSING,
                reason=FAILURE_CLASS_DESTINATION_METRIC_MISSING,
                outcome_details={"metric_names": ",".join(missing), "operation": operation},
            )

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        await self._probe_destination_metrics_or_skip(_id, resource, "slo_create")
        destination_client = self.config.destination_client
        resp = await destination_client.post(self.resource_config.base_path, resource)

        return _id, resp["data"][0]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        await self._probe_destination_metrics_or_skip(_id, resource, "slo_update")
        destination_client = self.config.destination_client
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            resource,
        )

        return _id, resp["data"][0]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            params={"force": "true"},
        )

    def connect_resources(self, _id: str, resource: Dict) -> ResourceConnectionResult:
        if not self.resource_config.resource_connections:
            return ResourceConnectionResult()

        failed_connections_dict = defaultdict(list)
        stale_connections_dict = defaultdict(list)
        c = find_attr(
            "monitor_ids",
            "monitors",
            resource,
            lambda key, r_obj, rtc: self._connect_monitor_id_or_classify_stale(key, r_obj, stale_connections_dict),
        )
        if c:
            failed_connections_dict["monitors"].extend(c)

        self._raise_connection_error_if_any(_id, failed_connections_dict)
        self._raise_stale_dependency_skip(_id, stale_connections_dict)

        return ResourceConnectionResult()

    def _connect_monitor_id_or_classify_stale(
        self,
        key: str,
        r_obj: Dict,
        stale_connections_dict: Dict[str, List[str]],
    ) -> Optional[List[str]]:
        monitor_ids = r_obj.get(key)
        if not monitor_ids:
            return None

        failed_connections = []
        for i, obj in enumerate(monitor_ids):
            _id = str(obj)
            resolved = self._destination_monitor_id_for_slo(_id)
            if resolved is not None:
                r_obj[key][i] = type(obj)(resolved)
                continue
            if self._source_has_slo_monitor_dependency(_id):
                failed_connections.append(_id)
            else:
                stale_connections_dict["monitors"].append(_id)
        return failed_connections or None

    def _destination_monitor_id_for_slo(self, _id: str) -> Optional[str]:
        monitors = self.config.state.destination["monitors"]
        if _id in monitors:
            return monitors[_id]["id"]

        self.config.state.ensure_resource_loaded("monitors", _id)
        monitors = self.config.state.destination["monitors"]
        if _id in monitors:
            return monitors[_id]["id"]

        self.config.state.ensure_resource_type_loaded("synthetics_tests")
        synthetics_tests = self.config.state.destination["synthetics_tests"]
        for k, v in synthetics_tests.items():
            if k.endswith("#" + _id):
                return v["monitor_id"]
        return None

    def _source_has_slo_monitor_dependency(self, _id: str) -> bool:
        if _id in self.config.state.source["monitors"]:
            return True
        synthetics_tests = self.config.state.source["synthetics_tests"]
        return any(k.endswith("#" + _id) for k in synthetics_tests)

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        monitors = self.config.state.destination["monitors"]

        failed_connections = []
        for i, obj in enumerate(r_obj[key]):
            _id = str(obj)
            # Check if resource exists in monitors
            if _id in monitors:
                r_obj[key][i] = monitors[_id]["id"]
                continue
            # Fall back on Synthetics and check — bulk-load the type first
            self.config.state.ensure_resource_type_loaded("synthetics_tests")
            synthetics_tests = self.config.state.destination["synthetics_tests"]
            found = False
            for k, v in synthetics_tests.items():
                if k.endswith(_id):
                    r_obj[key][i] = v["monitor_id"]
                    found = True
                    break
            if not found:
                failed_connections.append(_id)
        return failed_connections

    def extract_source_ids(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        # Mirror of connect_id -- keep in sync when connect_id changes.
        # connect_id checks each monitor_id against monitors destination state first,
        # then falls back to synthetics_tests using suffix match on composite keys
        # ('{public_id}#{monitor_id}'). For source discovery, exclude IDs that are
        # synthetics monitor IDs to prevent false ("monitors", id) misses.
        if key != "monitor_ids":
            return super().extract_source_ids(key, r_obj, resource_to_connect)
        ids = [str(obj) for obj in r_obj[key]]
        if resource_to_connect == "monitors":
            synthetics = self.config.state.source["synthetics_tests"]
            return [_id for _id in ids if not any(k.endswith("#" + _id) for k in synthetics)]
        return super().extract_source_ids(key, r_obj, resource_to_connect)
