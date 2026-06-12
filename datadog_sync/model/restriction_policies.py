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
        skip_resource_mapping=True,
    )
    # Additional RestrictionPolicies specific attributes
    current_user_path: str = "/api/v2/current_user"
    org_principal: Optional[str] = None
    # UUID of the syncing service account, captured in pre_apply_hook from
    # /api/v2/current_user. Used by the self-demote pre-filter to skip
    # policies that would remove this user's existing editor binding —
    # the Datadog API rejects such requests with 400 "users cannot
    # decrease their own level of access", producing noisy ERROR events
    # in prod telemetry with no actionable signal for operators.
    current_user_uuid: Optional[str] = None

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        policies = []

        dashboards = await self.config.resources["dashboards"].get_resources(client)
        notebooks = await self.config.resources["notebooks"].get_resources(client)
        slos = await self.config.resources["service_level_objectives"].get_resources(client)
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

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        source_client = self.config.source_client
        import_id = _id or resource["id"]

        try:
            resource = await source_client.get(self.resource_config.base_path + f"/{import_id}")
        except CustomClientHTTPError as e:
            if e.status_code == 404:
                raise SkipResource(import_id, self.resource_type, "Resource does not exist.")
            else:
                raise e

        if not resource["data"]["attributes"]["bindings"]:
            raise SkipResource(import_id, self.resource_type, "Resource does not have any bindings.")

        return import_id, resource["data"]

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        # Pre-filter two deterministic failure modes BEFORE the API call so
        # they don't surface as ERROR-level events in operator/customer
        # telemetry (NATHAN-50). Both checks are best-effort — they bail
        # out silently when the state needed to decide is unavailable.
        self._skip_if_target_dashboard_is_read_only(_id, resource)
        self._skip_if_would_self_demote(_id, resource)

        # Existing behavior: rewrite the source-org "org:<uuid>" principal
        # to the destination-org "org:<uuid>" so bindings reference the
        # local org. Runs AFTER the skip filters so the skip checks see
        # the as-imported source-shaped resource and aren't disturbed by
        # the in-place principal rewrite.
        if self.org_principal:
            for binding in resource["attributes"]["bindings"]:
                for i, key in enumerate(binding["principals"]):
                    if key.startswith("org:"):
                        binding["principals"][i] = self.org_principal
                        break

    def _skip_if_target_dashboard_is_read_only(self, _id: str, resource: Dict) -> None:
        """Skip dashboard-targeted policies when the destination dashboard
        is read-only (built-in / template / shared).

        The Datadog restriction-policy API rejects attachment to read-only
        dashboards with 403 "This dashboard is read-only". The check uses
        the ``is_read_only`` field from the destination dashboard state,
        which is populated by the dashboards resource's create/update
        response (the API returns the full body including this field even
        though it is in ``excluded_attributes`` — that list only affects
        diffing, not state storage; see resource_utils.prep_resource).
        """
        policy_id = resource.get("id") or _id
        if not isinstance(policy_id, str) or not policy_id.startswith("dashboard:"):
            # Only dashboards expose ``is_read_only``; other target types
            # pass through this branch.
            return

        # Resolve the destination dashboard via the source dashboard id
        # (the second half of the policy id), keyed in the destination
        # state by source id. ``connect_id`` does the same lookup later
        # for principal rewriting.
        try:
            _, src_dashboard_id = policy_id.split(":", 1)
        except ValueError:
            return

        dashboards_state = self.config.state.destination.get("dashboards", {})
        dashboard = dashboards_state.get(src_dashboard_id)
        if not dashboard:
            # Dashboard not in destination state — the existing
            # connect_id pass will surface a missing-connections error
            # if applicable. Don't short-circuit that path here.
            return

        if not dashboard.get("is_read_only"):
            return

        self.config.logger.info(
            f"[restriction_policies - {policy_id}] skipping: "
            f"target dashboard {src_dashboard_id} is read-only on destination"
        )
        raise SkipResource(
            policy_id,
            self.resource_type,
            f"Target dashboard {src_dashboard_id} is read-only on destination " "(skipped to avoid API 403).",
        )

    def _skip_if_would_self_demote(self, _id: str, resource: Dict) -> None:
        """Skip policies that would remove the syncing service account's own
        ``editor`` binding.

        The Datadog API rejects such requests with 400 "users cannot
        decrease their own level of access (from editor to viewer)". When
        the operator has explicitly set ``--allow-self-lockout``, the
        request goes through with ``?allow_self_lockout=true`` and the
        API permits it — we must NOT pre-filter in that case.

        Scope: this check inspects only **direct** ``user:<uuid>`` bindings.
        We fire the skip only when there is at least one ``user:<X>``
        editor principal AND the syncing SA's UUID is not among them —
        that is the unambiguous "removed from editor" case the API
        rejects. When the editor bindings are entirely ``role:`` /
        ``team:`` / ``org:`` (no direct user bindings at all), we cannot
        infer self-demote from the payload alone and let the request
        proceed — the API will accept it if the SA retains effective
        access via membership.
        """
        if not self.current_user_uuid:
            # pre_apply_hook didn't capture the uuid — no reliable way to
            # decide. Let the request go through and surface the API error
            # as it does today.
            return
        if self.config.allow_self_lockout:
            # Operator opted in to bypass the safety check.
            return

        sa_user_principal = f"user:{self.current_user_uuid}"
        bindings = resource.get("attributes", {}).get("bindings") or []
        if not bindings:
            # Empty bindings clears all restrictions — the syncing user
            # keeps whatever org-default access they have. Not a
            # self-demote case.
            return

        # Three signals from the bindings:
        # - sa_is_in_editor: SA appears under an editor binding (no self-demote).
        # - sa_is_in_viewer: SA appears under a viewer/non-editor binding
        #   (explicit downgrade — self-demote).
        # - has_user_editor_principal: at least one user:<X> appears under an
        #   editor binding (lets us decide on absence: if user editors exist
        #   but SA isn't one, SA is being removed → self-demote).
        sa_is_in_editor = False
        sa_is_in_viewer = False
        has_user_editor_principal = False
        for binding in bindings:
            relation = binding.get("relation")
            for principal in binding.get("principals") or []:
                if not isinstance(principal, str):
                    continue
                if relation == "editor":
                    if principal.startswith("user:"):
                        has_user_editor_principal = True
                    if principal == sa_user_principal:
                        sa_is_in_editor = True
                elif principal == sa_user_principal:
                    # SA listed under a non-editor relation (typically
                    # viewer) — explicit downgrade.
                    sa_is_in_viewer = True

        if sa_is_in_editor:
            # SA is explicitly retained as editor — no self-demote.
            return
        if not sa_is_in_viewer and not has_user_editor_principal:
            # No SA-specific signal and editor bindings are only
            # role:/team:/org: principals. SA's effective access may come
            # from membership we can't analyze. Let the API decide.
            return

        policy_id = resource.get("id") or _id
        self.config.logger.info(
            f"[restriction_policies - {policy_id}] skipping: "
            f"would self-demote syncing user {self.current_user_uuid}"
        )
        raise SkipResource(
            policy_id,
            self.resource_type,
            f"Policy would self-demote the syncing user {self.current_user_uuid} "
            "(skipped to avoid API 400). Pass --allow-self-lockout to override.",
        )

    async def pre_apply_hook(self) -> None:
        destination_client = self.config.destination_client
        try:
            resp = await destination_client.get(self.current_user_path)
            org_id = resp["data"]["relationships"]["org"]["data"]["id"]
            self.org_principal = f"org:{org_id}"
            # Capture the syncing user's UUID for the self-demote pre-filter.
            # ``data.id`` is the user UUID per /api/v2/current_user schema;
            # ``data.attributes.uuid`` carries the same value. Fall back to
            # the attributes copy if the top-level id is absent for any
            # reason (it's been stable in the API for years, but be defensive).
            self.current_user_uuid = resp["data"].get("id") or resp["data"].get("attributes", {}).get("uuid")
        except Exception as e:
            self.config.logger.error(f"Failed to get org details: {e}")
            raise

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource_id = resource["id"]
        payload = {"data": resource}

        # Add query parameter if allow_self_lockout is enabled
        params = {}
        if self.config.allow_self_lockout:
            params["allow_self_lockout"] = "true"

        resp = await destination_client.post(
            self.resource_config.base_path + f"/{resource_id}", payload, params=params if params else None
        )

        return _id, resp["data"]

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource_id = resource["id"]
        payload = {"data": resource}

        # Add query parameter if allow_self_lockout is enabled
        params = {}
        if self.config.allow_self_lockout:
            params["allow_self_lockout"] = "true"

        resp = await destination_client.post(
            self.resource_config.base_path + f"/{resource_id}", payload, params=params if params else None
        )

        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        dashboards = self.config.state.destination["dashboards"]
        slos = self.config.state.destination["service_level_objectives"]
        notebooks = self.config.state.destination["notebooks"]
        users = self.config.state.destination["users"]
        roles = self.config.state.destination["roles"]
        teams = self.config.state.destination["teams"]

        failed_connections = []
        if key == "id":
            _type, _id = r_obj[key].split(":", 1)
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
                _type, _id = policy_id.split(":", 1)

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
