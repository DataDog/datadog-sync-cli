# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import os
import re
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from graphlib import TopologicalSorter

from deepdiff import DeepDiff

from datadog_sync.constants import RESOURCE_FILE_PATH, LOGGER_NAME
from datadog_sync.constants import SOURCE_ORIGIN, DESTINATION_ORIGIN


log = logging.getLogger(LOGGER_NAME)


class ResourceConnectionError(Exception):
    def __init__(self, failed_connections_dict):
        super(ResourceConnectionError, self).__init__(f"Failed to connect resource. {dict(failed_connections_dict)}")


class CustomClientHTTPError(Exception):
    def __init__(self, response):
        super().__init__(f"{response.status_code} {response.reason} - {response.text}")
        self.status_code = response.status_code


class LoggedException(Exception):
    """Raise this when an error was already logged."""


def find_attr(keys_list, resource_to_connect, r_obj, connect_func):
    if isinstance(r_obj, list):
        failed_connections = []
        for k in r_obj:
            failed = find_attr(keys_list, resource_to_connect, k, connect_func)
            if failed:
                failed_connections.extend(failed)
        return failed_connections
    else:
        keys_list = keys_list.split(".", 1)

        if len(keys_list) == 1 and keys_list[0] in r_obj:
            if not r_obj[keys_list[0]]:
                return
            return connect_func(keys_list[0], r_obj, resource_to_connect)

        if isinstance(r_obj, dict):
            if keys_list[0] in r_obj:
                return find_attr(keys_list[1], resource_to_connect, r_obj[keys_list[0]], connect_func)


def prep_resource(resource_config, resource):
    remove_excluded_attr(resource_config, resource)
    remove_non_nullable_attributes(resource_config, resource)


def remove_excluded_attr(resource_config, resource):
    if resource_config.excluded_attributes:
        for key in resource_config.excluded_attributes:
            k_list = re.findall("\\['(.*?)'\\]", key)
            del_attr(k_list, resource)


def remove_non_nullable_attributes(resource_config, resource):
    if resource_config.non_nullable_attr:
        for key in resource_config.non_nullable_attr:
            k_list = key.split(".")
            del_null_attr(k_list, resource)


def del_attr(k_list, resource):
    if len(k_list) == 1:
        resource.pop(k_list[0], None)
    else:
        if k_list[0] not in resource:
            return
        del_attr(k_list[1:], resource[k_list[0]])


def del_null_attr(k_list, resource):
    if len(k_list) == 1 and k_list[0] in resource and resource[k_list[0]] is None:
        resource.pop(k_list[0], None)
    elif len(k_list) > 1 and resource[k_list[0]] is not None:
        del_null_attr(k_list[1:], resource[k_list[0]])


def check_diff(resource_config, resource, state):
    return DeepDiff(
        resource,
        state,
        ignore_order=True,
        exclude_paths=resource_config.excluded_attributes,
        exclude_regex_paths=resource_config.excluded_attributes_re,
    )


def open_resources(resource_type):
    source_resources = dict()
    destination_resources = dict()

    source_path = RESOURCE_FILE_PATH.format("source", resource_type)
    destination_path = RESOURCE_FILE_PATH.format("destination", resource_type)

    if os.path.exists(source_path):
        with open(source_path, "r") as f:
            try:
                source_resources = json.load(f)
            except json.decoder.JSONDecodeError:
                log.warning(f"invalid json in source resource file: {resource_type}")

    if os.path.exists(destination_path):
        with open(destination_path, "r") as f:
            try:
                destination_resources = json.load(f)
            except json.decoder.JSONDecodeError:
                log.warning(f"invalid json in destination resource file: {resource_type}")

    return source_resources, destination_resources


def dump_resources(config, resource_types, origin):
    for resource_type in resource_types:
        if origin == SOURCE_ORIGIN:
            resources = config.resources[resource_type].resource_config.source_resources
        elif origin == DESTINATION_ORIGIN:
            resources = config.resources[resource_type].resource_config.destination_resources

        write_resources_file(resource_type, origin, resources)


def write_resources_file(resource_type, origin, resources):
    resource_path = RESOURCE_FILE_PATH.format(origin, resource_type)

    with open(resource_path, "w") as f:
        json.dump(resources, f, indent=2)


def thread_pool_executor(max_workers=None):
    return ThreadPoolExecutor(max_workers=max_workers)


def init_topological_sorter(graph):
    sorter = TopologicalSorter(graph)
    sorter.prepare()
    return sorter
