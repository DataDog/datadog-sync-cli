# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast
from datetime import datetime

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class SLOCorrections(BaseResource):
    resource_type = "slo_corrections"
    resource_config = ResourceConfig(
        resource_connections={"service_level_objectives": ["attributes.slo_id"]},
        base_path="/api/v1/slo/correction",
        excluded_attributes=["id", "attributes.creator", "attributes.created_at", "attributes.modified_at"],
        non_nullable_attr=[
            "attributes.duration",
            "attributes.rrule",
            # slo_id and slo_query are mutually exclusive: a time-based
            # correction sets slo_id (with slo_query=null) and a
            # query-based correction sets slo_query (with slo_id=null).
            # The destination API rejects null on either field with
            # "Field may not be null" — strip whichever is null before POST.
            "attributes.slo_id",
            "attributes.slo_query",
            # end is optional (recurring corrections omit it); when the
            # source resource has end=null the destination API rejects
            # the payload rather than treating null as "unset". Strip.
            "attributes.end",
        ],
        skip_resource_mapping=True,
    )
    # Additional SLOCorrections specific attributes

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return resp["data"]

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (await source_client.get(self.resource_config.base_path + f"/{_id}"))["data"]

        resource = cast(dict, resource)
        if resource["attributes"].get("end", False):
            if (round(datetime.now().timestamp()) - int(resource["attributes"]["end"])) / 86400 > 90:
                raise SkipResource(resource["id"], self.resource_type, "End time is older than 90 days.")

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = await destination_client.post(self.resource_config.base_path, payload)

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
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
        return super(SLOCorrections, self).connect_id(key, r_obj, resource_to_connect)
