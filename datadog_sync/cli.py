from click import pass_context, group, option
import click_config_file

from datadog_sync import constants
from datadog_sync import models
from datadog_sync.commands import ALL_COMMANDS
from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.configuration import Configuration
from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.utils.log import Log
from datadog_sync.utils.filter import process_filters
from collections import defaultdict, OrderedDict


@group()
@option(
    "--source-api-key",
    envvar=constants.DD_SOURCE_API_KEY,
    required=True,
    help="Datadog source organization API key.",
)
@option(
    "--source-app-key",
    envvar=constants.DD_SOURCE_APP_KEY,
    required=True,
    help="Datadog source organization APP key.",
)
@option(
    "--source-api-url",
    envvar=constants.DD_SOURCE_API_URL,
    required=False,
    help="Datadog source organization API url.",
)
@option(
    "--destination-api-key",
    envvar=constants.DD_DESTINATION_API_KEY,
    required=True,
    help="Datadog destination organization API key.",
)
@option(
    "--destination-app-key",
    envvar=constants.DD_DESTINATION_APP_KEY,
    required=True,
    help="Datadog destination organization APP key.",
)
@option(
    "--destination-api-url",
    envvar=constants.DD_DESTINATION_API_URL,
    required=False,
    help="Datadog destination organization API url.",
)
@option(
    "--http-client-retry-timeout",
    envvar=constants.DD_HTTP_CLIENT_RETRY_TIMEOUT,
    required=False,
    type=int,
    default=60,
    help="The HTTP request retry timeout period. Defaults to 60s",
)
@option(
    "--resources",
    required=False,
    help="Optional comma separated list of resource to import. All supported resources are imported by default.",
)
@option(
    "--max-workers",
    envvar=constants.MAX_WORKERS,
    required=False,
    type=int,
    help="Max number of workers when running operations in multi-threads. Defaults to 'None'",
)
@option(
    "--verbose",
    "-v",
    required=False,
    is_flag=True,
    help="Enable verbose logging.",
)
@option(
    "--force-missing-dependencies",
    required=False,
    is_flag=True,
    default=False,
    help="Force importing and syncing resources that could be potential dependencies to the requested resources.",
)
@option("--filter", required=False, help="Filter imported resources.", multiple=True)
@option(
    "--skip-failed-resource-connections",
    type=bool,
    default=True,
    show_default=True,
    help="Skip resource if resource connection fails.",
)
@click_config_file.configuration_option()
@pass_context
def cli(ctx, **kwargs):
    """Initialize cli"""
    ctx.ensure_object(dict)

    # configure logger
    logger = Log(kwargs.get("verbose"))

    # configure Filter
    filters = process_filters(kwargs.get("filter"))

    source_api_url = kwargs.get("source_api_url")
    destination_api_url = kwargs.get("destination_api_url")

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
    max_workers = kwargs.get("max_workers")

    source_client = CustomClient(source_api_url, source_auth, retry_timeout)
    destination_client = CustomClient(destination_api_url, destination_auth, retry_timeout)

    skip_failed_resource_connections = kwargs.get("skip_failed_resource_connections")

    # Initialize Configuration
    config = Configuration(
        logger=logger,
        source_client=source_client,
        destination_client=destination_client,
        filters=filters,
        skip_failed_resource_connections=skip_failed_resource_connections,
        max_workers=max_workers,
    )
    ctx.obj["config"] = config

    # Initialize resources and missing dependencies
    config.resources, config.missing_deps = get_resources(config, kwargs.get("resources"))

    ctx.obj["force_missing_dependencies"] = kwargs.get("force_missing_dependencies")


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


# Register all click sub-commands
for command in ALL_COMMANDS:
    cli.add_command(command)
