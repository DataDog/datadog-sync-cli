# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

tracer = None
try:
    from ddtrace import config, patch

    config.httplib["distributed_tracing"] = True
    patch(httplib=True)
except ImportError:
    pass

from json.decoder import JSONDecodeError
import pytest
import os
import logging
import json

from datadog_sync.utils.configuration import Configuration
from datadog_sync import constants
from datadog_sync.utils.configuration import get_resources

@pytest.fixture()
def runner():
    from click.testing import CliRunner

    return CliRunner(mix_stderr=False)


def filter_private_location_data(response):
    if "body" not in response or "string" not in response["body"]:
        return response

    try:
        resp = json.loads(response["body"]["string"])
    except JSONDecodeError:
        return

    if "private_location" in resp:
        resp["private_location"].pop("secrets", None)
        if "config" in resp:
            resp.pop("config", None)

    response["body"]["string"] = str.encode((json.dumps(resp)))
    return response


def filter_response_data():
    def before_record_response(response):
        # add filter functions below
        response = filter_private_location_data(response)
        return response

    return before_record_response


def _disable_recording():
    """Disable VCR.py integration."""
    return os.getenv("RECORD", "false").lower() == "none"


@pytest.fixture(scope="session")
def disable_recording(request):
    """Disable VCR.py integration."""
    return _disable_recording()


def get_record_mode():
    return {"false": "none", "true": "rewrite", "none": "new_episodes",}[os.getenv("RECORD", "false").lower()]


@pytest.fixture(scope="module")
def vcr_config():
    return dict(
        record_mode=get_record_mode(),
        filter_headers=["DD-API-KEY", "DD-APPLICATION-KEY"],
        filter_query_parameters=("api_key", "application_key"),
        match_on=["method", "scheme", "host", "port", "path", "query", "body"],
        decode_compressed_response=True,
        before_record_response=filter_response_data(),
    )


@pytest.fixture(scope="module")
def config():
    max_workers = os.getenv(constants.MAX_WORKERS)

    cfg = Configuration(
        logger=logging.getLogger(__name__),
        max_workers=int(max_workers),
    )

    resources, _ = get_resources(cfg, "")

    cfg.resources = resources

    return cfg
