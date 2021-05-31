from concurrent.futures import ThreadPoolExecutor, wait

from deepdiff import DeepDiff

from datadog_sync.utils.base_resource import BaseResource


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
        super().__init__(ctx, RESOURCE_TYPE)

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

        if _id in destination_monitors:
            diff = DeepDiff(monitor, destination_monitors[_id], ignore_order=True, exclude_paths=EXCLUDED_ATTRIBUTES)
            if diff:
                res = destination_client.put(BASE_PATH + f"/{destination_monitors[_id]['id']}", monitor).json()
                destination_monitors[_id] = res
        else:
            res = destination_client.post(BASE_PATH, monitor).json()
            destination_monitors[_id] = res
