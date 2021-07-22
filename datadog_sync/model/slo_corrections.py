from datadog_sync.utils.resource_utils import ResourceConnectionError
from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


class SLOCorrections(BaseResource):
    resource_type = "slo_corrections"
    resource_connections = {"service_level_objectives": ["attributes.slo_id"]}
    base_path = "/api/v1/slo/correction"
    excluded_attributes = ["root['id']", "root['attributes']['creator']"]

    def import_resources(self):
        source_client = self.config.source_client

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing slo_correction %s", e)
            return

        self.import_resources_concurrently(resp["data"])

    def process_resource_import(self, slo_correction):
        self.source_resources[slo_correction["id"]] = slo_correction

    def apply_resources(self):
        self.logger.info("Processing slo_corrections")
        self.apply_resources_concurrently()

    def prepare_resource_and_apply(self, _id, slo_correction):
        self.connect_resources(_id, slo_correction)
        self.remove_excluded_attr(slo_correction)

        if _id in self.destination_resources:
            self.update_resource(_id, slo_correction)
        else:
            self.create_resource(_id, slo_correction)

    def create_resource(self, _id, slo_correction):
        destination_client = self.config.destination_client

        payload = {"data": slo_correction}

        try:
            resp = destination_client.post(self.base_path, payload).json()
        except HTTPError as e:
            self.logger.error("error creating slo_correction: %s", e.response.text)
            return

        self.destination_resources[_id] = resp["data"]

    def update_resource(self, _id, slo_correction):
        destination_client = self.config.destination_client

        diff = self.check_diff(slo_correction, self.destination_resources[_id])
        if diff:
            try:
                payload = {"data": slo_correction}
                resp = destination_client.patch(
                    self.base_path + f"/{self.destination_resources[_id]['id']}", payload
                ).json()
            except HTTPError as e:
                self.logger.error("error updating slo_correction: %s", e.response.text)
                return
            self.destination_resources[_id] = resp["data"]