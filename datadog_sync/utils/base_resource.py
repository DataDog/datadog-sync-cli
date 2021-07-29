# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import abc
from dataclasses import dataclass
from typing import Optional
from concurrent.futures import wait

from datadog_sync.constants import SOURCE_ORIGIN, DESTINATION_ORIGIN
from datadog_sync.utils.resource_utils import (
    open_resources,
    write_resources_file,
    find_attr,
    ResourceConnectionError,
    thread_pool_executor,
    check_diff,
    prep_resource,
)


@dataclass
class ResourceConfig:
    base_path: str
    resource_connections: dict[str, list[str]] = None
    non_nullable_attr: Optional[list[str]] = None
    excluded_attributes: Optional[list[str]] = None
    excluded_attributes_re: Optional[list[str]] = None
    concurrent: bool = True
    source_resources: dict = None
    destination_resources: dict = None


class BaseResource(abc.ABC):
    def __init__(self, config):
        self.config = config
        self.resource_config.source_resources, self.resource_config.destination_resources = open_resources(
            self.resource_type
        )

    @classmethod
    def resource_type(cls):
        pass

    @property
    @abc.abstractmethod
    def resource_config(self):
        pass

    @abc.abstractmethod
    def get_resources(self, client) -> list:
        pass

    @abc.abstractmethod
    def import_resource(self, resource) -> None:
        pass

    @abc.abstractmethod
    def pre_resource_action_hook(self, resource) -> None:
        pass

    @abc.abstractmethod
    def pre_apply_hook(self, resources) -> Optional[list]:
        pass

    @abc.abstractmethod
    def create_resource(self, _id, resource) -> None:
        pass

    @abc.abstractmethod
    def update_resource(self, _id, resource) -> None:
        pass

    @abc.abstractmethod
    def connect_id(self, key, r_obj, resource_to_connect) -> None:
        resources = self.config.resources[resource_to_connect].resource_config.destination_resources
        _id = str(r_obj[key])
        if _id in resources:
            # Cast resource id to str on int based on source type
            type_attr = type(r_obj[key])
            r_obj[key] = type_attr(resources[_id]["id"])
        else:
            raise ResourceConnectionError(resource_to_connect, _id=_id)

    def import_resources(self) -> None:
        get_resp = self.get_resources(self.config.source_client)

        with thread_pool_executor(self.config.max_workers) as executor:
            futures = [executor.submit(self.import_resource, r) for r in get_resp]

        for future in futures:
            try:
                future.result()
            except Exception as e:
                self.config.logger.error(f"error while importing resource {self.resource_type}: {str(e)}")

        write_resources_file(self.resource_type, SOURCE_ORIGIN, self.resource_config.source_resources)

    def apply_resources(self) -> None:
        max_workers = 1 if not self.resource_config.concurrent else self.config.max_workers
        # Run pre-apply hook with the resources
        resources_list = self.pre_apply_hook(self.resource_config.source_resources)
        futures = []

        with thread_pool_executor(max_workers) as executor:
            if resources_list:
                for r_list in resources_list:
                    for _id, resource in r_list.items():
                        if not self.filter(resource):
                            return
                        futures.append(executor.submit(self.apply_resource, _id, resource))
                    wait(futures)
            else:
                for _id, resource in self.resource_config.source_resources.items():
                    if not self.filter(resource):
                        return
                    futures.append(executor.submit(self.apply_resource, _id, resource))

        for future in futures:
            try:
                future.result()
            except ResourceConnectionError:
                # This should already be handled in connect_resource method
                continue
            except Exception as e:
                self.config.logger.error(f"error while applying resource {self.resource_type}: {str(e)}")

        write_resources_file(self.resource_type, DESTINATION_ORIGIN, self.resource_config.destination_resources)

    def apply_resource(self, _id, resource) -> None:
        self.pre_resource_action_hook(resource)
        self.connect_resources(_id, resource)

        if _id in self.resource_config.destination_resources:
            diff = check_diff(self.resource_config, resource, self.resource_config.destination_resources[_id])
            if diff:
                prep_resource(self.resource_config, resource)
                self.update_resource(_id, resource)
        else:
            prep_resource(self.resource_config, resource)
            self.create_resource(_id, resource)

    def connect_resources(self, _id, resource) -> None:
        if not self.resource_config.resource_connections:
            return

        for resource_to_connect, v in self.resource_config.resource_connections.items():
            for attr_connection in v:
                try:
                    find_attr(attr_connection, resource_to_connect, resource, self.connect_id)
                except ResourceConnectionError as e:
                    if self.config.skip_failed_resource_connections:
                        self.config.logger.warning(f"Skipping resource: {self.resource_type} with ID: {_id}. {str(e)}")
                        raise e
                    else:
                        self.config.logger.warning(f"{self.resource_type} with ID: {_id}. {str(e)}")
                        continue

    def filter(self, resource) -> bool:
        if not self.config.filters or self.resource_type not in self.config.filters:
            return True

        for _filter in self.config.filters[self.resource_type]:
            if _filter.is_match(resource):
                return True
        # Filter was specified for resource type but resource did not match any
        return False
