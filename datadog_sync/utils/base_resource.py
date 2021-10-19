# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import abc
from dataclasses import dataclass, field
from concurrent.futures import wait
from pprint import pformat
from typing import Optional, Dict, List


from datadog_sync.constants import SOURCE_ORIGIN, DESTINATION_ORIGIN
from datadog_sync.utils.custom_client import CustomClient
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
    resource_connections: Optional[Dict[str, List[str]]] = None
    non_nullable_attr: Optional[List[str]] = None
    excluded_attributes: Optional[List[str]] = None
    excluded_attributes_re: Optional[List[str]] = None
    concurrent: bool = True
    source_resources: dict = field(default_factory=dict)
    destination_resources: dict = field(default_factory=dict)

    def __post_init__(self):
        self.build_excluded_attributes()

    def build_excluded_attributes(self):
        if self.excluded_attributes:
            for i, attr in enumerate(self.excluded_attributes):
                self.excluded_attributes[i] = "root" + "".join(["['{}']".format(v) for v in attr.split(".")])


class BaseResource(abc.ABC):
    resource_type: str
    resource_config: ResourceConfig

    def __init__(self, config):
        self.config = config
        self.resource_config.source_resources, self.resource_config.destination_resources = open_resources(
            self.resource_type
        )

    @abc.abstractmethod
    def get_resources(self, client: CustomClient) -> List[Dict]:
        pass

    @abc.abstractmethod
    def import_resource(self, resource: Dict) -> None:
        pass

    @abc.abstractmethod
    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    @abc.abstractmethod
    def pre_apply_hook(self, resources: Dict[str, Dict]) -> Optional[list]:
        pass

    @abc.abstractmethod
    def create_resource(self, _id: str, resource: Dict) -> None:
        pass

    @abc.abstractmethod
    def update_resource(self, _id: str, resource: Dict) -> None:
        pass

    @abc.abstractmethod
    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> None:
        resources = self.config.resources[resource_to_connect].resource_config.destination_resources
        if isinstance(r_obj[key], list):
            for i, v in enumerate(r_obj[key]):
                _id = str(v)
                if _id in resources:
                    # Cast resource id to str or int based on source type
                    type_attr = type(v)
                    r_obj[key][i] = type_attr(resources[_id]["id"])
                else:
                    raise ResourceConnectionError(resource_to_connect, _id=_id)
        else:
            _id = str(r_obj[key])
            if _id in resources:
                # Cast resource id to str on int based on source type
                type_attr = type(r_obj[key])
                r_obj[key] = type_attr(resources[_id]["id"])
            else:
                raise ResourceConnectionError(resource_to_connect, _id=_id)

    def import_resources(self) -> None:
        # reset source resources obj
        self.resource_config.source_resources.clear()

        try:
            get_resp = self.get_resources(self.config.source_client)
        except Exception as e:
            self.config.logger.error(f"error while importing resources {self.resource_type}: {str(e)}")
            return

        futures = []
        with thread_pool_executor(self.config.max_workers) as executor:
            for r in get_resp:
                if not self.filter(r):
                    continue
                futures.append(executor.submit(self.import_resource, r))

        for future in futures:
            try:
                future.result()
            except Exception as e:
                self.config.logger.error(f"error while importing resource {self.resource_type}: {str(e)}")

        write_resources_file(self.resource_type, SOURCE_ORIGIN, self.resource_config.source_resources)

    def apply_resources(self) -> None:
        max_workers = 1 if not self.resource_config.concurrent else self.config.max_workers

        # Run pre-apply hook with the resources
        try:
            resources_list = self.pre_apply_hook(self.resource_config.source_resources)
        except Exception as e:
            self.config.logger.error(f"error while applying resources {self.resource_type}: {str(e)}")
            return

        if not resources_list:
            resources_list = [self.resource_config.source_resources]
        futures = []
        with thread_pool_executor(max_workers) as executor:
            for r_list in resources_list:
                for _id, resource in r_list.items():
                    if not self.filter(resource):
                        continue
                    futures.append(executor.submit(self.apply_resource, _id, resource))
                wait(futures)

        for future in futures:
            try:
                future.result()
            except ResourceConnectionError:
                # This should already be handled in connect_resource method
                continue
            except Exception as e:
                self.config.logger.error(f"error while applying resource {self.resource_type}: {str(e)}")

        write_resources_file(self.resource_type, DESTINATION_ORIGIN, self.resource_config.destination_resources)

    def check_diffs(self):
        for _id, resource in self.resource_config.source_resources.items():
            if not self.filter(resource):
                continue

            self.pre_resource_action_hook(_id, resource)

            try:
                self.connect_resources(_id, resource)
            except ResourceConnectionError:
                continue

            if _id in self.resource_config.destination_resources:
                diff = check_diff(self.resource_config, self.resource_config.destination_resources[_id], resource)
                if diff:
                    print("{} resource source ID {} diff: \n {}".format(self.resource_type, _id, pformat(diff)))
            else:
                print("Resource to be added {} source ID {}: \n {}".format(self.resource_type, _id, pformat(resource)))

    def apply_resource(self, _id: str, resource: Dict) -> None:
        self.pre_resource_action_hook(_id, resource)
        self.connect_resources(_id, resource)

        if _id in self.resource_config.destination_resources:
            diff = check_diff(self.resource_config, resource, self.resource_config.destination_resources[_id])
            if diff:
                prep_resource(self.resource_config, resource)
                try:
                    self.update_resource(_id, resource)
                except Exception as e:
                    self.config.logger.error(
                        f"error while updating resource {self.resource_type}. source ID: {_id} -  Error: {str(e)}"
                    )
        else:
            prep_resource(self.resource_config, resource)
            try:
                self.create_resource(_id, resource)
            except Exception as e:
                self.config.logger.error(
                    f"error while creating resource {self.resource_type}. source ID: {_id} - Error: {str(e)}"
                )

    def connect_resources(self, _id: str, resource: Dict) -> None:
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

    def filter(self, resource: Dict) -> bool:
        if not self.config.filters or self.resource_type not in self.config.filters:
            return True

        for _filter in self.config.filters[self.resource_type]:
            if _filter.is_match(resource):
                return True
        # Filter was specified for resource type but resource did not match any
        return False
