# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import PaginationConfig

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
            "attributes.provisioned_by",
            "attributes.provisioned_by_id",
            "relationships.team",
        ],
    )
    # Additional TeamMemberships specific attributes
    team_memberships_path = "/api/v2/team/{}/memberships"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        # get all the teams
        teams_pagination_config = PaginationConfig(
            page_size=100,
            page_number_param="page[number]",
            page_size_param="page[size]",
            remaining_func=lambda idx, resp, page_size, page_number: max(
                0,
                resp["meta"]["pagination"]["total"] - (page_size * (idx + 1)),
            ),
        )
        teams = await client.paginated_request(client.get)(
            self.resource_config.base_path,
            pagination_config=teams_pagination_config,
        )

        # iterate over the teams and create a list of all members of all teams
        all_team_memberships = []
        for team in teams:
            members_pagination_config = PaginationConfig(
                page_size=100,
                page_number_param="page[number]",
                page_size_param="page[size]",
                remaining_func=lambda idx, resp, page_size, page_number: max(
                    0,
                    resp["meta"]["pagination"]["total"] - (page_size * (idx + 1)),
                ),
            )
            members_of_team = await client.paginated_request(client.get)(
                self.team_memberships_path.format(team["id"]),
                pagination_config=members_pagination_config,
            )

            # add the team relationship
            for member in members_of_team:
                member["relationships"]["team"] = {"data": {"type": "team", "id": team["id"]}}
            all_team_memberships += members_of_team

        return all_team_memberships

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            all_team_memberships = self.get_resources(source_client)
            for team_member in all_team_memberships:
                if team_member["id"] == _id:
                    resource = team_member
                    break

        return resource["id"], resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        source_team_id = self.config.state.source[self.resource_type][_id]["relationships"]["team"]["data"]["id"]
        destination_team_id = self.config.state.destination["teams"][source_team_id]["id"]

        destination_client = self.config.destination_client
        resp = await destination_client.post(
            self.team_memberships_path.format(destination_team_id),
            {"data": resource},
        )
        resource = resp["data"]
        resource["relationships"]["team"] = {"data": {"type": "team", "id": destination_team_id}}
        return _id, resource

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        destination_resource = self.config.state.destination[self.resource_type][_id]
        team_id = destination_resource["relationships"]["team"]["data"]["id"]
        user_id = destination_resource["relationships"]["user"]["data"]["id"]
        resp = await destination_client.patch(
            self.team_memberships_path.format(team_id) + f"/{user_id}",
            {"data": resource},
        )
        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_resource = self.config.state.destination[self.resource_type][_id]
        team_id = destination_resource["relationships"]["team"]["data"]["id"]
        user_id = destination_resource["relationships"]["user"]["data"]["id"]
        await destination_client.delete(
            self.team_memberships_path.format(team_id) + f"/{user_id}",
        )
