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
            "roles": ["restricted_roles", "restriction_policy.bindings.principals"],
            "service_level_objectives": ["query"],
            "users": ["restriction_policy.bindings.principals"],
            "teams": ["restriction_policy.bindings.principals"],
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
        skip_resource_mapping=True,
    )
    # Additional Monitors specific attributes
    pagination_config = PaginationConfig(
        page_size=1000,
        page_number_param="page",
        page_size_param="page_size",
        remaining_func=lambda *args: 1,
        response_list_accessor=None,
    )
    current_user_path: str = "/api/v2/current_user"
    org_principal: Optional[str] = None

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
        if resource.get("type") == "service check":
            groupby = (resource.get("options") or {}).get("groupby", [])
            if not groupby:
                raise SkipResource(
                    _id,
                    self.resource_type,
                    "Deprecated resource configuration: Custom check monitor requires at least one group-by. "
                    "Update the source monitor's options.groupby before syncing.",
                )

        # org: principals are remapped here (before connect_resources runs).
        # user:/role:/team: principals are remapped by connect_id via resource_connections paths.
        if self.org_principal and resource.get("restriction_policy"):
            for binding in resource["restriction_policy"].get("bindings") or []:
                for i, principal in enumerate(binding.get("principals") or []):
                    if principal.startswith("org:"):
                        binding["principals"][i] = self.org_principal
                        break

    async def pre_apply_hook(self) -> None:
        destination_client = self.config.destination_client
        try:
            resp = await destination_client.get(self.current_user_path)
            org_id = resp["data"]["relationships"]["org"]["data"]["id"]
            self.org_principal = f"org:{org_id}"
        except Exception as e:
            self.config.logger.error(f"Failed to get org details: {e}")
            raise

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
        elif key == "principals":
            # Remap user:/role:/team: principals in restriction_policy bindings.
            # org: principals are handled in pre_resource_action_hook before this runs.
            # Each resource_to_connect pass handles only its type; other types pass through silently.
            users = self.config.state.destination["users"]
            roles = self.config.state.destination["roles"]
            teams = self.config.state.destination["teams"]
            failed_connections = []
            for i, principal in enumerate(r_obj[key]):
                _type, _id = principal.split(":", 1)
                if resource_to_connect == "users" and _type == "user":
                    if _id in users:
                        r_obj[key][i] = f"user:{users[_id]['id']}"
                    else:
                        failed_connections.append(_id)
                elif resource_to_connect == "roles" and _type == "role":
                    if _id in roles:
                        r_obj[key][i] = f"role:{roles[_id]['id']}"
                    else:
                        failed_connections.append(_id)
                elif resource_to_connect == "teams" and _type == "team":
                    if _id in teams:
                        r_obj[key][i] = f"team:{teams[_id]['id']}"
                    else:
                        failed_connections.append(_id)
            return failed_connections
        else:
            # Use default connect_id method in base class when not handling special case for `query`
            return super(Monitors, self).connect_id(key, r_obj, resource_to_connect)
