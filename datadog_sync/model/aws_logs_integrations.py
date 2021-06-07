import logging
from concurrent.futures import ThreadPoolExecutor, wait

from deepdiff import DeepDiff
from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


log = logging.getLogger("__name__")


RESOURCE_TYPE = "aws_logs_integrations"
BASE_PATH = "/api/v1/integration/aws/logs"


class AWSLogsIntegrations(BaseResource):
    def __init__(self, ctx):
        super().__init__(
            ctx,
            RESOURCE_TYPE,
            BASE_PATH,
        )

    def import_resources(self):
        aws_logs_integrations = {}
        source_client = self.ctx.obj.get("source_client")

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            log.error("error importing aws_logs_integrations %s", e)
            return

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(self.process_resource_import, aws_logs_integration, aws_logs_integrations)
                    for aws_logs_integration in resp
                ]
            )

        # Write resources to file
        self.write_resources_file("source", aws_logs_integrations)

    def process_resource_import(self, aws_logs_integration, aws_logs_integrations):
        aws_logs_integrations[aws_logs_integration["account_id"]] = aws_logs_integration

    def apply_resources(self):
        source_resources, local_destination_resources = self.open_resources()

        log.info("Processing aws_logs_integrations")

        connection_resource_obj = self.get_connection_resources()

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(
                        self.prepare_resource_and_apply,
                        _id,
                        aws_logs_integration,
                        local_destination_resources,
                        connection_resource_obj,
                    )
                    for _id, aws_logs_integration in source_resources.items()
                ]
            )

        self.write_resources_file("destination", local_destination_resources)

    def prepare_resource_and_apply(
        self, _id, aws_logs_integration, local_destination_resources, connection_resource_obj=None
    ):
        if self.resource_connections:
            self.connect_resources(aws_logs_integration, connection_resource_obj)

        if _id in local_destination_resources:
            self.update_resource(_id, aws_logs_integration, local_destination_resources)
        else:
            self.create_resource(_id, aws_logs_integration, local_destination_resources)

    def create_resource(self, _id, aws_logs_integration, local_destination_resources):
        destination_client = self.ctx.obj.get("destination_client")

        for lambdas in aws_logs_integration["lambdas"]:
            try:
                body = {
                    "account_id" : aws_logs_integration["account_id"],
                    "lambda_arn": lambdas["arn"]
                }
                resp = destination_client.post(self.base_path, body).json()
            except HTTPError as e:
                log.error("error creating aws_logs_integration: %s", e.response.text)
                return
            local_destination_resources[_id] = resp

    def update_resource(self, _id, aws_logs_integration, local_destination_resources):
        destination_client = self.ctx.obj.get("destination_client")

        diff = self.check_diff(aws_logs_integration, local_destination_resources[_id])
        if diff:
            try:
                resp = destination_client.put(
                    self.base_path + f"/{local_destination_resources[_id]['id']}", aws_logs_integration
                ).json()
            except HTTPError as e:
                log.error("error creating aws_logs_integration: %s", e.response.text)
                return
            local_destination_resources[_id] = resp
