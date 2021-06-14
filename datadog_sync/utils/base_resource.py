import os
import json
import re
import logging
from concurrent.futures import ThreadPoolExecutor
from pprint import pformat

from deepdiff import DeepDiff

from datadog_sync.constants import RESOURCE_FILE_PATH
from datadog_sync.utils.resource_utils import replace


log = logging.getLogger(__name__)


class BaseResource:
    def __init__(
        self,
        ctx,
        resource_type,
        base_path,
        excluded_attributes=None,
        resource_connections=None,
        resource_filter=None,
        excluded_attributes_re=None,
        non_nullable_attr=None,
    ):
        self.ctx = ctx
        self.resource_type = resource_type
        self.base_path = base_path
        self.excluded_attributes = excluded_attributes
        self.resource_filter = resource_filter
        self.resource_connections = resource_connections
        self.excluded_attributes_re = excluded_attributes_re
        self.non_nullable_attr = non_nullable_attr

    def import_resources(self):
        pass

    def import_resources_concurrently(self, resources_obj, resources):
        with ThreadPoolExecutor() as executor:
            [executor.submit(self.process_resource_import, resource, resources_obj) for resource in resources]

    def get_connection_resources(self):
        connection_resources = {}

        if self.resource_connections:
            for k in self.resource_connections.keys():
                path = RESOURCE_FILE_PATH.format("destination", k)
                if os.path.exists(path):
                    with open(RESOURCE_FILE_PATH.format("destination", k), "r") as f:
                        connection_resources[k] = json.load(f)
        return connection_resources

    def process_resource_import(self, *args):
        pass

    def remove_excluded_attr(self, resource):
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
        source_resources, local_destination_resources = self.open_resources()
        connection_resource_obj = self.get_connection_resources()

        for _id, resource in source_resources.items():
            if self.resource_connections:
                self.connect_resources(resource, connection_resource_obj)

            if _id in local_destination_resources:
                diff = self.check_diff(local_destination_resources[_id], resource)
                if diff:
                    log.info("%s resource ID %s diff: \n %s", self.resource_type, _id, pformat(diff))
            else:
                if resource.get("type") == "synthetics alert":
                    return
                log.info("Resource to be added %s: \n %s", self.resource_type, pformat(resource))

    def remove_non_nullable_attributes(self, resource):
        for key in self.non_nullable_attr:
            k_list = key.split(".")
            self.del_null_attr(k_list, resource)

    def apply_resources_concurrently(self, resources, local_destination_resources, connection_resource_obj, **kwargs):
        with ThreadPoolExecutor() as executor:
            futures = [
                executor.submit(
                    self.prepare_resource_and_apply,
                    _id,
                    resource,
                    local_destination_resources,
                    connection_resource_obj,
                    **kwargs,
                )
                for _id, resource in resources.items()
            ]
        for future in futures:
            try:
                future.result()
            except BaseException:
                log.exception("error while applying resource")

    def open_resources(self):
        destination_resources = dict()

        source_path = RESOURCE_FILE_PATH.format("source", self.resource_type)
        destination_path = RESOURCE_FILE_PATH.format("destination", self.resource_type)
        with open(source_path, "r") as f:
            source_resources = json.load(f)
        if os.path.exists(destination_path):
            with open(destination_path, "r") as f:
                destination_resources = json.load(f)
        return source_resources, destination_resources

    def write_resources_file(self, origin, resources):
        # Write the resource to a file
        resource_path = RESOURCE_FILE_PATH.format(origin, self.resource_type)

        with open(resource_path, "w") as f:
            json.dump(resources, f, indent=2)

    def connect_resources(self, resource, connection_resources_obj=None):
        if not connection_resources_obj:
            return
        for resource_to_connect, v in self.resource_connections.items():
            for attr_connection in v:
                replace(attr_connection, self.resource_type, resource, resource_to_connect, connection_resources_obj)
