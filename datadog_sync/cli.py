import logging
import os

from click import pass_context, group, option
from datadog_api_client.v1 import (
    ApiClient as ApiClientV1,
    Configuration as ConfigurationV1,
)
from datadog_api_client.v2 import (
    ApiClient as ApiClientV2,
    Configuration as ConfigurationV2,
)

import datadog_sync.constants as constants
from datadog_sync.commands import ALL_COMMANDS
from datadog_sync.models import (
    Dashboard,
    Monitor,
    Role,
    User,
    Downtime,
    LogsCustomPipeline,
    SyntheticsTest,
    SyntheticsPrivateLocation,
    IntegrationAws,
)

log = logging.getLogger("__name__")


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
    default="60",
    help="The HTTP request retry timeout period. Defaults to 60s",
)
@option(
    "--terraform-parallelism",
    required=False,
    help="Limit the number of concurrent operation as Terraform walks the graph.",
)
@option(
    "--terraformer-bin-path",
    default="terraformer",
    required=False,
    help="Terraformer binary path.",
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
        sh = logging.StreamHandler()
        fmt = logging.Formatter("%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s")
        sh.setFormatter(fmt)
        log.addHandler(sh)
        log.setLevel(logging.DEBUG)
    else:
        logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

    # Set root project dir
    ctx.obj["root_path"] = os.getcwd()

    # Initialize the datadog API Clients
    # Initialize Source client
    source_auth = {
        "apiKeyAuth": ctx.obj.get("source_api_key"),
        "appKeyAuth": ctx.obj.get("source_app_key"),
    }
    source_configuration_v1 = ConfigurationV1(
        host=ctx.obj.get("source_api_url"),
        api_key=source_auth,
    )
    source_configuration_v2 = ConfigurationV2(
        host=ctx.obj.get("source_api_url"),
        api_key=source_auth,
    )
    client_v1 = ApiClientV1(source_configuration_v1)
    client_v2 = ApiClientV2(source_configuration_v2)
    # Initialize Destination client
    destination_auth = {
        "apiKeyAuth": ctx.obj.get("destination_api_key"),
        "appKeyAuth": ctx.obj.get("destination_app_key"),
    }
    destination_configuration_v1 = ConfigurationV1(
        host=ctx.obj.get("destination_api_url"),
        api_key=destination_auth,
    )
    destination_configuration_v2 = ConfigurationV2(
        host=ctx.obj.get("destination_api_url"),
        api_key=destination_auth,
    )
    destination_client_v1 = ApiClientV1(destination_configuration_v1)
    destination_client_v2 = ApiClientV2(destination_configuration_v2)

    ctx.obj["source_client_v1"] = client_v1
    ctx.obj["source_client_v2"] = client_v2
    ctx.obj["destination_client_v1"] = destination_client_v1
    ctx.obj["destination_client_v2"] = destination_client_v2

    # Initialize resources
    ctx.obj["resources"] = get_resources(ctx)


def get_resources(ctx):
    """Returns list of Resources. Order of resources applied are based on the list returned"""
    resources = [
        Role(ctx),
        User(ctx),
        IntegrationAws(ctx),
        SyntheticsPrivateLocation(ctx),
        SyntheticsTest(ctx),
        Monitor(ctx),
        Downtime(ctx),
        Dashboard(ctx),
        LogsCustomPipeline(ctx),
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
