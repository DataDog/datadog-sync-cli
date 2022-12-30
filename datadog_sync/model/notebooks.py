# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from typing import Optional, List, Dict

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient, PaginationConfig


class Notebooks(BaseResource):
    resource_type = "notebooks"
    resource_config = ResourceConfig(
        resource_connections={},
        base_path="/api/v1/notebooks",
        excluded_attributes=[
            "id",
            "attributes.created",
            "attributes.modified",
            "attributes.author",
            "attributes.metadata",
        ],
    )
    # Additional Notebooks specific attributes
    pagination_config = PaginationConfig(
        page_size_param="count",
        page_number_param="start",
        remaining_func=lambda idx, resp, page_size, page_number: (resp["meta"]["page"]["total_count"])
        - (page_size * (idx + 1)),
        page_number_func=lambda idx, page_size, page_number: page_size * (idx + 1),
    )

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.paginated_request(client.get)(
            self.resource_config.base_path,
            params={"include_cells": True},
            pagination_config=self.pagination_config,
        )

        return resp

    def import_resource(self, resource: Dict) -> None:
        self.handle_special_case_attr(resource)
        self.resource_config.source_resources[resource["id"]] = resource

    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    def pre_apply_hook(self, resources: Dict[str, Dict]) -> Optional[list]:
        pass

    def create_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = destination_client.post(self.resource_config.base_path, payload).json()
        self.handle_special_case_attr(resp["data"])

        self.resource_config.destination_resources[_id] = resp["data"]

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        payload = {"data": resource}
        resp = destination_client.put(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}",
            payload,
        ).json()
        self.handle_special_case_attr(resp["data"])

        self.resource_config.destination_resources[_id] = resp["data"]

    def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        destination_client.delete(
            self.resource_config.base_path + f"/{self.resource_config.destination_resources[_id]['id']}"
        )

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> None:
        super(Notebooks, self).connect_id(key, r_obj, resource_to_connect)

    @staticmethod
    def handle_special_case_attr(resource):
        # Handle template_variables attribute
        if "template_variables" in resource["attributes"] and not resource["attributes"]["template_variables"]:
            resource["attributes"].pop("template_variables")
