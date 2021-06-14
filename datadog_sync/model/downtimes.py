from concurrent.futures import ThreadPoolExecutor, wait

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


RESOURCE_TYPE = "downtimes"
EXCLUDED_ATTRIBUTES = [
    "root['id']",
    "root['updater_id']",
    "root['created']",
    "root['org_id']",
    "root['modified']",
    "root['creator_id']",
    "root['active']",
]
RESOURCE_CONNECTIONS = {"monitors": ["monitor_id"]}
NON_NULLABLE_ATTRIBUTE = ["recurrence.until_date", "recurrence.until_occurrences"]
BASE_PATH = "/api/v1/downtime"


class Downtimes(BaseResource):
    def __init__(self, ctx):
        super().__init__(
            ctx,
            RESOURCE_TYPE,
            BASE_PATH,
            excluded_attributes=EXCLUDED_ATTRIBUTES,
            resource_connections=RESOURCE_CONNECTIONS,
            non_nullable_attr=NON_NULLABLE_ATTRIBUTE,
        )

    def import_resources(self):
        downtimes = {}
        source_client = self.ctx.obj.get("source_client")

        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing downtimes %s", e)
            return

        with ThreadPoolExecutor() as executor:
            wait([executor.submit(self.process_resource_import, downtime, downtimes) for downtime in resp])

        # Write resources to file
        self.write_resources_file("source", downtimes)

    def process_resource_import(self, downtime, downtimes):
        downtimes[downtime["id"]] = downtime

    def apply_resources(self):
        source_resources, local_destination_resources = self.open_resources()
        connection_resource_obj = self.get_connection_resources()
        self.apply_resources_concurrently(source_resources, local_destination_resources, connection_resource_obj)
        self.write_resources_file("destination", local_destination_resources)

    def prepare_resource_and_apply(self, _id, downtime, local_destination_resources, connection_resource_obj=None):
        if self.resource_connections:
            self.connect_resources(downtime, connection_resource_obj)

        if _id in local_destination_resources:
            self.update_resource(_id, downtime, local_destination_resources)
        else:
            self.create_resource(_id, downtime, local_destination_resources)

    def create_resource(self, _id, downtime, local_destination_resources):
        destination_client = self.ctx.obj.get("destination_client")
        self.remove_non_nullable_attributes(downtime)
        try:
            resp = destination_client.post(self.base_path, downtime).json()
        except HTTPError as e:
            self.logger.error("error creating downtime: %s", e.response.text)
            return
        local_destination_resources[_id] = resp

    def update_resource(self, _id, downtime, local_destination_resources):
        destination_client = self.ctx.obj.get("destination_client")

        diff = self.check_diff(downtime, local_destination_resources[_id])
        self.remove_non_nullable_attributes(downtime)
        if diff:
            try:
                resp = destination_client.put(
                    self.base_path + f"/{local_destination_resources[_id]['id']}", downtime
                ).json()
            except HTTPError as e:
                self.logger.error("error creating downtime: %s", e.response.text)
                return
            local_destination_resources[_id] = resp
