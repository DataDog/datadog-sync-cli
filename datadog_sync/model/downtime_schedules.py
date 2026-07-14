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
        resp = await client.paginated_request(client.get)(
            self.resource_config.base_path,
            pagination_config=self.pagination_config,
        )

        return resp

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = await source_client.get(self.resource_config.base_path + f"/{_id}")

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
            floor = now + timedelta(seconds=60)

            # Rewrite past `start` forward. Existing behavior; parse/format
            # hardened to UTC-aware so `.timestamp()` is correct on non-UTC hosts.
            start_raw = schedule.get("start")
            end_raw = schedule.get("end")
            start_dt_source = self._parse_utc(start_raw) if start_raw else None
            end_dt_source = self._parse_utc(end_raw) if end_raw else None

            start_dt = start_dt_source
            if start_dt_source is not None and start_dt_source <= now:
                start_dt = floor
                schedule["start"] = self._iso_utc(start_dt)

            # Rewrite past `end` forward while preserving both invariants:
            #   1. `end > start` — API 400s on inverted windows.
            #   2. Original `end - start` duration when the source window is
            #      entirely in the past — customer scheduled a 2h maintenance;
            #      shrinking to 60s on destination would be a semantic surprise.
            # Prior code did not touch `end`, so one-off downtimes with both
            # `start` and `end` in the past 400'd at POST.
            if end_dt_source is not None and end_dt_source <= now:
                if start_dt is not None:
                    if start_dt_source is not None:
                        duration = end_dt_source - start_dt_source
                        if duration <= timedelta(0):
                            duration = timedelta(seconds=60)
                        end_dt = start_dt + duration
                    else:
                        end_dt = start_dt + timedelta(seconds=60)
                    end_dt = max(end_dt, floor)
                else:
                    end_dt = floor
                schedule["end"] = self._iso_utc(end_dt)
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
