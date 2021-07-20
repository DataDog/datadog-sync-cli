import os
import json
import re
from pprint import pformat

from deepdiff import DeepDiff

from datadog_sync.constants import RESOURCE_FILE_PATH
from datadog_sync.utils.resource_utils import replace, thread_pool_executor


class BaseResource:
    resource_type = None
    resource_connections = None
    base_path = None
    non_nullable_attr = None
    resource_filter = None
    excluded_attributes = None
    excluded_attributes_re = None

    def __init__(self, config):
        self.config = config
        self.logger = config.logger
        self.source_resources = dict()
        self.destination_resources = dict()
        # Load in resources on initialization
        self.open_resources()

    def import_resources(self):
        pass

    def import_resources_concurrently(self, resources):
        with thread_pool_executor(self.config.max_workers) as executor:
            futures = [executor.submit(self.process_resource_import, resource) for resource in resources]
            for future in futures:
                try:
                    future.result()
                except BaseException:
                    self.logger.exception(f"error while importing resource {self.resource_type}")

    def get_connection_resources(self):
        connection_resources = {}

        if self.resource_connections:
            for k in self.resource_connections:
                if k in self.config.resources:
                    connection_resources[k] = self.config.resources[k].destination_resources
                else:
                    self.logger.warning(f"{k} not found in resource_connections for {self.resource_type}")

        return connection_resources

    def process_resource_import(self, *args):
        pass

    def remove_excluded_attr(self, resource):
        if self.excluded_attributes:
            for key in self.excluded_attributes:
                k_list = re.findall("\\['(.*?)'\\]", key)
                self.del_attr(k_list, resource)

    def prepare_resource_and_apply(self, *args, **kwargs):
        pass

    def del_attr(self, k_list, resource):
        if len(k_list) == 1:
            resource.pop(k_list[0], None)
        else:
            self.del_attr(k_list[1:], resource[k_list[0]])

    def del_null_attr(self, k_list, resource):
        if len(k_list) == 1 and resource[k_list[0]] is None:
            resource.pop(k_list[0], None)
        elif len(k_list) > 1 and resource[k_list[0]] is not None:
            self.del_null_attr(k_list[1:], resource[k_list[0]])

    def check_diff(self, resource, state):
        return DeepDiff(
            resource,
            state,
            ignore_order=True,
            exclude_paths=self.excluded_attributes,
            exclude_regex_paths=self.excluded_attributes_re,
        )

    def check_diffs(self):
        connection_resource_obj = self.get_connection_resources()

        for _id, resource in self.source_resources.items():
            if resource.get("type") == "synthetics alert":
                continue
            if self.resource_connections:
                self.connect_resources(resource, connection_resource_obj)

            if _id in self.destination_resources:
                diff = self.check_diff(self.destination_resources[_id], resource)
                if diff:
                    print("{} resource ID {} diff: \n {}".format(self.resource_type, _id, pformat(diff)))
            else:
                print("Resource to be added {}: \n {}".format(self.resource_type, pformat(resource)))

    def remove_non_nullable_attributes(self, resource):
        if self.non_nullable_attr:
            for key in self.non_nullable_attr:
                k_list = key.split(".")
                self.del_null_attr(k_list, resource)

    def apply_resources_sequentially(self, connection_resource_obj, **kwargs):
        resources = kwargs.get("resources") or self.source_resources
        for _id, resource in resources.items():
            try:
                self.prepare_resource_and_apply(_id, resource, connection_resource_obj, **kwargs)
            except BaseException:
                self.logger.exception(f"error while applying resource {self.resource_type}")

    def apply_resources_concurrently(self, connection_resource_obj, **kwargs):
        resources = kwargs.get("resources")
        if resources == None:
            resources = self.source_resources
        with thread_pool_executor(self.config.max_workers) as executor:
            futures = [
                executor.submit(
                    self.prepare_resource_and_apply,
                    _id,
                    resource,
                    connection_resource_obj,
                    **kwargs,
                )
                for _id, resource in resources.items()
            ]
        for future in futures:
            try:
                future.result()
            except BaseException:
                self.logger.exception(f"error while applying resource {self.resource_type}")

    def open_resources(self):
        source_resources = dict()
        destination_resources = dict()

        source_path = RESOURCE_FILE_PATH.format("source", self.resource_type)
        destination_path = RESOURCE_FILE_PATH.format("destination", self.resource_type)

        if os.path.exists(source_path):
            with open(source_path, "r") as f:
                try:
                    source_resources = json.load(f)
                except json.decoder.JSONDecodeError:
                    self.logger.warning(f"invalid json in source resource file: {self.resource_type}")

        if os.path.exists(destination_path):
            with open(destination_path, "r") as f:
                try:
                    destination_resources = json.load(f)
                except json.decoder.JSONDecodeError:
                    self.logger.warning(f"invalid json in destination resource file: {self.resource_type}")

        self.source_resources = source_resources
        self.destination_resources = destination_resources

    def write_resources_file(self, origin, resources):
        resource_path = RESOURCE_FILE_PATH.format(origin, self.resource_type)

        with open(resource_path, "w") as f:
            json.dump(resources, f, indent=2)

    def connect_resources(self, resource, connection_resources_obj=None):
        if not (connection_resources_obj or self.resource_connections):
            return
        for resource_to_connect, v in self.resource_connections.items():
            for attr_connection in v:
                replace(attr_connection, self.resource_type, resource, resource_to_connect, connection_resources_obj)

    def filter(self, resource):
        if not self.config.filters or self.resource_type not in self.config.filters:
            return True

        for _filter in self.config.filters[self.resource_type]:
            if _filter.is_match(resource):
                return True
        # Filter was specified for resource type but resource did not match any
        return False
