import pytest
import os
import logging
from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.configuration import Configuration
from datadog_sync import models
from datadog_sync.utils.base_resource import BaseResource


@pytest.fixture(scope="module")
def vcr_config():
    return dict(
        filter_headers=["DD-API-KEY", "DD-APPLICATION-KEY"],
        record_mode="once",
        match_on=["method", "scheme", "host", "port", "path", "query", "body"],
        decode_compressed_response=True,
    )


@pytest.fixture(scope="module")
def config():
    source_api_url = os.getenv("DD_SOURCE_API_URL")
    destination_api_url = os.getenv("DD_DESTINATION_API_URL")

    # Initialize the datadog API Clients
    source_auth = {
        "apiKeyAuth": os.getenv("DD_SOURCE_API_KEY"),
        "appKeyAuth": os.getenv("DD_SOURCE_APP_KEY"),
    }
    destination_auth = {
        "apiKeyAuth": os.getenv("DD_DESTINATION_API_KEY"),
        "appKeyAuth": os.getenv("DD_DESTINATION_APP_KEY"),
    }

    retry_timeout = 60

    source_client = CustomClient(source_api_url, source_auth, retry_timeout)
    destination_client = CustomClient(destination_api_url, destination_auth, retry_timeout)

    cfg = Configuration(logger=logging.getLogger(__name__), source_client=source_client, destination_client=destination_client)

    resources = [
        cls(cfg) for cls in models.__dict__.values() if isinstance(cls, type) and issubclass(cls, BaseResource)
    ]

    cfg.resources = resources

    return cfg

