# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import re
import logging
from copy import deepcopy
from graphlib import TopologicalSorter
from dateutil.parser import parse

from deepdiff import DeepDiff
from deepdiff.operator import BaseOperator

from datadog_sync.constants import LOGGER_NAME
from typing import Callable, List, Optional, Set, TYPE_CHECKING, Any, Dict, Tuple

if TYPE_CHECKING:
    from datadog_sync.utils.configuration import Configuration


log = logging.getLogger(LOGGER_NAME)


DEFAULT_TAGS = ["managed_by:datadog-sync"]


class SkipResource(Exception):
    def __init__(self, _id: str, _type: str, msg: str):
        super(SkipResource, self).__init__(f"Skipping {_type} with id: {_id}. {msg}")


class ResourceConnectionError(Exception):
    def __init__(self, failed_connections_dict):
        super(ResourceConnectionError, self).__init__(f"Failed to connect resource. {dict(failed_connections_dict)}")


class CustomClientHTTPError(Exception):
    def __init__(self, response, message=None):
        super().__init__(f"{response.status} {response.message} - {message}")
        self.status_code = response.status


class LogsPipelinesOrderIdsComparator(BaseOperator):
    def match(self, level):
        if "pipeline_ids" in level.t1 and "pipeline_ids" in level.t2:
            # make copy so we do not mutate the original
            level.t1 = deepcopy(level.t1)
            level.t2 = deepcopy(level.t2)

            # If we are at the top level, modify the list to exclude extra pipelines in destination.
            t1 = set(level.t1["pipeline_ids"])
            t2 = set(level.t2["pipeline_ids"])
            d_ignore = t1 - t2

            level.t1["pipeline_ids"] = [_id for _id in level.t1["pipeline_ids"] if _id not in d_ignore]
        return True

    def give_up_diffing(self, level, diff_instance) -> bool:
        return False


class LogsIndexesOrderNameComparator(BaseOperator):
    def match(self, level):
        if "index_names" in level.t1 and "index_names" in level.t2:
            # make copy so we do not mutate the original
            level.t1 = deepcopy(level.t1)
            level.t2 = deepcopy(level.t2)

            # If we are at the top level, modify the list to exclude extra index in destination.
            t1 = set(level.t1["index_names"])
            t2 = set(level.t2["index_names"])
            d_ignore = t1 - t2

            level.t1["index_names"] = [_id for _id in level.t1["index_names"] if _id not in d_ignore]
        return True

    def give_up_diffing(self, level, diff_instance) -> bool:
        return False


RECURRENCE_START_ATTR_PATH_RE = r"root\['attributes'\]\['schedule'\]\['recurrences'\]\[[0-9]+\]\['start'\]"


class DowntimeSchedulesDateOperator(BaseOperator):
    def match(self, level):
        if re.match(RECURRENCE_START_ATTR_PATH_RE, level.path()):
            return True
        return False

    def give_up_diffing(self, level, diff_instance) -> bool:
        try:
            t1 = parse(level.t1)
            t2 = parse(level.t2)

            if t1 == t2:
                return True
        except Exception:
            pass

        return False

    def normalize_value_for_hashing(self, parent: Any, obj: Any) -> Any:
        """
        Used for ignore_order=True (required in later versions of deepdiff)
        """
        return obj


async def create_global_downtime(config: Configuration):
    """Create global downtime"""
    payload = {
        "data": {
            "attributes": {
                "message": "Downtime created by datadog-sync-cli to mute all monitors synced. "
                "To be manually removed during failover when monitors have enough telemetry"
                "to trigger appropriately.",
                "monitor_identifier": {"monitor_tags": DEFAULT_TAGS},
                "scope": "*",
                "schedule": {
                    "start": None,
                },
            },
            "type": "downtime",
        }
    }

    try:
        resp = await config.destination_client.post(
            config.resources["downtime_schedules"].resource_config.base_path, payload
        )
        config.logger.info(f"Global downtime for datadog-sync-cli created successfully - {resp['data']['id']}")
    except CustomClientHTTPError as e:
        if e.status_code == 400 and "downtime being created is a duplicate" in str(e):
            config.logger.info("Global downtime for datadog-sync-cli already exists")
        else:
            config.logger.error(f"Error creating global downtime for datadog-sync-cli: {str(e)}")
    except Exception as e:
        config.logger.error(f"Error creating global downtime for datadog-sync-cli: {str(e)}")


def find_attr(keys_list_str: str, resource_to_connect: str, r_obj: Any, connect_func: Callable) -> Optional[List[str]]:
    if not r_obj:
        return None

    if isinstance(r_obj, list):
        failed_connections = []
        for k in r_obj:
            failed = find_attr(keys_list_str, resource_to_connect, k, connect_func)
            if failed:
                failed_connections.extend(failed)
        return failed_connections
    else:
        keys_list = keys_list_str.split(".", 1)

        if len(keys_list) == 1 and keys_list[0] in r_obj:
            if not r_obj[keys_list[0]]:
                return None
            return connect_func(keys_list[0], r_obj, resource_to_connect)

        if isinstance(r_obj, dict):
            if keys_list[0] in r_obj:
                return find_attr(keys_list[1], resource_to_connect, r_obj[keys_list[0]], connect_func)
        return None


def prep_resource(resource_config, resource):
    remove_excluded_attr(resource_config, resource)
    remove_non_nullable_attributes(resource_config, resource)
    remove_non_nullable_list_vals(resource_config, resource)


def remove_excluded_attr(resource_config, resource):
    if resource_config.excluded_attributes:
        for key in resource_config.excluded_attributes:
            k_list = re.findall("\\['(.*?)'\\]", key)
            del_attr(k_list, resource)


def remove_non_nullable_attributes(resource_config, resource):
    if resource_config.non_nullable_attr:
        for key in resource_config.non_nullable_attr:
            k_list = key.split(".")
            del_null_attr(resource_config, k_list, resource)


def remove_non_nullable_list_vals(resource_config, resource):
    if resource_config.non_nullable_list_vals:
        for key, val in resource_config.non_nullable_list_vals:
            k_list = key.split(".")
            del_list_val(k_list, resource, None, val)


def del_list_val(k_list, resource, key, val):
    if len(k_list) > 0:
        key = k_list.pop(0)
        del_list_val(k_list, resource[key], key, val)
        return

    if not isinstance(resource, list):
        log.error(f"resource: {resource} is not a list")

    try:
        index_of_val = resource.index(val)
        resource.pop(index_of_val)
        log.debug(f"Removed {val} from list {key}")
    except ValueError as err:
        log.debug(f"{val} not in list {key}, err: {err}")


def del_attr(k_list, resource):
    if isinstance(resource, list):
        for r in resource:
            del_attr(k_list, r)
        return

    if len(k_list) == 1:
        resource.pop(k_list[0], None)
    else:
        if k_list[0] not in resource:
            return
        del_attr(k_list[1:], resource[k_list[0]])


def del_null_attr(resource_config, k_list, resources):
    # the nulls get converted to "something", this converts them back
    if not isinstance(resources, list):
        resources = [resources]

    for resource in resources:
        if resource_config.null_values and len(k_list) == 1 and k_list[0] in resource_config.null_values:
            if k_list[0] in resource and resource[k_list[0]] in resource_config.null_values[k_list[0]]:
                resource[k_list[0]] = None

        if len(k_list) == 1 and k_list[0] in resource and resource[k_list[0]] is None:
            resource.pop(k_list[0], None)
        elif len(k_list) > 1 and resource[k_list[0]] is not None:
            del_null_attr(resource_config, k_list[1:], resource[k_list[0]])


def check_diff(resource_config, resource, state):
    diff = DeepDiff(
        resource,
        state,
        exclude_paths=resource_config.excluded_attributes,
        **resource_config.deep_diff_config,
    )
    # log.info(f"diff: {diff}") # this debug statement will break tests that look for diffs
    return diff


def init_topological_sorter(graph: Dict[Tuple[str, str], Set[Tuple[str, str]]]) -> TopologicalSorter:
    sorter = TopologicalSorter(graph)
    sorter.prepare()
    return sorter
