# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple
from datetime import datetime, timedelta, timezone
from dateutil.parser import parse

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import PaginationConfig
from datadog_sync.utils.resource_utils import DowntimeSchedulesDateOperator, SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class DowntimeSchedules(BaseResource):
    resource_type = "downtime_schedules"
    resource_config = ResourceConfig(
        resource_connections={"monitors": ["attributes.monitor_identifier.monitor_id"]},
        non_nullable_attr=[],
        base_path="/api/v2/downtime",
        excluded_attributes=[
            "id",
            "attributes.modified",
            "attributes.created",
            "attributes.status",
            "attributes.canceled",
            "relationships",
            "attributes.schedule.current_downtime",
        ],
        deep_diff_config={
            "ignore_order": True,
            "custom_operators": [DowntimeSchedulesDateOperator()],
        },
        skip_resource_mapping=True,
    )
    pagination_config = PaginationConfig(
        page_size=100,
        page_size_param="page[limit]",
        page_number_param="page[offset]",
        page_number_func=lambda idx, page_size, page_number: page_number + page_size,
        remaining_func=lambda *args: 1,
    )
    # Additional DowntimeSchedules specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        # `include=created_by` populates `relationships.created_by.data.id` on
        # each downtime. Downstream consumers (e.g. HAMR managed-sync's OBO
        # grouper) key on that field to route the resource under its creator's
        # identity; without the include, the LIST response omits `relationships`
        # entirely and downstream code falls back to a service-account identity.
        resp = await client.paginated_request(client.get)(
            self.resource_config.base_path,
            pagination_config=self.pagination_config,
            params={"include": "created_by"},
        )

        return resp

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (
                await source_client.get(
                    self.resource_config.base_path + f"/{_id}",
                    params={"include": "created_by"},
                )
            )["data"]

        if resource["attributes"].get("canceled"):
            raise SkipResource(resource["id"], self.resource_type, "Downtime is canceled.")

        return str(resource["id"]), resource

    @staticmethod
    def _parse_utc(value):
        """Parse an ISO timestamp and return a UTC-aware datetime. Naive input
        is assumed UTC (the destination stores schedules in UTC)."""
        parsed = parse(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _iso_utc(dt) -> str:
        return dt.isoformat().replace("+00:00", "Z")

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        if _id not in self.config.state.destination[self.resource_type]:
            schedule = resource["attributes"].get("schedule")
            if not schedule:
                return
            now = datetime.now(timezone.utc)

            # Past `end` means the maintenance window has already closed on the
            # source. Replicating it to the destination would either invent a
            # new customer-visible maintenance (if we shifted `end` forward) or
            # 400 with "Downtime cannot be scheduled in the past". Skip: an
            # ended downtime has nothing left to silence.
            end_raw = schedule.get("end")
            if end_raw:
                end_dt = self._parse_utc(end_raw)
                if end_dt <= now:
                    raise SkipResource(
                        str(_id), self.resource_type,
                        "Downtime end is in the past.",
                    )

            # Rewrite past `start` forward to now+60s. `end` (if present) is
            # left as-is per customer intent — the window may shrink but its
            # original end time is preserved.
            start_raw = schedule.get("start")
            if start_raw:
                start_dt = self._parse_utc(start_raw)
                if start_dt <= now:
                    schedule["start"] = self._iso_utc(now + timedelta(seconds=60))
        else:
            # If start or end times of the resource are in the past, we set to the current destination `start` and `end`
            # this is to avoid unnecessary diff outputs
            if resource["attributes"].get("schedule"):
                one_time_source = resource["attributes"].get("schedule")
                one_time_created = self.config.state.destination[self.resource_type][_id]["attributes"].get("schedule")
                if one_time_created.get("start") and one_time_source.get("start"):
                    start_source = parse(one_time_source["start"])
                    start_created = parse(one_time_created["start"])
                    if start_source.timestamp() < start_created.timestamp():
                        one_time_source["start"] = one_time_created["start"]
                if one_time_created.get("end") and one_time_source.get("end"):
                    start_source = parse(one_time_source["end"])
                    start_created = parse(one_time_created["end"])
                    if start_source.timestamp() < start_created.timestamp():
                        one_time_source["end"] = one_time_created["end"]

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = await destination_client.post(self.resource_config.base_path, payload)

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource["id"] = self.config.state.destination[self.resource_type][_id]["id"]
        payload = {"data": resource}
        resp = await destination_client.patch(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            payload,
        )

        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(DowntimeSchedules, self).connect_id(key, r_obj, resource_to_connect)
