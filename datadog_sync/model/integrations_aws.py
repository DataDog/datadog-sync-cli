# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


class IntegrationsAWS(BaseResource):
    resource_type = "integrations_aws"
    resource_connections = None
    base_path = "/api/v1/integration/aws"
    excluded_attributes = ["root['external_id']", "root['errors']"]

    def import_resources(self):
        source_client = self.config.source_client

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing integrations_aws %s", e)
            return

        self.import_resources_concurrently(resp["accounts"])

    def process_resource_import(self, integration_aws):
        if not self.filter(integration_aws):
            return

        self.source_resources[integration_aws["account_id"]] = integration_aws

    def apply_resources(self):
        self.logger.info("Processing integrations_aws")

        # must not be done in parallel, api returns conflict error
        self.apply_resources_sequentially()

    def prepare_resource_and_apply(self, _id, integration_aws):
        self.connect_resources(_id, integration_aws)

        if _id in self.destination_resources:
            self.update_resource(_id, integration_aws)
        else:
            self.create_resource(_id, integration_aws)

    def create_resource(self, _id, integration_aws):
        destination_client = self.config.destination_client
        try:
            resp = destination_client.post(self.base_path, integration_aws).json()
            data = destination_client.get(self.base_path, params={"account_id": _id}).json()
        except HTTPError as e:
            self.logger.error("error creating integration_aws: %s", e.response.text)
            return

        if "accounts" in data:
            resp.update(data["accounts"][0])

        print(f"integrations_aws created with external_id: {resp['external_id']}")

        self.destination_resources[_id] = resp

    def update_resource(self, _id, integration_aws):
        destination_client = self.config.destination_client
        self.remove_excluded_attr(integration_aws)

        diff = self.check_diff(integration_aws, self.destination_resources[_id])
        if diff:
            account_id = integration_aws.pop("account_id", None)
            try:
                destination_client.put(
                    self.base_path,
                    integration_aws,
                    params={"account_id": account_id, "role_name": integration_aws["role_name"]},
                ).json()
            except HTTPError as e:
                self.logger.error("error updating integration_aws: %s", e.response.text)
                return
            self.destination_resources[_id].update(integration_aws)
