# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import abc
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Dict, List

from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.resource_utils import open_resources, find_attr, ResourceConnectionError

if TYPE_CHECKING:
    from datadog_sync.utils.configuration import Configuration


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

    def __post_init__(self) -> None:
        self.build_excluded_attributes()

    def build_excluded_attributes(self) -> None:
        if self.excluded_attributes:
            for i, attr in enumerate(self.excluded_attributes):
                self.excluded_attributes[i] = "root" + "".join(["['{}']".format(v) for v in attr.split(".")])


class BaseResource(abc.ABC):
    resource_type: str
    resource_config: ResourceConfig

    def __init__(self, config: Configuration) -> None:
        self.config = config
        self.resource_config.source_resources, self.resource_config.destination_resources = open_resources(
            self.resource_type
        )

    @abc.abstractmethod
    def get_resources(self, client: CustomClient) -> List[Dict]:
        pass

    @abc.abstractmethod
    def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> None:
        pass

    @abc.abstractmethod
    def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    @abc.abstractmethod
    def pre_apply_hook(self) -> None:
        pass

    @abc.abstractmethod
    def create_resource(self, _id: str, resource: Dict) -> None:
        pass

    @abc.abstractmethod
    def update_resource(self, _id: str, resource: Dict) -> None:
        pass

    @abc.abstractmethod
    def delete_resource(self, _id: str) -> None:
        pass

    @abc.abstractmethod
    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        resources = self.config.resources[resource_to_connect].resource_config.destination_resources
        failed_connections = []
        if isinstance(r_obj[key], list):
            for i, v in enumerate(r_obj[key]):
                _id = str(v)
                if _id in resources:
                    # Cast resource id to str or int based on source type
                    type_attr = type(v)
                    r_obj[key][i] = type_attr(resources[_id]["id"])
                else:
                    failed_connections.append(_id)
        else:
            _id = str(r_obj[key])
            if _id in resources:
                # Cast resource id to str on int based on source type
                type_attr = type(r_obj[key])
                r_obj[key] = type_attr(resources[_id]["id"])
            else:
                failed_connections.append(_id)

        return failed_connections

    def connect_resources(self, _id: str, resource: Dict) -> None:
        if not self.resource_config.resource_connections:
            return

        failed_connections_dict = defaultdict(list)
        for resource_to_connect, v in self.resource_config.resource_connections.items():
            for attr_connection in v:
                c = find_attr(attr_connection, resource_to_connect, resource, self.connect_id)
                if c:
                    failed_connections_dict[resource_to_connect].extend(c)

        if failed_connections_dict:
            e = ResourceConnectionError(failed_connections_dict=failed_connections_dict)
            if self.config.skip_failed_resource_connections:
                e = ResourceConnectionError(failed_connections_dict=failed_connections_dict)
                self.config.logger.info(f"Skipping resource: {self.resource_type} with ID: {_id}. {str(e)}")
                raise e
            else:
                self.config.logger.warning(f"{self.resource_type} with ID: {_id}. {str(e)}")

    def filter(self, resource: Dict) -> bool:
        if not self.config.filters or self.resource_type not in self.config.filters:
            return True

        if self.config.filter_operator.lower() == "and":
            for _filter in self.config.filters[self.resource_type]:
                if not _filter.is_match(resource):
                    return False
            # All resources successfully matched
            return True
        else:
            # Fallback to 'OR' logic. Filter in if any filter applies to resource
            for _filter in self.config.filters[self.resource_type]:
                if _filter.is_match(resource):
                    return True
            # Filter was specified for resource type but resource did not match any
            return False
