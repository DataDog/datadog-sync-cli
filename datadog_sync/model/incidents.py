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


class Incidents(BaseResource):
    resource_type = "incidents"
    resource_config = ResourceConfig(
        resource_connections={
            "users": [
                "relationships.commander_user.data.id",
            ]
        },
        base_path="/api/v2/incidents",
        excluded_attributes=[
            "id",
            "attributes.public_id",
            "attributes.commander",  # somehow returned by create or update, not by get
            "attributes.last_modified_by",  # somehow returned by create or update, not by get
            "attributes.last_modified_by_uuid",
            "attributes.created",
            "attributes.modified",
            "attributes.created_by",  # somehow returned by create or update, not by get
            "attributes.created_by_uuid",
            "attributes.notification_handles",  # too hard to support properly, also, it gives wrong dates, and possibly spams people, we don't want that; ok to loose that info
            "attributes.time_to_resolve",
            "attributes.customer_impact_duration",  # computed field
            "relationships.created_by_user",
            "relationships.last_modified_by_user",
            "relationships.user_defined_fields",
            "relationships.integrations",
            "relationships.attachments",
            "relationships.responders",
            "relationships.impacts",
        ],
        non_nullable_attr=[
            "attributes.creation_idempotency_key",
            "attributes.customer_impact_scope",
        ],

    )
    # Additional Incidents specific attributes
    pagination_config = PaginationConfig(
        page_size=100,
        page_number_param="page[offset]",
        page_size_param="page[size]",
        # this endpoint uses offset (number of items) instead of page number, workaround the paginated client by reusing `page_number` to store offset instead (computed here because we don't have `resp`)
        page_number_func=lambda idx, page_size, page_number: page_size * (idx + 1),
        # just return 1, the pagination loop already handles breaking when a page is smaller than page size
        remaining_func=lambda *args: 1,
    )

    def get_resources(self, client: CustomClient) -> List[Dict]:
        # we return the incidents in public_id order, so creating them on a fresh organizations will gives us the same public_id in source & destination organizations

        resp = client.paginated_request(client.get)(
            self.resource_config.base_path, pagination_config=self.pagination_config
        )

        return resp

    def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> None:
        if _id:
            source_client = self.config.source_client
            resource = source_client.get(self.resource_config.base_path + f"/{_id}").json()["data"]

        resource = cast(dict, resource)

        # it's the new default imposed by the api; forcing it here so we don't have a forever-diff
        if "visibility" in resource["attributes"] and resource["attributes"]["visibility"] is None:
            resource["attributes"]["visibility"] = "organization"

        # let's do some deepomatic-specific incidents fields migrations:
        if "Namespace" in resource["attributes"]["fields"] and resource["attributes"]["fields"]["Namespace"]["value"] is not None and "kube_namespace" in resource["attributes"]["fields"] and resource["attributes"]["fields"]["kube_namespace"]["value"] is None:
            resource["attributes"]["fields"]["kube_namespace"]["value"] = resource["attributes"]["fields"]["Namespace"]["value"]
            resource["attributes"]["fields"]["Namespace"]["value"] = None

        self.resource_config.source_resources[resource["id"]] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self) -> None:
        pass

    def create_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        payload = {"data": resource}
        # the datadog api documentation says only a subset of accepted fields for creation; in practice it does handles only a subset, and ignores the others
        resp = destination_client.post(
            self.resource_config.base_path,
            payload,
        ).json()

        self.resource_config.destination_resources[_id] = resp["data"]

        # create doesn't accept everything right away, e.g. attributes.resolved; follow the create by an update to sync more data
        self.update_resource(_id, resource)

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
        return super(Incidents, self).connect_id(key, r_obj, resource_to_connect)