# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from typing import Optional, List, Dict

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import CustomClient


class IntegrationsAWS(BaseResource):
    resource_type = "integrations_aws"
    resource_config = ResourceConfig(
        concurrent=False,
        base_path="/api/v1/integration/aws",
        excluded_attributes=["external_id", "errors"],
    )
    # Additional LogsCustomPipelines specific attributes

    def get_resources(self, client: CustomClient) -> List[Dict]:
        resp = client.get(self.resource_config.base_path).json()

        return resp["accounts"]

    def import_resource(self, resource: Dict) -> None:
        self.resource_config.source_resources[resource["account_id"]] = resource

    def pre_resource_action_hook(self, resource: Dict) -> None:
        pass

    def pre_apply_hook(self, resources: Dict[str, Dict]) -> Optional[list]:
        pass

    def create_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        resp = destination_client.post(self.resource_config.base_path, resource).json()
        data = destination_client.get(self.resource_config.base_path, params={"account_id": _id}).json()
        if "accounts" in data:
            resp.update(data["accounts"][0])

        print(f"integrations_aws created with external_id: {resp['external_id']}")

    def update_resource(self, _id: str, resource: Dict) -> None:
        destination_client = self.config.destination_client
        account_id = resource.pop("account_id", None)
        destination_client.put(
            self.resource_config.base_path,
            resource,
            params={"account_id": account_id, "role_name": resource["role_name"]},
        ).json()

        self.resource_config.destination_resources[_id].update(resource)

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> None:
        super(IntegrationsAWS, self).connect_id(key, r_obj, resource_to_connect)
