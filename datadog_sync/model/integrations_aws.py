# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from typing import Optional

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResourceModel, ResourceConfig


class IntegrationsAWS(BaseResourceModel):
    resource_type = "integrations_aws"
    resource_config = ResourceConfig(
        concurrent=False,
        base_path="/api/v1/integration/aws",
        excluded_attributes=["root['external_id']", "root['errors']"],
    )
    # Additional LogsCustomPipelines specific attributes

    def get_resources(self, client) -> list:
        try:
            resp = client.get(self.resource_config.base_path).json()
        except HTTPError as e:
            self.config.logger.error("error importing integrations_aws %s", e)
            return []

        return resp["accounts"]

    def import_resource(self, resource) -> None:
        self.resource_config.source_resources[resource["account_id"]] = resource

    def pre_resource_action_hook(self, resource) -> None:
        pass

    def pre_apply_hook(self, resources) -> Optional[list]:
        pass

    def create_resource(self, _id, resource) -> None:
        destination_client = self.config.destination_client
        try:
            resp = destination_client.post(self.resource_config.base_path, resource).json()
            data = destination_client.get(self.resource_config.base_path, params={"account_id": _id}).json()
        except HTTPError as e:
            self.config.logger.error("error creating integration_aws: %s", e.response.text)
            return

        if "accounts" in data:
            resp.update(data["accounts"][0])

        print(f"integrations_aws created with external_id: {resp['external_id']}")

    def update_resource(self, _id, resource) -> None:
        destination_client = self.config.destination_client

        account_id = resource.pop("account_id", None)
        try:
            destination_client.put(
                self.resource_config.base_path,
                resource,
                params={"account_id": account_id, "role_name": resource["role_name"]},
            ).json()
        except HTTPError as e:
            self.config.logger.error("error updating integration_aws: %s", e.response.text)
            return
        self.resource_config.destination_resources[_id].update(resource)

    def connect_id(self, key, r_obj, resource_to_connect) -> None:
        super(IntegrationsAWS, self).connect_id(key, r_obj, resource_to_connect)
