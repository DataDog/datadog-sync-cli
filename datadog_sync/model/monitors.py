import logging
from concurrent.futures import ThreadPoolExecutor, wait

from deepdiff import DeepDiff
from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


log = logging.getLogger("__name__")


RESOURCE_TYPE = "monitors"
EXCLUDED_ATTRIBUTES = [
    "root['id']",
    "root['matching_downtimes']",
    "root['creator']",
    "root['created']",
    "root['deleted']",
    "root['org_id']",
    "root['created_at']",
    "root['modified']",
    "root['overall_state']",
    "root['overall_state_modified']",
]
BASE_PATH = "/api/v1/monitor"


class Monitors(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_TYPE, BASE_PATH, excluded_attributes=EXCLUDED_ATTRIBUTES)

    def import_resources(self):
        monitors = {}
        source_client = self.ctx.obj.get("source_client")

        try:
            resp = source_client.get(BASE_PATH).json()
        except HTTPError as e:
            log.error("error importing monitors: %", e)
            return

        with ThreadPoolExecutor() as executor:
            wait([executor.submit(self.process_resource, monitor, monitors) for monitor in resp])

        # Write resources to file
        self.write_resources_file("source", monitors)

    def process_resource(self, monitor, monitors):
        monitors[monitor["id"]] = monitor

    def apply_resources(self):
        source_resources, destination_resources = self.open_resources()
        connection_resource_obj = self.get_connection_resources()

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(
                        self.prepare_resource_and_apply,
                        _id,
                        resource,
                        destination_resources,
                        connection_resource_obj,
                    )
                    for _id, resource in source_resources.items()
                ]
            )

        self.write_resources_file("destination", destination_resources)

    def prepare_resource_and_apply(self, _id, resource, local_resources, connection_resource_obj=None):
        destination_client = self.ctx.obj.get("destination_client")
        if self.resource_connections:
            self.connect_resources(resource, connection_resource_obj)

        if _id in local_resources:
            diff = DeepDiff(resource, local_resources[_id], ignore_order=True, exclude_paths=self.excluded_attributes)
            if diff:
                try:
                    resp = destination_client.put(self.base_path + f"/{local_resources[_id]['id']}", resource).json()
                except HTTPError as e:
                    log.error("error creating monitor: %s", e.response.text)
                    return
                local_resources[_id] = resp
        else:
            try:
                resp = destination_client.post(self.base_path, resource).json()
            except HTTPError as e:
                log.error("error creating monitor: %s", e.response.text)
                return
            local_resources[_id] = resp
