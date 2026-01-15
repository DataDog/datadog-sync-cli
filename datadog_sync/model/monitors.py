# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import re
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig, TaggingConfig
from datadog_sync.utils.custom_client import PaginationConfig
from datadog_sync.utils.resource_utils import SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class Monitors(BaseResource):
    resource_type = "monitors"
    resource_config = ResourceConfig(
        resource_connections={
            "monitors": ["query"],
            "roles": ["restricted_roles"],
            "service_level_objectives": ["query"],
            "restriction_policies": ["restriction_policy"],
        },
        base_path="/api/v1/monitor",
        excluded_attributes=[
            "id",
            "assets",
            "matching_downtimes",
            "creator",
            "created",
            "deleted",
            "org_id",
            "created_at",
            "modified",
            "overall_state",
            "overall_state_modified",
        ],
        non_nullable_attr=["restriction_policy", "draft_status"],
        null_values={"draft_status": "published"},
        tagging_config=TaggingConfig(path="tags"),
    )
    # Additional Monitors specific attributes
    pagination_config = PaginationConfig(
        page_size=1000,
        page_number_param="page",
        page_size_param="page_size",
        remaining_func=lambda *args: 1,
        response_list_accessor=None,
    )

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.paginated_request(client.get)(
            self.resource_config.base_path, pagination_config=self.pagination_config
        )

        return resp

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = await source_client.get(self.resource_config.base_path + f"/{_id}")

        resource = cast(dict, resource)
        if resource["type"] == "synthetics alert":
            raise SkipResource(
                str(resource["id"]), self.resource_type, "Synthetics monitors are created by synthetics tests."
            )

        return str(resource["id"]), resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resp = await destination_client.post(self.resource_config.base_path, resource)

        return _id, resp

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resp = await destination_client.put(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            resource,
        )

        return _id, resp

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            params={"force": "true"},
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        monitors = self.config.state.destination[resource_to_connect]
        synthetics_tests = self.config.state.destination["synthetics_tests"]
        slos = self.config.state.destination["service_level_objectives"]

        if r_obj.get("type") == "composite" and key == "query" and resource_to_connect != "service_level_objectives":
            failed_connections = []
            ids = re.findall("[0-9]+", r_obj[key])
            for _id in ids:
                found = False
                if _id in monitors:
                    found = True
                    new_id = f"{monitors[_id]['id']}"
                    r_obj[key] = re.sub(_id + r"([^#]|$)", lambda match: f"{new_id}#{match.group(1)}", r_obj[key])
                else:
                    # Check if it is a synthetics monitor
                    for k, v in synthetics_tests.items():
                        if k.endswith(_id):
                            found = True
                            r_obj[key] = re.sub(
                                _id + r"([^#]|$)", lambda match: f"{str(v['monitor_id'])}#{match.group(1)}", r_obj[key]
                            )
                if not found:
                    failed_connections.append(_id)
            r_obj[key] = (r_obj[key].replace("#", "")).strip()
            return failed_connections
        elif resource_to_connect == "service_level_objectives" and r_obj.get("type") == "slo alert" and key == "query":
            failed_connections = []
            if res := re.search(r"(?:error_budget|burn_rate)\(\"(.*?)\"\)\.", r_obj[key]):
                _id = res.group(1)
                if _id in slos:
                    r_obj[key] = re.sub(_id, slos[_id]["id"], r_obj[key])
                else:
                    failed_connections.append(_id)
            return failed_connections
        elif key == "query":
            return None
        else:
            # Use default connect_id method in base class when not handling special case for `query`
            return super(Monitors, self).connect_id(key, r_obj, resource_to_connect)
