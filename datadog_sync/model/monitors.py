import copy
import logging
from concurrent.futures import ThreadPoolExecutor, wait

from datadog_sync.utils.base_resource import BaseResource
from deepdiff import DeepDiff


logging.basicConfig(level=logging.DEBUG)


RESOURCE_TYPE = "monitors"
COMPUTED_ATTRIBUTES = [
    "id",
    "matching_downtimes",
    "creator",
    "created",
    "deleted",
    "org_id",
    "created_at",
    "modified",
    "overall_state",
    "overall_state_modified",
]
BASE_PATH = "/api/v1/monitor"


class Monitors(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_TYPE, computed_attributes=COMPUTED_ATTRIBUTES)

    def import_resources(self):
        monitors = {}

        source_client = self.ctx.obj.get("source_client")
        res = source_client.get(BASE_PATH).json()
        with ThreadPoolExecutor() as executor:
            wait([executor.submit(self.process_resource, monitor, monitors) for monitor in res])

        # Write resources to file
        self.write_resources_file("source", monitors)

    def process_resource(self, monitor, monitors):
        monitors[monitor["id"]] = monitor

    def apply_resources(self):
        source_monitors, destination_monitors = self.open_resources()

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(self.prepare_resource_and_apply, _id, monitor, destination_monitors)
                    for _id, monitor in source_monitors.items()
                ]
            )

        self.write_resources_file("destination", destination_monitors)

    def prepare_resource_and_apply(self, _id, monitor, destination_monitors):
        destination_client = self.ctx.obj.get("destination_client")
        self.remove_computed_attr(monitor)

        if _id in destination_monitors:
            dest_monitor_copy = copy.deepcopy(destination_monitors[_id])
            self.remove_computed_attr(dest_monitor_copy)
            diff = DeepDiff(monitor, dest_monitor_copy, ignore_order=True)
            if diff:
                res = destination_client.put(BASE_PATH + f"/{destination_monitors[_id]['id']}", monitor).json()
                destination_monitors[_id] = res
        else:
            res = destination_client.post(BASE_PATH, monitor).json()
            destination_monitors[_id] = res
