# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import copy
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.resource_utils import check_diff

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class DashboardLists(BaseResource):
    resource_type = "dashboard_lists"
    resource_config = ResourceConfig(
        resource_connections={"dashboards": ["dashboards.id"]},
        base_path="/api/v1/dashboard/lists/manual",
        excluded_attributes=[
            "id",
            "type",
            "author",
            "created",
            "modified",
            "is_favorite",
            "dashboard_count",
        ],
        skip_resource_mapping=True,
    )
    # Additional Dashboards specific attributes
    dash_list_items_path: str = "/api/v2/dashboard/lists/manual/{}/dashboards"

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = await client.get(self.resource_config.base_path)

        return resp["dashboard_lists"]

    async def import_resource(
        self, _id: Optional[str] = None, resource: Optional[Dict] = None
    ) -> Tuple[str, Dict]:
        source_client = self.config.source_client

        if _id:
            resource = await source_client.get(
                self.resource_config.base_path + f"/{_id}"
            )

        resource = cast(dict, resource)
        _id = str(resource["id"])
        # Fetch the list's dashboard items.  A transient 5xx here must
        # propagate so the per-resource import worker counts this resource
        # as a failure and writes no incomplete source state.  Swallowing
        # the error and persisting dashboards=[] would let a later sync
        # interpret a transient read failure as an intentionally empty
        # list and clear destination membership — destructive data loss
        # from a transient API error.  The worker classifies 5xx as
        # http_5xx (transient): counted as failure, logged at WARNING,
        # no exit-code poisoning, no state written.
        resp = await source_client.get(self.dash_list_items_path.format(_id))

        resource["dashboards"] = []
        for dash in resp.get("dashboards", []):
            dash_list_item = {"id": dash["id"], "type": dash["type"]}
            resource["dashboards"].append(dash_list_item)

        return _id, resource

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        self._drop_integration_dashboards(_id, resource)

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        dashboards = copy.deepcopy(resource["dashboards"])
        resource.pop("dashboards")
        resp = await destination_client.post(self.resource_config.base_path, resource)
        await self.update_dash_list_items(resp["id"], dashboards, resp)

        return _id, resp

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        dashboards = copy.deepcopy(resource["dashboards"])
        dash_list_diff = check_diff(
            self.resource_config,
            self.config.state.destination[self.resource_type][_id]["dashboards"],
            dashboards,
        )
        resource.pop("dashboards")

        resp = await destination_client.put(
            self.resource_config.base_path
            + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
            resource,
        )

        resp.pop("dashboards")
        self.config.state.destination[self.resource_type][_id].update(resp)

        if dash_list_diff:
            await self.update_dash_list_items(
                self.config.state.destination[self.resource_type][_id]["id"],
                dashboards,
                self.config.state.destination[self.resource_type][_id],
            )

        return _id, self.config.state.destination[self.resource_type][_id]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        await destination_client.delete(
            self.resource_config.base_path
            + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
        )

    def connect_id(
        self, key: str, r_obj: Dict, resource_to_connect: str
    ) -> Optional[List[str]]:
        if resource_to_connect == "dashboards" and self._is_integration_dashboard(
            r_obj
        ):
            return None
        return super(DashboardLists, self).connect_id(key, r_obj, resource_to_connect)

    def extract_source_ids(
        self, key: str, r_obj: Dict, resource_to_connect: str
    ) -> Optional[List[str]]:
        if resource_to_connect == "dashboards" and self._is_integration_dashboard(
            r_obj
        ):
            return None
        return super().extract_source_ids(key, r_obj, resource_to_connect)

    @staticmethod
    def _is_integration_dashboard(r_obj: Dict) -> bool:
        return str(r_obj.get("type", "")).startswith("integration_")

    def _drop_integration_dashboards(self, _id: str, resource: Dict) -> None:
        dashboards = resource.get("dashboards")
        if not isinstance(dashboards, list):
            return

        portable_dashboards = [
            dash for dash in dashboards if not self._is_integration_dashboard(dash)
        ]
        if len(portable_dashboards) == len(dashboards):
            return

        dropped = sorted(
            str(dash.get("id", ""))
            for dash in dashboards
            if self._is_integration_dashboard(dash)
        )
        self.config.logger.info(
            "dropping integration dashboards from dashboard list before sync; "
            "integration dashboard IDs are not portable across orgs; "
            f"dropped_dashboard_ids={','.join(dropped)}",
            resource_type=self.resource_type,
            _id=_id,
        )
        resource["dashboards"] = portable_dashboards

    async def update_dash_list_items(
        self, _id: str, dashboards: List[Dict], dashboard_list: dict
    ):
        payload = {
            "dashboards": [
                dash for dash in dashboards if not self._is_integration_dashboard(dash)
            ]
        }
        destination_client = self.config.destination_client
        dashboards = await destination_client.put(
            self.dash_list_items_path.format(_id), payload
        )
        dashboard_list.update(dashboards)
