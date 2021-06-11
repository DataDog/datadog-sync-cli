import logging
from concurrent.futures import ThreadPoolExecutor, wait

from deepdiff import DeepDiff
from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


log = logging.getLogger("__name__")


RESOURCE_TYPE = "integrations_aws"
EXCLUDED_ATTRIBUTES = ["root['external_id']"]
BASE_PATH = "/api/v1/integration/aws"


class IntegrationsAWS(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_TYPE, BASE_PATH, excluded_attributes=EXCLUDED_ATTRIBUTES)

    def import_resources(self):
        integrations_aws = {}
        source_client = self.ctx.obj.get("source_client")

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            log.error("error importing integrations_aws %s", e)
            return

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(self.process_resource_import, integration_aws, integrations_aws)
                    for integration_aws in resp["accounts"]
                ]
            )

        # Write resources to file
        self.write_resources_file("source", integrations_aws)

    def process_resource_import(self, integration_aws, integrations_aws):
        integrations_aws[integration_aws["account_id"]] = integration_aws

    def apply_resources(self):
        source_resources, local_destination_resources = self.open_resources()

        log.info("Processing integrations_aws")

        connection_resource_obj = self.get_connection_resources()

        # must not be done in parallel, api returns conflict error
        for _id, aws_integration in source_resources.items():
            self.prepare_resource_and_apply(_id, aws_integration, local_destination_resources, connection_resource_obj)

        self.write_resources_file("destination", local_destination_resources)

    def prepare_resource_and_apply(
        self, _id, integration_aws, local_destination_resources, connection_resource_obj=None
    ):
        if self.resource_connections:
            self.connect_resources(integration_aws, connection_resource_obj)

        if _id in local_destination_resources:
            self.update_resource(_id, integration_aws, local_destination_resources)
        else:
            self.create_resource(_id, integration_aws, local_destination_resources)

    def create_resource(self, _id, integration_aws, local_destination_resources):
        destination_client = self.ctx.obj.get("destination_client")
        try:
            resp = destination_client.post(self.base_path, integration_aws).json()
            data = destination_client.get(self.base_path, params={"account_id": _id}).json()
        except HTTPError as e:
            log.error("error creating integration_aws: %s", e.response.text)
            return

        if "accounts" in data:
            resp.update(data["accounts"][0])

        local_destination_resources[_id] = resp

    def update_resource(self, _id, integration_aws, local_destination_resources):
        destination_client = self.ctx.obj.get("destination_client")

        diff = self.check_diff(integration_aws, local_destination_resources[_id])
        if diff:
            try:
                destination_client.put(
                    self.base_path + f"/{local_destination_resources[_id]['id']}", integration_aws
                ).json()
            except HTTPError as e:
                log.error("error updating integration_aws: %s", e.response.text)
                return
