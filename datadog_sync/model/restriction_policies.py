# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class RestrictionPolicies(BaseResource):
    resource_type = "restriction_policies"
    resource_config = ResourceConfig(
        resource_connections={
            # Primary ID connections
            "dashboards": ["id"],
            "service_level_objectives": ["id"],
            "notebooks": ["id"],
            # # TODO: Commented out until security rules are supported
            # "security_rules": ["id"],
            # Bindings connections
            "users": ["attributes.bindings.principals"],
            "roles": ["attributes.bindings.principals"],
            "teams": ["attributes.bindings.principals"],
        },
        base_path="/api/v2/restriction_policy",
        excluded_attributes=[],
    )
    # Additional RestrictionPolicies specific attributes
    orgs_path: str = "/api/v1/org"
    org_principal: str = "org:{}"

    def get_resources(self, client: CustomClient) -> List[Dict]:
        policies = []

        dashboards = self.config.resources["dashboards"].get_resources(client)
        notebooks = self.config.resources["notebooks"].get_resources(client)
        slos = self.config.resources["service_level_objectives"].get_resources(client)
        # # TODO: Commented out until security rules are supported
        # security_rules = self.config.resources["security_rules"].get_resources(client)

        if dashboards and len(dashboards) > 0:
            for dashboard in dashboards:
                policies.append(
                    {
                        "id": f"dashboard:{dashboard['id']}",
                    }
                )
        if notebooks and len(notebooks) > 0:
            for notebook in notebooks:
                policies.append(
                    {
                        "id": f"notebook:{notebook['id']}",
                    }
                )
        if slos and len(slos) > 0:
            for slo in slos:
                policies.append(
                    {
                        "id": f"slo:{slo['id']}",
                    }
                )
        # # TODO: Commented out until security rules are supported
        # if security_rules and len(security_rules) > 0:
        #     for rule in security_rules:
        #         policies.append({
        #             "id": f"security-rule:{rule['id']}",
        #         })

        return policies

    def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        source_client = self.config.source_client
        import_id = _id or resource["id"]

        try:
            resource = source_client.get(self.resource_config.base_path + f"/{import_id}").json()
        except CustomClientHTTPError as e:
            if e.status_code == 404:
                raise SkipResource(_id, self.resource_type, "Resource does not exist.")
            else:
                raise e

        if not resource["data"]["attributes"]["bindings"]:
            raise SkipResource(_id, self.resource_type, "Resource does not have any bindings.")

        return import_id, resource["data"]

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        for binding in resource["attributes"]["bindings"]:
            for i, key in enumerate(binding["principals"]):
                if key.startswith("org:"):
                    binding["principals"][i] = self.org_principal
                    break

    def pre_apply_hook(self) -> None:
        destination_client = self.config.destination_client
        try:
            org = destination_client.get(self.orgs_path).json()["orgs"][0]
        except Exception as e:
            self.config.logger.error(f"Failed to get org details: {e}")

        self.org_principal = self.org_principal.format(org["public_id"])

    def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource_id = resource["id"]
        payload = {"data": resource}
        resp = destination_client.post(self.resource_config.base_path + f"/{resource_id}", payload).json()

        return _id, resp["data"]

    def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource_id = resource["id"]
        payload = {"data": resource}
        resp = destination_client.post(self.resource_config.base_path + f"/{resource_id}", payload).json()

        return _id, resp["data"]

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_client.delete(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        dashboards = self.config.resources["dashboards"].resource_config.destination_resources
        slos = self.config.resources["service_level_objectives"].resource_config.destination_resources
        notebooks = self.config.resources["notebooks"].resource_config.destination_resources
        users = self.config.resources["users"].resource_config.destination_resources
        roles = self.config.resources["roles"].resource_config.destination_resources
        teams = self.config.resources["teams"].resource_config.destination_resources

        failed_connections = []
        if key == "id":
            _type, _id = r_obj[key].split(":")
            if resource_to_connect == "dashboards" and _type == "dashboard":
                if _id in dashboards:
                    r_obj[key] = f"dashboard:{dashboards[_id]['id']}"
                else:
                    failed_connections.append(_id)
            elif resource_to_connect == "service_level_objectives" and _type == "slo":
                if _id in slos:
                    r_obj[key] = f"slo:{slos[_id]['id']}"
                else:
                    failed_connections.append(_id)
            elif resource_to_connect == "notebooks" and _type == "notebook":
                if _id in notebooks:
                    r_obj[key] = f"notebook:{notebooks[_id]['id']}"
                else:
                    failed_connections.append(_id)

        if key == "principals":
            for i, policy_id in enumerate(r_obj[key]):
                _type, _id = policy_id.split(":")

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
