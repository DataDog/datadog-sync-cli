import re

from requests.exceptions import HTTPError

from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.constants import SOURCE_ORIGIN, DESTINATION_ORIGIN


class SyntheticsPrivateLocations(BaseResource):
    resource_type = "synthetics_private_locations"
    resource_connections = None
    base_locations_path = "/api/v1/synthetics/locations"
    base_path = "/api/v1/synthetics/private-locations"
    pl_id_regex = re.compile("^pl:.*")
    excluded_attributes = [
        "root['id']",
        "root['modifiedAt']",
        "root['createdAt']",
        "root['metadata']",
        "root['secrets']",
        "root['config']",
    ]
    match_on = "name"

    def import_resources(self):
        source_client = self.config.source_client

        try:
            resp = source_client.get(self.base_locations_path).json()
        except HTTPError as e:
            self.logger.error("error importing synthetics_private_locations: %s", e)
            return

        if self.config.import_existing:
            self.populate_destination_existing_resources()

        self.import_resources_concurrently(resp["locations"])

    def process_resource_import(self, synthetics_private_location):
        _id = synthetics_private_location["id"]
        if not self.filter(synthetics_private_location):
            return
        if not self.pl_id_regex.match(_id):
            return

        self.source_resources[_id] = self.get_private_location(_id, SOURCE_ORIGIN)

        # Map existing resources
        if self.config.import_existing:
            if synthetics_private_location[self.match_on] in self.destination_existing_resources:
                existing_pl = self.destination_existing_resources[synthetics_private_location[self.match_on]]
                self.destination_resources[str(_id)] = self.get_private_location(existing_pl["id"], DESTINATION_ORIGIN)

    def populate_destination_existing_resources(self):
        destination_client = self.config.destination_client

        try:
            resp = destination_client.get(self.base_locations_path).json()
        except HTTPError as e:
            self.logger.error("error fetching destination monitors %s", e)
            return

        for location in resp["locations"]:
            if not self.pl_id_regex.match(location["id"]):
                continue

            self.destination_existing_resources[location[self.match_on]] = location

    def apply_resources(self):
        connection_resource_obj = self.get_connection_resources()
        self.apply_resources_concurrently(connection_resource_obj)

    def prepare_resource_and_apply(self, _id, synthetics_private_location, connection_resource_obj):
        self.connect_resources(synthetics_private_location, connection_resource_obj)

        if _id in self.destination_resources:
            self.update_resource(_id, synthetics_private_location)
        else:
            self.create_resource(_id, synthetics_private_location)

    def create_resource(self, _id, synthetics_private_location):
        destination_client = self.config.destination_client
        self.remove_excluded_attr(synthetics_private_location)

        try:
            resp = destination_client.post(self.base_path, synthetics_private_location).json()["private_location"]
        except HTTPError as e:
            self.logger.error("error creating synthetics_private_location: %s", e.response.text)
            return
        self.destination_resources[_id] = resp

    def update_resource(self, _id, synthetics_private_location):
        destination_client = self.config.destination_client
        self.remove_excluded_attr(synthetics_private_location)

        diff = self.check_diff(synthetics_private_location, self.destination_resources[_id])
        if diff:
            try:
                resp = destination_client.put(
                    self.base_path + f"/{self.destination_resources[_id]['id']}", synthetics_private_location
                ).json()
            except HTTPError as e:
                self.logger.error("error creating synthetics_private_location: %s", e.response.text)
                return
            self.destination_resources[_id].update(resp)

    def get_private_location(self, _id, origin):
        if origin == SOURCE_ORIGIN:
            client = self.config.source_client
        else:
            client = self.config.destination_client

        try:
            pl = client.get(self.base_path + f"/{_id}").json()
        except HTTPError as e:
            self.logger.error(
                "error getting synthetics_private_location %s: %s",
                _id,
                e.response.text,
            )
            return
        return pl
