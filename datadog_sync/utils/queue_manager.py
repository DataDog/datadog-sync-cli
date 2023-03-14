from collections import deque
from graphlib import TopologicalSorter
from typing import List

from datadog_sync.utils.configuration import Configuration
from datadog_sync.utils.resource_utils import ResourceConnectionError, find_attr


class QueueManager:
    def __init__(self, config):
        self.config = config
        self.queue = deque()

        # mapping of all-resources to its type instance
        all_resources = {}
        for resource_type in config.resources_arg:
            for k, v in config.resources[resource_type].resource_config.source_resources.items():
                all_resources[k] = resource_type

        # unsorted dependencie graphs
        dependencies_graph = {}
        for k, v in all_resources.items():
            dependencies_graph[k] = resource_connections(self.config, k, v)

        # Initialize topological sorter
        print(dependencies_graph)
        sorter = TopologicalSorter(dependencies_graph)
        print(tuple(sorter.static_order()))

        self.sorter = sorter


def resource_connections(config: Configuration, _id: str, resource_type: str) -> List[str]:
    failed_connections = []

    if not config.resources[resource_type].resource_config.resource_connections:
        return resource_connections

    for resource_to_connect, v in config.resources[resource_type].resource_config.resource_connections.items():
        for attr_connection in v:
            failed = find_attr(
                attr_connection,
                resource_to_connect,
                config.resources[resource_type].resource_config.source_resources[_id],
                config.resources[resource_type].connect_id,
            )
            if failed:
                failed_connections.extend(failed)

    return resource_connections
