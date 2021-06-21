from click import pass_context, group, option

import datadog_sync.constants as constants
from datadog_sync.commands import ALL_COMMANDS
from datadog_sync.models import (
    Roles,
    Users,
    Monitors,
    Dashboards,
    DashboardLists,
    Downtimes,
    SyntheticsPrivateLocations,
    SyntheticsTests,
    SyntheticsGlobalVariables,
    ServiceLevelObjectives,
    LogsCustomPipelines,
    IntegrationsAWS,
)
from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.configuration import Configuration
from datadog_sync.utils.log import Log


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
    "--verbose",
    "-v",
    required=False,
    is_flag=True,
    help="Enable verbose logging.",
)
@pass_context
def cli(ctx, **kwargs):
    """Initialize cli"""
    ctx.ensure_object(dict)

    # configure logger
    logger = Log(kwargs.get("verbose"))

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

    source_client = CustomClient(source_api_url, source_auth, retry_timeout)
    destination_client = CustomClient(destination_api_url, destination_auth, retry_timeout)

    # Initialize Configuration
    config = Configuration(logger=logger, source_client=source_client, destination_client=destination_client)
    ctx.obj["config"] = config

    # Initialize resources
    config.resources = get_resources(config, kwargs.get("resources"))


def get_resources(cfg, resources_arg):
    """Returns list of Resources. Order of resources applied are based on the list returned"""
    resources = [
        Roles(cfg),
        Users(cfg),
        SyntheticsPrivateLocations(cfg),
        SyntheticsTests(cfg),
        SyntheticsGlobalVariables(cfg),
        Monitors(cfg),
        Downtimes(cfg),
        Dashboards(cfg),
        DashboardLists(cfg),
        ServiceLevelObjectives(cfg),
        LogsCustomPipelines(cfg),
        IntegrationsAWS(cfg),
    ]

    if resources_arg:
        new_resources = []
        resources_arg_list = resources_arg.split(",")
        for resource in resources:
            if resource.resource_type in resources_arg_list:
                new_resources.append(resource)
        return new_resources

    return resources


def get_import_order(resources):
    """Returns the order of importing resources to guarantee that all resource dependencies are met"""
    graph, nbr_dependencies = get_resources_dependency_graph(resources)
    dependency_order = []

    # See Kahn's algorithm: https://en.wikipedia.org/wiki/Topological_sorting#Kahn's_algorithm

    queue = []
    for resource, dependencies in graph.items():
        if resource not in nbr_dependencies:
            nbr_dependencies[resource] = 0
        # nbr_dependencies == 0 meaning it doesn't have any unresolved dependency
        if nbr_dependencies.get(resource) == 0:
            queue.append(resource)

    # queue contains all resources that don't have any dependency to resolve
    while queue:
        current_resource = queue.pop()

        dependency_order.append(current_resource)

        # if current_resource has dependencies
        if current_resource in graph:
            for depender in graph[current_resource]:
                if depender in nbr_dependencies:
                    # current_resource will be created, therefore the depender's number of dependencies is decremented by one
                    nbr_dependencies[depender] -= 1

                    # if all it's dependencies are resolved, we can create it next
                    if nbr_dependencies[depender] == 0:
                        queue.append(depender)

    pretty_dependency_order = ""
    for i, resource in enumerate(dependency_order):
        pretty_dependency_order += resource
        if i != len(dependency_order) - 1:
            pretty_dependency_order += " -> "

    return dependency_order, pretty_dependency_order


def get_resources_dependency_graph(resources):
    """Returns a Directed Acyclic Graph of the resources. An edge between A and B means that resource A might require resource B"""
    graph = {}
    nbr_dependencies = {}

    all_resources = get_resources(None, None)
    str_to_class = {r.resource_type: r for r in all_resources}

    queue = [resource for resource in resources]

    processed = {resource.resource_type: False for resource in all_resources}

    while queue:
        resource = queue.pop()

        # for safety, to avoid processing the same resource twice
        if processed[resource.resource_type]:
            continue

        processed[resource.resource_type] = True

        # initializing the dicts keys
        if resource.resource_type not in graph:
            graph[resource.resource_type] = []
        if resource.resource_type not in nbr_dependencies:
            nbr_dependencies[resource.resource_type] = 0

        if resource.resource_connections:
            for dependency in resource.resource_connections.keys():
                # some resources depend on similar type of resource e.g. composite monitors, this case should be ignored
                if dependency == resource.resource_type:
                    continue

                # add the dependency to the queue as it might need some dependencies aswell
                if not processed[dependency] and dependency not in [r.resource_type for r in queue]:
                    queue.append(str_to_class[dependency])

                # add an edge between resource and dependency in the form of [resource => [list of dependers]]
                if dependency not in graph:
                    graph[dependency] = [resource.resource_type]
                else:
                    graph[dependency].append(resource.resource_type)

                # update the number of dependencies of the resource
                if resource.resource_type not in nbr_dependencies:
                    nbr_dependencies[resource.resource_type] = 1
                else:
                    nbr_dependencies[resource.resource_type] += 1

    return graph, nbr_dependencies


# Register all click sub-commands
for command in ALL_COMMANDS:
    cli.add_command(command)
