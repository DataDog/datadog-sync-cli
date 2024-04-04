# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple
from datetime import datetime, timedelta
from dateutil.parser import parse

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
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
    )
    # Additional DowntimeSchedules specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return resp.get("data", [])

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = await source_client.get(self.resource_config.base_path + f"/{_id}")

        if resource["attributes"].get("canceled"):
            raise SkipResource(_id, self.resource_type, "Downtime is canceled.")

        return str(resource["id"]), resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        if _id not in self.resource_config.destination_resources:
            schedule = resource["attributes"].get("schedule")
            if schedule and "start" in schedule:
                current_time = datetime.utcnow()
                t = parse(schedule["start"])
                if t.timestamp() <= current_time.timestamp():
                    current_time = current_time + timedelta(seconds=60)
                    if getattr(current_time, "tzinfo", None) is not None:
                        new_time = current_time.isoformat()
                    else:
                        new_time = "{}Z".format(current_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3])
                    schedule["start"] = new_time
        else:
            # If start or end times of the resource are in the past, we set to the current destination `start` and `end`
            # this is to avoid unnecessary diff outputs
            if resource["attributes"].get("schedule"):
                one_time_source = resource["attributes"].get("schedule")
                one_time_created = self.resource_config.destination_resources[_id]["attributes"].get("schedule")
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
        resource["id"] = self.resource_config.destination_resources[_id]["id"]
        payload = {"data": resource}
        resp = await destination_client.patch(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}",
            payload,
        )

        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(DowntimeSchedules, self).connect_id(key, r_obj, resource_to_connect)
