import logging
from collections import defaultdict, OrderedDict

from datadog_sync import models
from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.utils.log import Log
from datadog_sync.utils.filter import process_filters
from datadog_sync.constants import LOGGER_NAME


class Configuration(object):
    def __init__(
        self,
        logger=None,
        source_client=None,
        destination_client=None,
        resources=None,
        missing_deps=None,
        filters=None,
        force_missing_dependencies=None,
        skip_failed_resource_connections=None,
        max_workers=None,
    ):
        if not logger:
            # fallback to default logger if not provided
            logger = logging.getLogger(LOGGER_NAME)
        self.logger = logger
        self.source_client = source_client
        self.destination_client = destination_client
        self.resources = resources
        self.missing_deps = missing_deps
        self.filters = filters
        self.force_missing_dependencies = force_missing_dependencies
        self.skip_failed_resource_connections = skip_failed_resource_connections
        self.max_workers = max_workers


def build_config(**kwargs):
    # configure logger
    logger = Log(kwargs.get("verbose"))

    # configure Filter
    filters = process_filters(kwargs.get("filter"))

    source_api_url = kwargs.get("source_api_url")
    destination_api_url = kwargs.get("destination_api_url")

    force_missing_dependencies = kwargs.get("force_missing_dependencies")
    skip_failed_resource_connections = kwargs.get("skip_failed_resource_connections")

    # Initialize the datadog API Clients
    source_auth = {
        "apiKeyAuth": kwargs.get("source_api_key"),
        "appKeyAuth": kwargs.get("source_app_key"),
    }
    destination_auth = {
        "apiKeyAuth": kwargs.get("destination_api_key"),
        "appKeyAuth": kwargs.get("destination_app_key"),
    }
    retry_timeout = kwargs.get("http_client_retry_timeout")

    source_client = CustomClient(source_api_url, source_auth, retry_timeout)
    destination_client = CustomClient(destination_api_url, destination_auth, retry_timeout)

    max_workers = kwargs.get("max_workers")

    # Initialize Configuration
    config = Configuration(
        logger=logger,
        source_client=source_client,
        destination_client=destination_client,
        filters=filters,
        force_missing_dependencies=force_missing_dependencies,
        skip_failed_resource_connections=skip_failed_resource_connections,
        max_workers=max_workers,
    )

    # Initialize resources
    config.resources, config.missing_deps = get_resources(config, kwargs.get("resources"))

    return config


# TODO: add unit tests
def get_resources(cfg, resources_arg):
    """Returns list of Resources. Order of resources applied are based on the list returned"""

    all_resources = [
        cls.resource_type for cls in models.__dict__.values() if isinstance(cls, type) and issubclass(cls, BaseResource)
    ]

    if resources_arg:
        resources_arg = resources_arg.split(",")
    else:
        resources_arg = all_resources

    str_to_class = dict(
        (cls.resource_type, cls)
        for cls in models.__dict__.values()
        if isinstance(cls, type) and issubclass(cls, BaseResource)
    )

    resources_classes = [
        str_to_class[resource_type] for resource_type in resources_arg if resource_type in str_to_class
    ]

    order_list = get_import_order(resources_classes, str_to_class)

    missing_deps = [resource for resource in order_list if resource not in resources_arg]

    resources = OrderedDict({resource_type: str_to_class[resource_type](cfg) for resource_type in order_list})

    return resources, missing_deps


def get_import_order(resources, str_to_class):
    """Returns the order of importing resources to guarantee that all resource dependencies are met"""
    graph, dependencies_count = get_resources_dependency_graph(resources, str_to_class)
    dependency_order = []

    # See Kahn's algorithm: https://en.wikipedia.org/wiki/Topological_sorting#Kahn's_algorithm

    queue = []
    for resource in graph:
        # dependencies_count == 0 meaning it doesn't have any unresolved dependency
        if dependencies_count[resource] == 0:
            queue.append(resource)

    # queue contains all resources that don't have any dependency to resolve
    while queue:
        current_resource = queue.pop()
        dependency_order.append(current_resource)

        # if current_resource has dependencies
        if current_resource in graph:
            for depender in graph[current_resource]:
                # current_resource will be created, therefore the depender's number of dependencies is decremented by one
                dependencies_count[depender] = max(0, dependencies_count[depender] - 1)

                # if all it's dependencies are resolved, we can create it next
                if dependencies_count[depender] == 0:
                    queue.append(depender)

    return dependency_order


def get_resources_dependency_graph(resources, str_to_class):
    """Returns a Directed Acyclic Graph of the resources. An edge between A and B means that resource A might require resource B"""
    graph = defaultdict(list)
    dependencies_count = defaultdict(int)

    # resources that don't have resource_connections need to be initialized manually or their key will never be created
    for r in resources:
        graph[r.resource_type] = []

    queue = [resource for resource in resources]
    # Breadth-First Search over the resources and dependencies
    while queue:
        resource = queue.pop()

        if resource.resource_connections:
            for dependency in resource.resource_connections:
                # some resources depend on similar type of resource e.g. composite monitors, this case should be ignored
                if dependency == resource.resource_type:
                    continue

                # add the dependency to the queue as it might need some dependencies aswell
                if dependency not in [r.resource_type for r in queue]:
                    queue.append(str_to_class[dependency])

                # add an edge between resource and dependency in the form of [resource => [list of dependers]]
                graph[dependency].append(resource.resource_type)

                # update dependencies_count
                dependencies_count[resource.resource_type] += 1

    # make the graph read-only
    graph.default_factory = None

    return graph, dependencies_count
