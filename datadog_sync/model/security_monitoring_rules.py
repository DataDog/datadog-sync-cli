
from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.utils.custom_client import paginated_request


class SecurityMonitoringRules(BaseResource):
    resource_type = "security_monitoring_rules"
    resource_connections = None
    base_path = "/api/v2/security_monitoring/rules"
    excluded_attributes = [
        "root['data']['id']",
        "root['data']['createdAt']",
        "root['data']['creationAuthorId']",
        "root['data']['updateAuthorId']",
    ]

    def import_resources(self):
        source_client = self.config.source_client

        try:
            resp = paginated_request(source_client.get)(self.base_path)
        except HTTPError as e:
            self.logger.error("error importing security_monitoring_rule %s", e)
            return

        self.import_resources_concurrently(resp)

    def process_resource_import(self, security_monitoring_rule):
        self.source_resources[security_monitoring_rule["id"]] = security_monitoring_rule

    def apply_resources(self):
        self.logger.info("Processing service_level_objectives")

        connection_resource_obj = self.get_connection_resources()

        self.apply_resources_concurrently(
            connection_resource_obj,
        )

    def prepare_resource_and_apply(self, _id, security_monitoring_rule, connection_resource_obj):
        self.connect_resources(security_monitoring_rule, connection_resource_obj)

        if _id in self.destination_resources:
            self.update_resource(_id, security_monitoring_rule)
        else:
            self.create_resource(_id, security_monitoring_rule)

    def create_resource(self, _id, security_monitoring_rule):
        destination_client = self.config.destination_client

        try:
            resp = destination_client.post(self.base_path, security_monitoring_rule).json()
        except HTTPError as e:
            self.logger.error("error creating security_monitoring_rule: %s", e.response.text)
            return

        self.destination_resources[_id] = resp["data"][0]

    def update_resource(self, _id, security_monitoring_rule):
        destination_client = self.config.destination_client

        diff = self.check_diff(security_monitoring_rule, self.destination_resources[_id])
        if diff:
            try:
                resp = destination_client.put(self.base_path + f"/{self.destination_resources[_id]['id']}", security_monitoring_rule).json()
            except HTTPError as e:
                self.logger.error("error creating security_monitoring_rule: %s", e.response.text)
                return
            self.destination_resources[_id] = resp["data"][0]
