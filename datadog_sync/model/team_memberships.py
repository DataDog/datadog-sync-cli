# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import asyncio
import copy
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import PaginationConfig
from datadog_sync.utils.resource_utils import (
    check_diff,
    CustomClientHTTPError,
    SkipResource,
)

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


def _team_membership_key(r):
    return f"{r['relationships']['team']['data']['id']}:{r['relationships']['user']['data']['id']}"


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
        ],
        resource_mapping_key=_team_membership_key,
    )
    team_memberships_path = "/api/v2/team/{}/memberships"
    # Additional TeamMemberships specific attributes

    @staticmethod
    def _memberships_pagination_config() -> PaginationConfig:
        return PaginationConfig(
            page_size=100,
            page_number_param="page[number]",
            page_size_param="page[size]",
            remaining_func=lambda idx, resp, page_size, page_number: max(
                0,
                resp["meta"]["pagination"]["total"] - (page_size * (idx + 1)),
            ),
        )

    async def _get_memberships_for_team(self, client: CustomClient, team_id: str) -> List[Dict]:
        members_of_team = await client.paginated_request(client.get)(
            self.team_memberships_path.format(team_id),
            pagination_config=self._memberships_pagination_config(),
        )
        for member in members_of_team:
            member["relationships"]["team"] = {"data": {"type": "team", "id": team_id}}
        return members_of_team

    async def _get_memberships_for_team_id_targeted(self, client: CustomClient, team_id: str) -> List[Dict]:
        """Fetch memberships for a single team via direct GET pagination.

        The id-targeted path must classify HTTP failures accurately. Using
        client.paginated_request(...) can swallow non-5xx errors and return a
        partial or empty list, which would incorrectly look like a successful
        empty-team no-op. This method keeps failures loud.
        """
        pagination = self._memberships_pagination_config()
        page_size = pagination.page_size or 100
        page_number = pagination.page_number or 0
        list_accessor = pagination.response_list_accessor or "data"
        idx = 0
        all_members: List[Dict] = []

        while True:
            resp = await client.get(
                self.team_memberships_path.format(team_id),
                params={
                    pagination.page_size_param: page_size,
                    pagination.page_number_param: page_number,
                },
            )

            if isinstance(resp, dict):
                members_of_team = resp.get(list_accessor, [])
            else:
                members_of_team = resp

            if not isinstance(members_of_team, list):
                raise ValueError("unexpected response shape while listing team memberships")

            for member in members_of_team:
                member["relationships"]["team"] = {"data": {"type": "team", "id": team_id}}
            all_members.extend(members_of_team)

            if len(members_of_team) < page_size:
                break

            # For exact page boundaries (e.g., total=100 and page_size=100),
            # stop without probing the next page by honoring meta.pagination.total.
            # Fall back to short-page stopping if the metadata is missing.
            if isinstance(resp, dict) and pagination.remaining_func is not None:
                try:
                    remaining = pagination.remaining_func(idx, resp, page_size, page_number)
                    if remaining <= 0:
                        break
                except (KeyError, TypeError):
                    pass

            page_number = pagination.page_number_func(idx, page_size, page_number)
            idx += 1

        return all_members

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        # get all the teams
        teams_pagination_config = self._memberships_pagination_config()
        teams = await client.paginated_request(client.get)(
            self.resource_config.base_path,
            pagination_config=teams_pagination_config,
        )

        # iterate over the teams and create a list of all members of all teams
        all_team_memberships = []
        for team in teams:
            members_of_team = await self._get_memberships_for_team(client, team["id"])
            all_team_memberships += members_of_team

        return all_team_memberships

    async def get_resources_by_ids(
        self,
        client: CustomClient,
        ids: List[str],
        max_concurrent_reads: int = 10,
    ) -> Tuple[List[Dict], List[str], List[Tuple[str, str, str]]]:
        """Fan out team IDs into team_memberships resources without mutating state.

        For ``team_memberships``, the id-file payload contains team IDs. Each
        team ID fans out to zero or more membership resources from
        ``/api/v2/team/{team_id}/memberships``. This fetch phase must remain
        side-effect free so second-pass import/filter semantics stay intact.
        """
        sem = asyncio.Semaphore(max_concurrent_reads)
        resources: List[Dict] = []
        missing: List[str] = []
        errored: List[Tuple[str, str, str]] = []

        import aiohttp

        async def fetch_one(team_id: str):
            async with sem:
                try:
                    members = await self._get_memberships_for_team_id_targeted(client, team_id)
                    return ("ok", members)
                except CustomClientHTTPError as e:
                    if e.status_code == 404:
                        return ("missing", team_id)
                    if e.status_code == 429 or e.status_code >= 500:
                        return ("transient", team_id, f"HTTP {e.status_code}")
                    return ("permanent", team_id, f"HTTP {e.status_code}")
                except (asyncio.TimeoutError,):
                    return ("transient", team_id, "timeout")
                except aiohttp.ClientError as e:
                    return (
                        "transient",
                        team_id,
                        f"connection error: {type(e).__name__}",
                    )
                except Exception as e:
                    msg = str(e)
                    if "retry limit exceeded" in msg:
                        return ("transient", team_id, msg[:200])
                    return ("permanent", team_id, msg[:200])

        results = await asyncio.gather(*(fetch_one(i) for i in ids))
        for result in results:
            tag = result[0]
            if tag == "ok":
                resources.extend(result[1])
            elif tag == "missing":
                missing.append(result[1])
            elif tag == "skipped":
                errored.append((result[1], "skipped", result[2]))
            elif tag == "transient":
                errored.append((result[1], "transient", result[2]))
            else:
                errored.append((result[1], "permanent", result[2]))
        return resources, missing, errored

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id is not None and resource is None:
            raise ValueError(
                "Error creating team membership resource: direct ID import is not supported. "
                "Use get_resources_by_ids() with team IDs."
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
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource_team_id = resource["relationships"]["team"]["data"]["id"]
        destination_resource = copy.deepcopy(resource)

        key = self.get_resource_mapping_key(destination_resource)
        existing = self._existing_resources_map.get(key, {}) if key else {}
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
        key = self.get_resource_mapping_key(state) if state else None
        existing = self._existing_resources_map.get(key, {}) if key else {}

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

        # Get the state directly without refetching resources to avoid race conditions
        # during concurrent cleanups where teams might already be deleted
        state = self.config.state.destination[self.resource_type].get(_id, None)

        # skip if the membership isn't found in state
        if not state:
            raise SkipResource(_id, self.resource_type, f"resource {_id} not found for deletion")

        # Use team_id and user_id directly from state to avoid race conditions
        team_id = state["relationships"]["team"]["data"]["id"]
        user_id = state["relationships"]["user"]["data"]["id"]

        try:
            await destination_client.delete(
                self.team_memberships_path.format(team_id) + f"/{user_id}",
            )
        except Exception as e:
            # If we get a 404, the membership or team may have already been deleted
            # This can happen during cleanup when teams are deleted concurrently
            if "404" in str(e):
                raise SkipResource(
                    _id,
                    self.resource_type,
                    f"resource {_id} or its team not found (may have been already deleted)",
                )
            raise

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
