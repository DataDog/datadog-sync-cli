import logging

from click import pass_context, group, option

import datadog_sync.constants as constants
from datadog_sync.commands import ALL_COMMANDS
from datadog_sync.models import (
    AWSIntegrations,
    Roles,
    Users,
    Monitors,
    Dashboards,
    Downtimes,
    SyntheticsPrivateLocations,
    SyntheticsTests,
    SyntheticsGlobalVariables,
    ServiceLevelObjectives,
    LogsCustomPipelines,
)
from datadog_sync.utils.custom_client import CustomClient


log = logging.getLogger(__name__)


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
    ctx.obj = kwargs
    if ctx.obj.get("source_api_url") is None:
        ctx.obj["source_api_url"] = constants.DEFAULT_API_URL
    if ctx.obj.get("destination_api_url") is None:
        ctx.obj["destination_api_url"] = constants.DEFAULT_API_URL

    # Set logging level and format
    if ctx.obj.get("verbose"):
        logging.basicConfig(
            format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s",
            level=logging.DEBUG,
        )
    else:
        logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

    # Initialize the datadog API Clients
    source_auth = {
        "apiKeyAuth": ctx.obj.get("source_api_key"),
        "appKeyAuth": ctx.obj.get("source_app_key"),
    }
    destination_auth = {
        "apiKeyAuth": ctx.obj.get("destination_api_key"),
        "appKeyAuth": ctx.obj.get("destination_app_key"),
    }
    retry_timeout = ctx.obj.get("http_client_retry_timeout")

    source_client = CustomClient(ctx.obj["source_api_url"], source_auth, retry_timeout)
    destination_client = CustomClient(ctx.obj["destination_api_url"], destination_auth, retry_timeout)

    ctx.obj["source_client"] = source_client
    ctx.obj["destination_client"] = destination_client

    # Initialize resources
    ctx.obj["resources"] = get_resources(ctx)


def get_resources(ctx):
    """Returns list of Resources. Order of resources applied are based on the list returned"""
    resources = [
        AWSIntegrations(ctx),
        Roles(ctx),
        Users(ctx),
        SyntheticsPrivateLocations(ctx),
        SyntheticsTests(ctx),
        SyntheticsGlobalVariables(ctx),
        Monitors(ctx),
        Downtimes(ctx),
        Dashboards(ctx),
        ServiceLevelObjectives(ctx),
        LogsCustomPipelines(ctx),
    ]

    resources_arg = ctx.obj.get("resources")
    if resources_arg:
        new_resources = []
        resources_arg_list = resources_arg.split(",")
        for resource in resources:
            if resource.resource_type in resources_arg_list:
                new_resources.append(resource)
        return new_resources

    return resources


# Register all click sub-commands
for command in ALL_COMMANDS:
    cli.add_command(command)
