import re
import logging
from concurrent.futures import ThreadPoolExecutor, wait

from deepdiff import DeepDiff
from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource


log = logging.getLogger("__name__")


RESOURCE_TYPE = "synthetics_private_locations"
EXCLUDED_ATTRIBUTES = [
    "root['id']",
    "root['modifiedAt']",
    "root['createdAt']",
    "root['metadata']",
    "root['secrets']",
    "root['config']",
]
BASE_LOCATIONS_PATH = "/api/v1/synthetics/locations"
BASE_PATH = "/api/v1/synthetics/private-locations"
PL_ID_REGEX = re.compile("^pl:.*")


class SyntheticsPrivateLocations(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_TYPE, BASE_PATH, excluded_attributes=EXCLUDED_ATTRIBUTES)

    def import_resources(self):
        synthetics_private_locations = {}
        source_client = self.ctx.obj.get("source_client")

        try:
            resp = source_client.get(BASE_LOCATIONS_PATH).json()
        except HTTPError as e:
            log.error("error importing synthetics_private_locations: %s", e)
            return

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(self.process_resource, synthetics_private_location, synthetics_private_locations)
                    for synthetics_private_location in resp["locations"]
                ]
            )

        # Write resources to file
        self.write_resources_file("source", synthetics_private_locations)

    def process_resource(self, synthetics_private_location, synthetics_private_locations):
        source_client = self.ctx.obj.get("source_client")
        if PL_ID_REGEX.match(synthetics_private_location["id"]):
            try:
                pl = source_client.get(BASE_PATH + f"/{synthetics_private_location['id']}").json()
            except HTTPError as e:
                log.error(
                    "error getting synthetics_private_location %s: %s",
                    synthetics_private_location["id"],
                    e.response.text,
                )
                return
            synthetics_private_locations[synthetics_private_location["id"]] = pl

    def apply_resources(self):
        source_resources, local_destination_resources = self.open_resources()
        connection_resource_obj = self.get_connection_resources()

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(
                        self.prepare_resource_and_apply,
                        _id,
                        synthetics_private_location,
                        local_destination_resources,
                        connection_resource_obj,
                    )
                    for _id, synthetics_private_location in source_resources.items()
                ]
            )

        self.write_resources_file("destination", local_destination_resources)

    def prepare_resource_and_apply(
        self, _id, synthetics_private_location, local_destination_resources, connection_resource_obj=None
    ):
        destination_client = self.ctx.obj.get("destination_client")
        if self.resource_connections:
            self.connect_resources(synthetics_private_location, connection_resource_obj)

        self.remove_excluded_attr(synthetics_private_location)

        if _id in local_destination_resources:
            diff = DeepDiff(
                synthetics_private_location,
                local_destination_resources[_id],
                ignore_order=True,
                exclude_paths=self.excluded_attributes,
            )
            if diff:
                try:
                    print("hello", diff)
                    resp = destination_client.put(
                        self.base_path + f"/{local_destination_resources[_id]['id']}", synthetics_private_location
                    ).json()
                except HTTPError as e:
                    log.error("error creating synthetics_private_location: %s", e.response.text)
                    return
                local_destination_resources[_id].update(resp)
        else:
            try:
                resp = destination_client.post(self.base_path, synthetics_private_location).json()["private_location"]
            except HTTPError as e:
                log.error("error creating synthetics_private_location: %s", e.response.text)
                return
            local_destination_resources[_id] = resp
