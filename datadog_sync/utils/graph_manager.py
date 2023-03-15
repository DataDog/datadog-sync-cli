# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from collections import defaultdict, deque
from graphlib import TopologicalSorter
from typing import List, Set
from copy import deepcopy

from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.utils.resource_utils import find_attr


class GraphManager:
    def __init__(self, config):
        self.config = config
        self.all_resources = {}  # mapping of all resources to its resource_type
        self.missing_resources = deque()  # queue for missing resources
        self.dependencies_graph = {}  # dependency graph
        self.all_cleanup_resource = {}

        # Build initial graphs
        for resource_type in config.resources_arg:
            # individual resource dependency graph
            for _id, _ in config.resources[resource_type].resource_config.source_resources.items():
                self.all_resources[_id] = resource_type
                self.dependencies_graph[_id] = self._resource_connections(_id, resource_type)

            if self.config.cleanup.lower != "false":
                # populate resources to cleanup
                source_resources = set(config.resources[resource_type].resource_config.source_resources.keys())
                destination_resources = set(config.resources[resource_type].resource_config.destination_resources.keys())
                
                for cleanup_id in destination_resources.difference(source_resources):
                    self.all_cleanup_resource[cleanup_id] = resource_type

    def _resource_connections(self, _id: str, resource_type: str) -> Set[str]:
        failed_connections = []

        if not self.config.resources[resource_type].resource_config.resource_connections:
            return set(failed_connections)

        resource = deepcopy(self.config.resources[resource_type].resource_config.source_resources[_id])
        for resource_to_connect, v in self.config.resources[resource_type].resource_config.resource_connections.items():
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
                            self.missing_resources.append((f_id, resource_to_connect))

                    failed_connections.extend(failed)

        return set(failed_connections)


def init_topological_sorter(graph):
    sorter = TopologicalSorter(graph)
    sorter.prepare()
    return sorter
