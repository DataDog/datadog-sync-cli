# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast
from asyncio import sleep

from re import match

from datadog_sync.constants import LOGGER_NAME, Metrics
from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import DEFAULT_TAGS, SkipResource, check_diff

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient

_log = logging.getLogger(LOGGER_NAME)


def _summarize_diff_keys(diff) -> List[str]:
    """Extract high-level change locations from a DeepDiff object.

    Returns a de-duplicated list of top-level field names that changed
    (e.g. ["is_enabled", "filter"]). Used to log which fields would have
    been written when we're skipping an unsupported update.
    """
    if not diff:
        return []
    keys = set()
    for change_type in ("values_changed", "type_changes", "iterable_item_added",
                        "iterable_item_removed", "dictionary_item_added",
                        "dictionary_item_removed"):
        for path in diff.get(change_type, {}) or {}:
            # DeepDiff paths look like "root['is_enabled']" or "root['filter']['query']"
            # Extract the first bracketed segment.
            m = match(r"root\['([^']+)'\]", str(path))
            if m:
                keys.add(m.group(1))
    return sorted(keys)


class LogsPipelines(BaseResource):
    resource_type = "logs_pipelines"
    resource_config = ResourceConfig(
        concurrent=False,
        base_path="/api/v1/logs/config/pipelines",
        excluded_attributes=["id", "type", "__datadog_sync_invalid", "meta"],
        non_nullable_attr=[
            "tags",
            "description",
        ],
        null_values={"tags": [[]], "description": [""]},
        skip_resource_mapping=True,
    )
    # Additional LogsPipelines specific attributes
    destination_integration_pipelines: Dict[str, Dict] = dict()
    logs_intake_subdomain = "http-intake.logs"
    logs_intake_path = "/api/v2/logs"
    logs_intg_pipeline_source_re = r"source:((?P<source>\S+)$|\((?P<source_or>\S+) OR.*\))"
    invalid_integration_pipelines = {"cron", "ufw"}

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return resp

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = await source_client.get(self.resource_config.base_path + f"/{_id}")

        resource = cast(dict, resource)
        if resource["is_read_only"]:
            resource["name"] = resource["name"].lower()
            resource["processors"] = []

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        self.destination_integration_pipelines = await self.get_destination_integration_pipelines()

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client

        if not resource["is_read_only"]:
            resp = await destination_client.post(self.resource_config.base_path, resource)

            return _id, resp

        if resource["name"] not in self.destination_integration_pipelines:
            if resource["name"] in self.invalid_integration_pipelines:
                # We do not create invalid integration pipelines but rather insert
                # the pipeline in local state with additional metadata to indicate
                # that it is invalid.
                # Upstream resources will selectively drop references to the resource based on the field.
                resource["__datadog_sync_invalid"] = True
                return _id, resource
            # Extract the source from the query
            source = self.extract_source_from_query(resource.get("filter", {}).get("query"))
            if not source:
                raise Exception(f"Source not found in the query for integration pipeline '{resource['name']}'")
            payload = {
                "ddsource": source,
                "ddtags": ",".join(DEFAULT_TAGS),
                "message": f"[datadog-sync-cli] Triggering creation of '{resource['name']}' integration pipeline",
            }

            # Submit a log to the logs intake API to trigger the creation of the integration pipeline
            override_url = self.config.destination_logs_intake_url
            if override_url:
                await destination_client.post_unauthenticated(override_url, payload)
            else:
                subdomain = f"{self.logs_intake_subdomain}.{destination_client.url_object.subdomain}"
                if destination_client.url_object.subdomain == "api":
                    subdomain = self.logs_intake_subdomain
                elif destination_client.url_object.subdomain.startswith("api."):
                    subdomain = f"{self.logs_intake_subdomain}.{destination_client.url_object.subdomain[4:]}"
                await destination_client.post(self.logs_intake_path, payload, subdomain=subdomain)

            created = False
            for _ in range(12):
                updated_pipelines = await self.get_destination_integration_pipelines()
                if resource["name"] in updated_pipelines:
                    self.destination_integration_pipelines = updated_pipelines
                    created = True
                    break
                else:
                    await sleep(5)

            if not created:
                raise Exception(
                    f"Integration pipeline '{resource['name']}' is not created after x seconds. "
                    "It will be rechecked in the next sync."
                )

        self.config.state.destination[self.resource_type][_id] = self.destination_integration_pipelines[
            resource["name"]
        ]

        diff = check_diff(self.resource_config, self.destination_integration_pipelines[resource["name"]], resource)
        if diff:
            # See _handle_read_only_diff for why we do not PUT here.
            # In create_resource, we return the destination's state instead of
            # raising so first-sync callers still get a resource dict back and
            # the ResourcesHandler accounts this as a success (the pipeline is
            # now in the state map). Subsequent syncs that re-check the diff
            # go through update_resource, which raises SkipResource so the
            # handler buckets it correctly.
            await self._handle_read_only_diff(_id, resource, diff)

        return _id, self.config.state.destination[self.resource_type][_id]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        current_destination_resource = self.config.state.destination[self.resource_type][_id]
        if current_destination_resource.get("__datadog_sync_invalid"):
            # We do not update invalid integration pipelines.
            # We only update the local state with the new payload to avoid diffs.
            current_destination_resource.update(resource)
            current_destination_resource["__datadog_sync_invalid"] = True
            return _id, current_destination_resource

        # Integration pipelines (is_read_only=True on the destination) cannot be
        # updated via the public API -- PUT /api/v1/logs/config/pipelines/<id>
        # does not route integration-pipeline IDs and returns a 404 that falls
        # through to the edge web tier as HTML. Raise SkipResource so the
        # ResourcesHandler counts this as a legitimate skip rather than a
        # success with a silently-failed PUT, or a failure that retries on the
        # next dispatch. Fires the same WARN + metric as create_resource.
        if current_destination_resource.get("is_read_only") or resource.get("is_read_only"):
            diff = check_diff(self.resource_config, current_destination_resource, resource)
            if diff:
                await self._handle_read_only_diff(_id, resource, diff)
            raise SkipResource(
                _id,
                self.resource_type,
                "Integration pipeline is auto-managed by Datadog and cannot be modified via the public API.",
            )

        destination_client = self.config.destination_client
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            resource,
        )

        if resp["is_read_only"]:
            resource.update(resp)
            resource["name"] = resource["name"].lower()

        return _id, resp

    async def _handle_read_only_diff(self, _id: str, resource: Dict, diff) -> None:
        """Log + metric for the "integration pipeline diverges but cannot be
        updated via the public API" class.

        Integration pipelines are auto-managed by Datadog
        (https://docs.datadoghq.com/logs/log_configuration/pipelines/#integration-pipelines).
        The PUT /api/v1/logs/config/pipelines/<id> endpoint only routes IDs in
        the custom-pipelines namespace, so any PUT against an integration
        pipeline returns 404 -- and the 404 falls through to the edge web
        tier producing an HTML body rather than JSON. We accept the
        destination's state as authoritative, log a WARNING with a diff
        summary so the divergence stays visible, and emit a distinct
        action-metric so operators can dashboard the class.

        Called from both create_resource (first-sync path) and update_resource
        (subsequent-sync path) so all writes to read-only pipelines are
        handled consistently.
        """
        diff_keys = _summarize_diff_keys(diff)
        _log.warning(
            "logs_pipelines: integration pipeline %r (source id=%s) diverges from destination; "
            "skipping update -- integration pipelines are auto-managed by Datadog and cannot be "
            "modified via the public API. Diff keys: %s",
            resource.get("name"),
            _id,
            diff_keys or "[unknown]",
        )
        try:
            await self.config.destination_client.send_metric(
                Metrics.ACTION.value,
                tags=[
                    f"resource_type:{self.resource_type}",
                    "action_type:sync",
                    "status:skipped",
                    "action_sub_type:integration_diff_skipped",
                    f"pipeline_name:{resource.get('name', 'unknown')}",
                ],
            )
        except Exception as e:
            # Never let metric emission block the return path.
            self.config.logger.debug(
                f"logs_pipelines: failed to emit integration_diff_skipped metric: {e}"
            )

    async def delete_resource(self, _id: str) -> None:
        if self.config.state.destination[self.resource_type][_id]["is_read_only"]:
            self.config.logger.warning("Integration pipelines cannot deleted. Removing resource from config only.")
        else:
            destination_client = self.config.destination_client
            await destination_client.delete(
                self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
            )

    async def get_destination_integration_pipelines(self):
        destination_integration_pipelines_obj = {}
        destination_client = self.config.destination_client

        resp = await self.get_resources(destination_client)
        for pipeline in resp:
            if pipeline["is_read_only"]:
                # Normalize name for the integration pipeline
                pipeline["name"] = pipeline["name"].lower()
                pipeline["processors"] = []

                destination_integration_pipelines_obj[pipeline["name"]] = pipeline

        return destination_integration_pipelines_obj

    @staticmethod
    def extract_source_from_query(query: str | None) -> str | None:
        """Extract the first source from the query."""
        if not query:
            return

        source = match(LogsPipelines.logs_intg_pipeline_source_re, query)
        if source:
            return source.group("source") or source.group("source_or")
