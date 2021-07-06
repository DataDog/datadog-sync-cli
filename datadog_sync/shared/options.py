from click import option

from datadog_sync import constants

_auth_options = [
    option(
        "--source-api-key",
        envvar=constants.DD_SOURCE_API_KEY,
        required=True,
        help="Datadog source organization API key.",
    ),
    option(
        "--source-app-key",
        envvar=constants.DD_SOURCE_APP_KEY,
        required=True,
        help="Datadog source organization APP key.",
    ),
    option(
        "--source-api-url",
        envvar=constants.DD_SOURCE_API_URL,
        required=False,
        help="Datadog source organization API url.",
    ),
    option(
        "--destination-api-key",
        envvar=constants.DD_DESTINATION_API_KEY,
        required=True,
        help="Datadog destination organization API key.",
    ),
    option(
        "--destination-app-key",
        envvar=constants.DD_DESTINATION_APP_KEY,
        required=True,
        help="Datadog destination organization APP key.",
    ),
    option(
        "--destination-api-url",
        envvar=constants.DD_DESTINATION_API_URL,
        required=False,
        help="Datadog destination organization API url.",
    ),
]


_common_options = [
    option(
        "--http-client-retry-timeout",
        envvar=constants.DD_HTTP_CLIENT_RETRY_TIMEOUT,
        required=False,
        type=int,
        default=60,
        help="The HTTP request retry timeout period. Defaults to 60s",
    ),
    option(
        "--resources",
        required=False,
        help="Optional comma separated list of resource to import. All supported resources are imported by default.",
    ),
    option(
        "--verbose",
        "-v",
        required=False,
        is_flag=True,
        help="Enable verbose logging.",
    ),
]


def auth_options(func):
    for _option in _auth_options:
        func = _option(func)
    return func


def common_options(func):
    for _option in _common_options:
        func = _option(func)
    return func
