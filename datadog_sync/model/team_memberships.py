# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import copy
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import check_diff, SkipResource

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class TeamMemberships(BaseResource):
    resource_type = "team_memberships"
    resource_config = ResourceConfig(
        resource_connections={
            "teams": ["relationships.team.data.id"],
            "users": ["relationships.user.data.id"],
        },
        base_path="/api/v2/team",
        excluded_attributes=[
            "id",
            "attributes.provisioned_by_id",
        ],
    )
    team_memberships_path = "/api/v2/team/{}/memberships"
    destination_team_memberships: List[Dict] = []

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        # get all the teams
        teams = await client.paginated_request(client.get)(
            self.resource_config.base_path,
        )

        # iterate over the teams and create a list of all members of all teams
        all_team_memberships = []
        for team in teams:
            members_of_team = await client.paginated_request(client.get)(
                self.team_memberships_path.format(team["id"]),
            )

            # add the team relationship
            for member in members_of_team:
                member["relationships"]["team"] = {"data": {"type": "team", "id": team["id"]}}
            all_team_memberships += members_of_team

        return all_team_memberships

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        source_client = self.config.source_client

        if _id:
            resource = await source_client.paginated_request(source_client.get)(
                self.team_memberships_path.format(_id),
            )

        resource = cast(dict, resource)
        if not resource:
            raise ValueError("Error creating team membership resource")

        _id = str(resource.get("id"))
        if not _id:
            raise ValueError("Error creating team membership resource, no id")

        return _id, resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        destination_client = self.config.destination_client
        self.destination_team_memberships = await self.get_resources(destination_client)

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource_team_id = resource["relationships"]["team"]["data"]["id"]
        destination_resource = copy.deepcopy(resource)

        existing = self._get_existing_team_membership(destination_resource)
        if existing:
            raise SkipResource(_id, self.resource_type, "User is already a member of the team")

        resp = await destination_client.post(
            self.team_memberships_path.format(resource_team_id),
            {"data": destination_resource},
        )
        resp["data"]["relationships"]["team"] = {"data": {"type": "team", "id": resource_team_id}}
        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        state = self.config.state.destination[self.resource_type].get(_id, None)
        existing = self._get_existing_team_membership(state)

        # membership doesn't exist, create it instead of updating
        if not existing:
            return await self.create_resource(_id, resource)

        # skip if there are no differences
        diff = check_diff(self.resource_config, state, existing)
        if not diff:
            raise SkipResource(resource["id"], self.resource_type, "No differences detected")

        # update the existing resource
        team_id = existing["relationships"]["team"]["data"]["id"]
        user_id = existing["relationships"]["user"]["data"]["id"]
        resp = await destination_client.patch(
            self.team_memberships_path.format(team_id) + f"/{user_id}",
            {"data": resource},
        )
        resp["data"]["relationships"]["team"] = {"data": {"type": "team", "id": team_id}}
        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        self.destination_team_memberships = await self.get_resources(destination_client)

        # check the destination for a matching resource
        state = self.config.state.destination[self.resource_type].get(_id, None)
        existing = self._get_existing_team_membership(state)

        # skip if the membership isn't found at the destination
        if not existing:
            raise SkipResource(_id, self.resource_type, f"resource {_id} not found for deletion")

        # delete the matching resource
        team_id = existing["relationships"]["team"]["data"]["id"]
        user_id = existing["relationships"]["user"]["data"]["id"]
        await destination_client.delete(
            self.team_memberships_path.format(team_id) + f"/{user_id}",
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        failed_connections = []
        _id = r_obj["id"]
        _type = r_obj["type"]
        if resource_to_connect == "users" and _type == "users":
            users = self.config.state.destination["users"]
            if _id in users:
                r_obj[key] = users[_id]["id"]
            else:
                failed_connections.append(_id)
        elif resource_to_connect == "teams" and _type == "team":
            teams = self.config.state.destination["teams"]
            if _id in teams:
                r_obj[key] = teams[_id]["id"]
            else:
                failed_connections.append(_id)
        return failed_connections

    def _get_existing_team_membership(self, state):
        existing = {}
        if state:
            state_team_id = state["relationships"]["team"]["data"]["id"]
            state_user_id = state["relationships"]["user"]["data"]["id"]
            for destination_team_membership in self.destination_team_memberships:
                if (
                    destination_team_membership["relationships"]["team"]["data"]["id"] == state_team_id
                    and destination_team_membership["relationships"]["user"]["data"]["id"] == state_user_id
                ):
                    existing = destination_team_membership
                    break
        return existing
