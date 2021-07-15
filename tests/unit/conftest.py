import os
import logging

import pytest

from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.configuration import Configuration
from datadog_sync import constants
from datadog_sync.cli import get_resources


@pytest.fixture(scope="module")
def config():
    source_api_url = os.getenv(constants.DD_SOURCE_API_URL)
    destination_api_url = os.getenv(constants.DD_DESTINATION_API_URL)

    # Initialize the datadog API Clients
    source_auth = {
        "apiKeyAuth": os.getenv(constants.DD_SOURCE_API_KEY),
        "appKeyAuth": os.getenv(constants.DD_SOURCE_APP_KEY),
    }
    destination_auth = {
        "apiKeyAuth": os.getenv(constants.DD_DESTINATION_API_KEY),
        "appKeyAuth": os.getenv(constants.DD_DESTINATION_APP_KEY),
    }

    retry_timeout = os.getenv(constants.DD_HTTP_CLIENT_RETRY_TIMEOUT, 60)

    source_client = CustomClient(source_api_url, source_auth, retry_timeout)
    destination_client = CustomClient(destination_api_url, destination_auth, retry_timeout)

    cfg = Configuration(
        logger=logging.getLogger(__name__),
        source_client=source_client,
        destination_client=destination_client,
    )

    resources, _ = get_resources(cfg, "")

    cfg.resources = resources

    return cfg
