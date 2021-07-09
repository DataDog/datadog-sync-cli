import pytest
import os
import logging
import json

from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.configuration import Configuration
from datadog_sync import models
from datadog_sync.utils.base_resource import BaseResource
from datadog_sync import constants


def filter_pl_secrets():
    def before_record_response(response):
        if "body" not in response or "string" not in response["body"]:
            return response

        resp = json.loads(response["body"]["string"])

        if "private_location" in resp:
            resp["private_location"].pop("secrets", None)
            if "config" in resp:
                resp.pop("config", None)

        response["body"]["string"] = str.encode((json.dumps(resp)))

        return response

    return before_record_response


@pytest.fixture(scope="module")
def vcr_config():
    return dict(
        filter_headers=["DD-API-KEY", "DD-APPLICATION-KEY"],
        filter_query_parameters=("api_key", "application_key"),
        match_on=["method", "scheme", "host", "port", "path", "query", "body"],
        decode_compressed_response=True,
        before_record_response=filter_pl_secrets(),
    )


@pytest.fixture(scope="module")
def config():
    source_api_url = os.getenv(constants.DD_SOURCE_API_URL)
    destination_api_url = os.getenv(constants.DD_DESTINATION_API_URL)
    max_workers = os.getenv(constants.MAX_WORKERS)

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
        max_workers=int(max_workers),
    )

    resources = {
        cls.resource_type: cls(cfg)
        for cls in models.__dict__.values()
        if isinstance(cls, type) and issubclass(cls, BaseResource)
    }

    cfg.resources = resources

    return cfg
