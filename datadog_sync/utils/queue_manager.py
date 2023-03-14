from collections import defaultdict
from graphlib import TopologicalSorter
from typing import List
from datadog_sync.utils.base_resource import BaseResource

from datadog_sync.utils.resource_utils import find_attr


class QueueManager:
    def __init__(self, config):
        self.config = config
        self.all_resources = {}
        self.missing_resources = {}

        dependencies_graph = {}
        for resource_type in config.resources_arg:
            for k, _ in config.resources[resource_type].resource_config.source_resources.items():
                self.all_resources[k] = resource_type
                dependencies_graph[k] = self._resource_connections(k, resource_type)

        # Initialize topological sorter
        self.sorter = self._init_topological_sorter(dependencies_graph)

    def _init_topological_sorter(self, dependencies):
        sorter = TopologicalSorter(dependencies)
        sorter.prepare()
        return sorter

    def _resource_connections(self, _id: str, resource_type: str) -> List[str]:
        failed_connections = []

        if not self.config.resources[resource_type].resource_config.resource_connections:
            return failed_connections

        for resource_to_connect, v in self.config.resources[resource_type].resource_config.resource_connections.items():
            for attr_connection in v:
                failed = find_attr(
                    attr_connection,
                    resource_to_connect,
                    self.config.resources[resource_type].resource_config.source_resources[_id],
                    self.config.resources[resource_type].connect_id,
                )
                if failed:
                    # After retrieving all of the failed connections, we check if
                    # the resources are imported. Otherwise append to missing with its type.
                    for f_id in failed:
                        if f_id not in self.config.resources[resource_to_connect].resource_config.source_resources:
                            self.missing_resources[f_id] = resource_to_connect

                    failed_connections.extend(failed)

        return failed_connections
