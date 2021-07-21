import re

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.utils.resource_utils import ResourceConnectionError


class Monitors(BaseResource):
    resource_type = "monitors"
    resource_connections = {"monitors": ["query"], "roles": ["restricted_roles"]}
    base_path = "/api/v1/monitor"
    excluded_attributes = [
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

    def import_resources(self):
        source_client = self.config.source_client
        try:
            resp = source_client.get(self.base_path).json()
        except HTTPError as e:
            self.logger.error("error importing monitors %s", e)
            return

        self.import_resources_concurrently(resp)

    def process_resource_import(self, monitor):
        if not self.filter(monitor) or monitor["type"] == "synthetics alert":
            return

        self.source_resources[str(monitor["id"])] = monitor

    def apply_resources(self):
        simple_monitors = {}
        composite_monitors = {}

        for _id, monitor in self.source_resources.items():
            if monitor["type"] == "synthetics alert":
                continue
            if monitor["type"] == "composite":
                composite_monitors[_id] = monitor
            else:
                simple_monitors[_id] = monitor

        self.logger.info("Processing Simple Monitors")
        self.apply_resources_concurrently(resources=simple_monitors)

        self.logger.info("Processing Composite Monitors")
        self.apply_resources_concurrently(resources=composite_monitors)

    def prepare_resource_and_apply(self, _id, monitor, **kwargs):
        self.connect_resources(_id, monitor)

        if _id in self.destination_resources:
            self.update_resource(_id, monitor)
        else:
            self.create_resource(_id, monitor)

    def create_resource(self, _id, monitor):
        destination_client = self.config.destination_client

        try:
            resp = destination_client.post(self.base_path, monitor).json()
        except HTTPError as e:
            self.logger.error("error creating monitor: %s", e.response.text)
            return
        self.destination_resources[_id] = resp

    def update_resource(self, _id, monitor):
        destination_client = self.config.destination_client

        diff = self.check_diff(monitor, self.destination_resources[_id])
        if diff:
            try:
                resp = destination_client.put(
                    self.base_path + f"/{self.destination_resources[_id]['id']}", monitor
                ).json()
            except HTTPError as e:
                self.logger.error("error creating monitor: %s", e.response.text)
                return
            self.destination_resources[_id] = resp

    def connect_id(self, key, r_obj, resource_to_connect):
        resources = self.config.resources[resource_to_connect].destination_resources

        if r_obj.get("type") == "composite" and key == "query":
            ids = re.findall("[0-9]+", r_obj[key])
            for _id in ids:
                if _id in resources:
                    new_id = f"{resources[_id]['id']}"
                    r_obj[key] = re.sub(_id + r"([^#]|$)", new_id + "# ", r_obj[key])
                else:
                    raise ResourceConnectionError(resource_to_connect, _id=_id)
            r_obj[key] = (r_obj[key].replace("#", "")).strip()
