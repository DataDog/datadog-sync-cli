import logging
from concurrent.futures import ThreadPoolExecutor, wait

from deepdiff import DeepDiff
from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


log = logging.getLogger("__name__")


RESOURCE_TYPE = "downtimes"
EXCLUDED_ATTRIBUTES = [
    "root['id']",
    "root['updater_id']",
    "root['created']",
    "root['org_id']",
    "root['modified']",
    "root['creator_id']",
]
RESOURCE_CONNECTIONS = {"monitors": ["monitor_id"]}
BASE_PATH = "/api/v1/downtime"


class Downtimes(BaseResource):
    def __init__(self, ctx):
        super().__init__(
            ctx,
            RESOURCE_TYPE,
            BASE_PATH,
            excluded_attributes=EXCLUDED_ATTRIBUTES,
            resource_connections=RESOURCE_CONNECTIONS,
        )

    def import_resources(self):
        downtimes = {}
        source_client = self.ctx.obj.get("source_client")

        try:
            resp = source_client.get(BASE_PATH).json()
        except HTTPError as e:
            log.error("error importing downtimes %s", e)
            return

        with ThreadPoolExecutor() as executor:
            wait([executor.submit(self.process_resource, downtime, downtimes) for downtime in resp])

        # Write resources to file
        self.write_resources_file("source", downtimes)

    def process_resource(self, downtime, downtimes):
        downtimes[downtime["id"]] = downtime

    def apply_resources(self):
        source_resources, local_destination_resources = self.open_resources()
        connection_resource_obj = self.get_connection_resources()

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(
                        self.prepare_resource_and_apply,
                        _id,
                        downtime,
                        local_destination_resources,
                        connection_resource_obj,
                    )
                    for _id, downtime in source_resources.items()
                ]
            )

        self.write_resources_file("destination", local_destination_resources)

    def prepare_resource_and_apply(self, _id, downtime, local_destination_resources, connection_resource_obj=None):
        destination_client = self.ctx.obj.get("destination_client")
        if self.resource_connections:
            self.connect_resources(downtime, connection_resource_obj)

        if _id in local_destination_resources:
            diff = DeepDiff(
                downtime, local_destination_resources[_id], ignore_order=True, exclude_paths=self.excluded_attributes
            )
            if diff:
                try:
                    resp = destination_client.put(
                        self.base_path + f"/{local_destination_resources[_id]['id']}", downtime
                    ).json()
                except HTTPError as e:
                    log.error("error creating downtime: %s", e.response.text)
                    return
                local_destination_resources[_id] = resp
        else:
            try:
                resp = destination_client.post(self.base_path, downtime).json()
            except HTTPError as e:
                log.error("error creating downtime: %s", e.response.text)
                return
            local_destination_resources[_id] = resp
