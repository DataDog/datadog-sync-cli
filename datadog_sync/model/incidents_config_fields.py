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


class IncidentsConfigFields(BaseResource):
    resource_type = "incidents_config_fields"
    resource_config = ResourceConfig(
        base_path="/api/v2/incidents/config/fields",
        excluded_attributes=[
            "attributes.created_by",
            "attributes.created_by_uuid",
            "attributes.last_modified_by",
            "attributes.last_modified_by_uuid",
            "attributes.created",
            "attributes.modified",
            "relationships.created_by_user",
            "relationships.last_modified_by_user",
            "id",
        ]
    )
    # Additional Incidents specific attributes
    pagination_config = PaginationConfig(
        page_size=1000,
        page_number_param="page[offset]",
        page_size_param="page[limit]",
        # this endpoint uses offset (number of items) instead of page number, workaround the paginated client by reusing `page_number` to store offset instead (computed here because we don't have `resp`)
        page_number_func=lambda idx, page_size, page_number: page_size * (idx + 1),
        # just return 1, the pagination loop already handles breaking when a page is smaller than page size
        remaining_func=lambda *args: 1,
    )
    # key: (unique) attributes.name
    destination_incidents_config_fields: Dict[str, Dict] = dict()

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.paginated_request(client.get)(
            self.resource_config.base_path,
            pagination_config=self.pagination_config
        )

        return resp

    def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> None:
        if _id:
            source_client = self.config.source_client
            resource = source_client.get(self.resource_config.base_path + f"/{_id}").json()["data"]

        resource = cast(dict, resource)
        self.resource_config.source_resources[resource["id"]] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self) -> None:
        self.destination_incidents_config_fields = self.get_destination_incidents_config_fields()

    def create_resource(self, _id: str, resource: Dict) -> None:
        # names are unique: patching existing ones instead of create
        name = resource["attributes"]["name"]
        if name in self.destination_incidents_config_fields:
            self.resource_config.destination_resources[_id] = self.destination_incidents_config_fields[name]
            self.update_resource(_id, resource)
            return

        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = destination_client.post(
            self.resource_config.base_path,
            payload,
        ).json()

        self.resource_config.destination_resources[_id] = resp["data"]

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = destination_client.patch(
            self.resource_config.base_path +
            f"/{self.resource_config.destination_resources[_id]['id']}",
            payload,
        ).json()

        self.resource_config.destination_resources[_id] = resp["data"]

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_client.delete(
            self.resource_config.base_path +
            f"/{self.resource_config.destination_resources[_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        pass

    def get_destination_incidents_config_fields(self) -> Dict[str, Dict]:
        destination_incidents_config_fields = {}
        destination_client = self.config.destination_client

        resp = self.get_resources(destination_client)
        for log_facet in resp:
            destination_incidents_config_fields[log_facet["attributes"]["name"]] = log_facet

        return destination_incidents_config_fields
