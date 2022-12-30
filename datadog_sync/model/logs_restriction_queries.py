# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from typing import Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient, PaginationConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError, check_diff


class LogsRestrictionQueries(BaseResource):
    resource_type = "logs_restriction_queries"
    resource_config = ResourceConfig(
        resource_connections={"roles": ["data.relationships.roles.data.id"]},
        base_path="/api/v2/logs/config/restriction_queries",
        excluded_attributes=[
            "data.attributes.created_at",
            "data.attributes.modified_at",
            "data.id",
            "included",
        ],
    )
    # Additional LogsRestrictionQueries specific attributes
    pagination_config = PaginationConfig(
        page_size=100,
        remaining_func=lambda *args: 1,
    )
    logs_restriction_query_roles_path: str = "/api/v2/logs/config/restriction_queries/{}/roles"

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.paginated_request(client.get)(
            self.resource_config.base_path, pagination_config=self.pagination_config
        )
        return resp

    def import_resource(self, resource: Dict) -> None:
        source_client = self.config.source_client
        r_query = source_client.get(self.resource_config.base_path + f"/{resource['id']}").json()
        r_query.pop("included", None)
        self.resource_config.source_resources[resource["id"]] = r_query

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self, resources: Dict[str, Dict]) -> Optional[list]:
        pass

    def create_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        relationships = resource["data"].pop("relationships")
        added_role_ids = set([role["id"] for role in relationships["roles"]["data"]])

        resp = destination_client.post(self.resource_config.base_path, resource).json()
        successfully_added, _ = self.update_log_restriction_query_roles(resp["data"]["id"], added_role_ids, set())

        new_roles = [{"id": _id, "type": "roles"} for _id in successfully_added]
        resp["data"]["relationships"] = {"roles": {"data": new_roles}}
        self.resource_config.destination_resources[_id] = resp

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        new_relationships = resource["data"].pop("relationships", {})
        old_relationships = self.resource_config.destination_resources[_id]["data"].pop("relationships", {})
        old_roles_ids = set([role["id"] for role in old_relationships.get("roles", {}).get("data", {})])
        new_roles_ids = set([role["id"] for role in new_relationships.get("roles", {}).get("data", {})])
        intersection = new_roles_ids & old_roles_ids
        added_role_ids = new_roles_ids - intersection
        removed_role_ids = old_roles_ids - intersection

        dest_id = self.resource_config.destination_resources[_id]["data"]["id"]
        if check_diff(
            self.resource_config,
            self.resource_config.destination_resources[_id],
            resource,
        ):
            resp = destination_client.put(self.resource_config.base_path + f"/{dest_id}", resource).json()
            self.resource_config.destination_resources[_id].update(resp)
            self.resource_config.destination_resources[_id]["data"]["relationships"] = old_relationships

        if added_role_ids or removed_role_ids:
            succ_added, succ_removed = self.update_log_restriction_query_roles(
                dest_id, added_role_ids, removed_role_ids
            )
            new_roles = [{"id": role_id, "type": "roles"} for role_id in (list(intersection) + succ_added)]
            self.resource_config.destination_resources[_id]["data"]["relationships"] = {"roles": {"data": new_roles}}

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_client.delete(
            self.resource_config.base_path + f'/{self.resource_config.destination_resources[_id]["data"]["id"]}'
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> None:
        super(LogsRestrictionQueries, self).connect_id(key, r_obj, resource_to_connect)

    def update_log_restriction_query_roles(self, _id: str, added_roles: set, removed_roles: set) -> Tuple[list, list]:
        successfully_added, successfully_removed = [], []
        for role_id in added_roles:
            try:
                self.add_log_restriction_query_role(_id, role_id)
            except CustomClientHTTPError as e:
                self.config.logger.error(
                    "error adding role %s to log restriction query %s: %s",
                    role_id,
                    _id,
                    e,
                )
                continue
            successfully_added.append(role_id)
        for role_id in removed_roles:
            try:
                self.remove_log_restriction_query_role(_id, role_id)
            except CustomClientHTTPError as e:
                self.config.logger.error(
                    "error removing role %s to log restriction query %s: %s",
                    role_id,
                    _id,
                    e,
                )
                continue
            successfully_removed.append(role_id)
        return successfully_added, successfully_removed

    def add_log_restriction_query_role(self, _id: str, role_id: str) -> None:
        destination_client = self.config.destination_client
        payload = {"data": {"id": role_id, "type": "roles"}}
        destination_client.post(self.logs_restriction_query_roles_path.format(_id), payload)

    def remove_log_restriction_query_role(self, _id: str, role_id: str) -> None:
        destination_client = self.config.destination_client
        payload = {"data": {"id": role_id, "type": "roles"}}
        destination_client.delete(self.logs_restriction_query_roles_path.format(_id), payload)
