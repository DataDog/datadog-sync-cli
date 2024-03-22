# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from copy import deepcopy
from asyncio import Queue
from typing import TYPE_CHECKING, Dict, List, Set

from datadog_sync.constants import FALSE
from datadog_sync.utils.resource_utils import find_attr

if TYPE_CHECKING:
    from datadog_sync.utils.configuration import Configuration


class ResourcesManager:
    def __init__(self, config: Configuration) -> None:
        self.config: Configuration = config
        self.all_resources: Dict[str, str] = {}  # mapping of all resources to its resource_type
        self.all_cleanup_resources: Dict[str, str] = {}  # mapping of all resources to cleanup
        self.dependencies_graph: Dict[str, Set[str]] = {}  # dependency graph
        self.all_missing_resources: Dict[str, str] = {}  # mapping of all missing resources imported
        self.missing_resources_queue: Queue = Queue()  # queue for missing resources

        for resource_type in config.resources_arg:
            for _id, _ in config.resources[resource_type].resource_config.source_resources.items():
                self.all_resources[_id] = resource_type
                # individual resource dependency graph
                self.dependencies_graph[_id] = self._resource_connections(_id, resource_type)

            if self.config.cleanup != FALSE:
                # populate resources to cleanup
                source_resources = set(config.resources[resource_type].resource_config.source_resources.keys())
                destination_resources = set(
                    config.resources[resource_type].resource_config.destination_resources.keys()
                )

                for cleanup_id in destination_resources.difference(source_resources):
                    self.all_cleanup_resources[cleanup_id] = resource_type

    def _resource_connections(self, _id: str, resource_type: str) -> Set[str]:
        failed_connections: List[str] = []

        if not self.config.resources[resource_type].resource_config.resource_connections:
            return set(failed_connections)

        resource = deepcopy(self.config.resources[resource_type].resource_config.source_resources[_id])
        if self.config.resources[resource_type].resource_config.resource_connections:
            for resource_to_connect, v in self.config.resources[
                resource_type
            ].resource_config.resource_connections.items():
                for attr_connection in v:
                    failed = find_attr(
                        attr_connection,
                        resource_to_connect,
                        resource,
                        self.config.resources[resource_type].connect_id,
                    )
                    if failed:
                        # After retrieving all of the failed connections, we check if
                        # the resources are imported. Otherwise append to missing with its type.
                        for f_id in failed:
                            if f_id not in self.config.resources[resource_to_connect].resource_config.source_resources:
                                self.missing_resources_queue.put_nowait((f_id, resource_to_connect))
                                self.all_missing_resources[f_id] = resource_to_connect

                        failed_connections.extend(failed)
        return set(failed_connections)
