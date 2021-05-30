import os
import json

from datadog_sync.constants import RESOURCE_FILE_PATH
from datadog_sync.utils.resource_utils import replace


class BaseResource:
    def __init__(self, ctx, resource_type, resource_connections=None, resource_filter=None, computed_attributes=None):
        self.ctx = ctx
        self.resource_type = resource_type
        self.resource_filter = resource_filter
        self.computed_attributes = computed_attributes
        self.resource_connections = resource_connections

    def import_resources(self):
        pass

    def process_resource(self, *args):
        pass

    def open_resources(self):
        source_resources = dict()
        destination_resources = dict()

        source_path = RESOURCE_FILE_PATH.format("source", self.resource_type)
        destination_path = RESOURCE_FILE_PATH.format("destination", self.resource_type)
        with open(source_path, "r") as f:
            source_resources = json.load(f)
        if os.path.exists(destination_path):
            with open(destination_path, "r") as f:
                destination_resources = json.load(f)
        return source_resources, destination_resources

    def remove_computed_attr(self, resource):
        [resource.pop(key, None) for key in self.computed_attributes]

    def write_resources_file(self, origin, resources):
        # Write the resource to a file
        resource_path = RESOURCE_FILE_PATH.format(origin, self.resource_type)
        if os.path.exists(resource_path):
            with open(resource_path, "w") as f:
                json.dump(resources, f, indent=2)
        else:
            with open(resource_path, "a+") as f:
                json.dump(resources, f, indent=2)

    def connect_resources(self, resource, connection_resources_obj):
        for resource_to_connect, v in self.resource_connections.items():
            for attr_connection in v:
                replace(attr_connection.split("."), resource, resource_to_connect, connection_resources_obj)
