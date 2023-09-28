# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Dict, cast

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import PaginationConfig

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient


class IncidentsTodos(BaseResource):
    resource_type = "incidents_todos"
    resource_config = ResourceConfig(
        resource_connections={
            "incidents": [
                "attributes.incident_id",
            ]
        },
        base_path="/api/v2/incidents",
        excluded_attributes=[
            "id",
            "attributes.last_modified_by",  # somehow returned by create or update, not by get
            "attributes.last_modified_by_uuid",
            "attributes.created",
            "attributes.modified",
            "attributes.created_by",  # somehow returned by create or update, not by get
            "attributes.created_by_uuid",
            "relationships.created_by_user",
            "relationships.last_modified_by_user",
        ]
    )
    # Additional IncidentsTodos specific attributes
    pagination_config = PaginationConfig(
        page_size=100,
        page_number_param="page[offset]",
        page_size_param="page[size]",
        # this endpoint uses offset (number of items) instead of page number, workaround the paginated client by reusing `page_number` to store offset instead (computed here because we don't have `resp`)
        page_number_func=lambda idx, page_size, page_number: page_size * (idx + 1),
        # just return 1, the pagination loop already handles breaking when a page is smaller than page size
        remaining_func=lambda *args: 1,
    )
    todos_path: str = "/api/v2/incidents/{incident_id}/relationships/todos"

    def get_resources(self, client: CustomClient) -> List[Dict]:
        # first, get all incidents, then for each incidents, get all incidents todos
        resp_incidents = client.paginated_request(client.get)(
            self.resource_config.base_path,
            pagination_config=self.pagination_config
        )

        resp = []
        for incident in resp_incidents:
            resp += client.paginated_request(client.get)(
                # use public id, to avoid connecting manually the resource here (we are in the get_resources, it's not usually done there, so not free); this assumes the public IDs between source & destination are in sync, which should be the case if importing incidents via datadog-sync-cli, cf comments in that resource
                self.todos_path.format(incident_id=incident["attributes"]["public_id"]),
                pagination_config=self.pagination_config
            )
        return resp

    def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> None:
        if _id:
            raise Exception("importing incidents_todos by id is not supported: we need not only the incidents_todos id (which we have) but also the parent incident id, which we do not have.")

        resource = cast(dict, resource)
        self.resource_config.source_resources[resource["id"]] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self) -> None:
        pass

    def create_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        destination_incident_id = resource["attributes"].pop("incident_id")
        payload = {"data": resource}

        resp = destination_client.post(
            # incidents api works both with public_id and id, here we use the connected (converted to the destination incident) uuid id
            self.todos_path.format(incident_id=destination_incident_id),
            payload,
        ).json()

        self.resource_config.destination_resources[_id] = resp["data"]

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        destination_incident_id = resource["attributes"].pop("incident_id")
        payload = {"data": resource}
        resp = destination_client.patch(
            # incidents api works both with public_id and id, here we use the connected (converted to the destination incident) uuid id
            self.todos_path.format(incident_id=destination_incident_id) +
            f"/{self.resource_config.destination_resources[_id]['id']}",
            payload,
        ).json()

        self.resource_config.destination_resources[_id] = resp["data"]

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_incident_id = resource["attributes"].pop("incident_id")
        destination_client.delete(
            # incidents api works both with public_id and id, here we use the connected (converted to the destination incident) uuid id
            self.todos_path.format(incident_id=destination_incident_id) +
            f"/{self.resource_config.destination_resources[_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(IncidentsTodos, self).connect_id(key, r_obj, resource_to_connect)
