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


@group()
@option(
    "--source-api-key", default=os.getenv(constants.DD_SOURCE_API_KEY), required=True
)
@option(
    "--source-app-key", default=os.getenv(constants.DD_SOURCE_APP_KEY), required=True
)
@option(
    "--source-api-url", default=os.getenv(constants.DD_SOURCE_API_URL), required=False
)
@option(
    "--destination-api-key",
    default=os.getenv(constants.DD_DESTINATION_API_KEY),
    required=True,
)
@option(
    "--destination-app-key",
    default=os.getenv(constants.DD_DESTINATION_APP_KEY),
    required=True,
)
@option(
    "--destination-api-url",
    default=os.getenv(constants.DD_DESTINATION_API_URL),
    required=False,
)
@option(
    "--terraformer-bin-path",
    default="terraformer",
    required=False,
)
@pass_context
def cli(ctx, **kwargs):
    """Initialize cli"""
    ctx.obj = kwargs
    if ctx.obj.get("source_api_url") is None:
        ctx.obj["source_api_url"] = constants.DEFAULT_API_URL
    if ctx.obj.get("destination_api_url") is None:
        ctx.obj["destination_api_url"] = constants.DEFAULT_API_URL

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


# Register all click sub-commands
for command in ALL_COMMANDS:
    cli.add_command(command)
