# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from __future__ import annotations
import math
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast
from datetime import datetime

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


RECURRING_TIMES = {
    "days": 86400,
    "weeks": 604800,
    "months": 2592000,
    "years": 31536000,
}


class Downtimes(BaseResource):
    resource_type = "downtimes"
    resource_config = ResourceConfig(
        resource_connections={"monitors": ["monitor_id"]},
        non_nullable_attr=["recurrence.until_date", "recurrence.until_occurrences"],
        base_path="/api/v1/downtime",
        excluded_attributes=[
            "id",
            "uuid",
            "updater_id",
            "created",
            "org_id",
            "modified",
            "creator_id",
            "active",
            "child_id",
        ],
    )
    # Additional Downtimes specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return resp

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = await source_client.get(self.resource_config.base_path + f"/{_id}")

        resource = cast(dict, resource)
        if resource["canceled"]:
            raise SkipResource(resource["id"], self.resource_type, "Downtime is canceled.")
        # Dispose the recurring child downtimes and only retain the parent
        if resource["recurrence"] and resource["parent_id"]:
            raise SkipResource(resource["id"], self.resource_type, "Parent downtime is used for recurring downtimes.")

        return str(resource["id"]), resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        if _id not in self.config.storage.data[self.resource_type].destination:
            current_time = round(datetime.now().timestamp())
            if resource["recurrence"] is None:
                # If the downtime start time is in the past, convert it to now + 1min
                if resource["start"] and resource["start"] <= current_time:
                    resource["start"] = current_time + 60
            else:
                # Calculate the next recurrence `start` time by counting the number of recurrences
                # that has occurred since `start` and round it up
                r_time = RECURRING_TIMES[resource["recurrence"]["type"]]
                r_period = resource["recurrence"]["period"]
                r_interval = r_time * r_period
                if resource["start"] and resource["start"] <= current_time:
                    num_of_recurrences_since_start = math.ceil((current_time - resource["start"]) / r_interval)
                    resource["start"] += r_interval * num_of_recurrences_since_start
                    resource["end"] += r_interval * num_of_recurrences_since_start
        else:
            # If start or end times of the resource are in the past, we set to the current destination `start` and `end`
            # this is to avoid unnecessary diff outputs
            if resource.get("start") and self.config.storage.data[self.resource_type].destination[_id].get("start"):
                if resource["start"] < self.config.storage.data[self.resource_type].destination[_id]["start"]:
                    resource["start"] = self.config.storage.data[self.resource_type].destination[_id]["start"]
            if resource.get("end") and self.config.storage.data[self.resource_type].destination[_id].get("end"):
                if resource["end"] < self.config.storage.data[self.resource_type].destination[_id]["end"]:
                    resource["end"] = self.config.storage.data[self.resource_type].destination[_id]["end"]

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resp = await destination_client.post(self.resource_config.base_path, resource)

        return _id, resp

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.config.storage.data[self.resource_type].destination[_id]['id']}",
            resource,
        )

        return _id, resp

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.storage.data[self.resource_type].destination[_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(Downtimes, self).connect_id(key, r_obj, resource_to_connect)
