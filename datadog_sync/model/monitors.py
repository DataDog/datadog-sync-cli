# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import re
from typing import Optional, List, Dict

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.resource_utils import ResourceConnectionError


class Monitors(BaseResource):
    resource_type = "monitors"
    resource_config = ResourceConfig(
        resource_connections={"monitors": ["query"], "roles": ["restricted_roles"], "synthetics_tests": []},
        base_path="/api/v1/monitor",
        excluded_attributes=[
            "id",
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
    )
    # Additional Monitors specific attributes

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(self.resource_config.base_path).json()

        return resp

    def import_resource(self, resource: Dict) -> None:
        if resource["type"] in ("synthetics alert", "slo alert"):
            return

        self.resource_config.source_resources[str(resource["id"])] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self, resources: Dict[str, Dict]) -> Optional[list]:
        simple_monitors = {}
        composite_monitors = {}

        for _id, monitor in self.resource_config.source_resources.items():
            if monitor["type"] == "composite":
                composite_monitors[_id] = monitor
            else:
                simple_monitors[_id] = monitor
        return [simple_monitors, composite_monitors]

    def create_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        resp = destination_client.post(self.resource_config.base_path, resource).json()

        self.resource_config.destination_resources[_id] = resp

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        resp = destination_client.put(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}",
            resource,
        ).json()

        self.resource_config.destination_resources[_id] = resp

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_client.delete(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}",
            params={"force": True},
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> None:
        monitors = self.config.resources[resource_to_connect].resource_config.destination_resources
        synthetics_tests = self.config.resources["synthetics_tests"].resource_config.destination_resources

        if r_obj.get("type") == "composite" and key == "query":
            ids = re.findall("[0-9]+", r_obj[key])
            for _id in ids:
                found = False
                if _id in monitors:
                    found = True
                    new_id = f"{monitors[_id]['id']}"
                    r_obj[key] = re.sub(_id + r"([^#]|$)", new_id + "# ", r_obj[key])
                else:
                    # Check if it is a synthetics monitor
                    for k, v in synthetics_tests.items():
                        if k.endswith(_id):
                            found = True
                            r_obj[key] = re.sub(_id + r"([^#]|$)", str(v["monitor_id"]) + "# ", r_obj[key])
                if not found:
                    raise ResourceConnectionError(resource_to_connect, _id=_id)
            r_obj[key] = (r_obj[key].replace("#", "")).strip()
        elif key != "query":
            # Use default connect_id method in base class when not handling special case for `query`
            super(Monitors, self).connect_id(key, r_obj, resource_to_connect)
