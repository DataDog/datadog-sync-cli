# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from datadog_sync.utils.state import State
import pytest
import os
import logging
import json
import re
import pathlib
from datetime import datetime
from json.decoder import JSONDecodeError

from datadog_sync.utils.configuration import Configuration
from datadog_sync.utils.configuration import init_resources
from datadog_sync.utils.custom_client import CustomClient

from freezegun import freeze_time


tracer = None
try:
    if os.getenv("DD_AGENT_HOST"):
        from ddtrace import config, patch

        config.httplib["distributed_tracing"] = True
        config.requests["distributed_tracing"] = True
        patch(httplib=True, requests=True)
except ImportError:
    pass

# disable vcr logging
for _ in ("vcr", "vcr.requests", "vcr.matcher"):
    logging.getLogger(_).setLevel(logging.CRITICAL)

PATTERN_DOUBLE_UNDERSCORE = re.compile(r"__+")
HEADERS_TO_PERSISTS = ("Accept-Encoding", "Content-Type")


@pytest.fixture()
def runner(freezed_time):
    from click.testing import CliRunner

    return CliRunner(mix_stderr=False)


def filter_private_location_data(response):
    if response["status"]["code"] == 204:
        return response

    if "body" not in response or "string" not in response["body"]:
        return response

    try:
        resp = json.loads(response["body"]["string"])
    except JSONDecodeError:
        return response

    if "private_location" in resp:
        resp["private_location"].pop("secrets", None)
        if "config" in resp:
            resp.pop("config", None)

    response["body"]["string"] = str.encode((json.dumps(resp)))
    return response


def filter_response_data():
    def before_record_response(response):
        _filter_response_headers(response)
        # add filter functions below
        response = filter_private_location_data(response)
        return response

    return before_record_response


def _filter_response_headers(response):
    for key in list(response["headers"].keys()):
        if key not in HEADERS_TO_PERSISTS:
            response["headers"].pop(key, None)


def _disable_recording():
    """Disable VCR.py integration."""
    return os.getenv("RECORD", "false").lower() == "none"


@pytest.fixture(scope="session")
def disable_recording(request):
    """Disable VCR.py integration."""
    return _disable_recording()


def get_record_mode():
    return {
        "false": "none",
        "true": "rewrite",
        "none": "new_episodes",
    }[os.getenv("RECORD", "false").lower()]


@pytest.fixture(scope="module")
def vcr_config():
    config = dict(
        record_mode=get_record_mode(),
        filter_headers=("DD-API-KEY", "DD-APPLICATION-KEY", "Connection", "Content-Length", "User-Agent"),
        filter_query_parameters=("api_key", "application_key"),
        match_on=["method", "scheme", "host", "port", "path", "query", "body"],
        decode_compressed_response=True,
        before_record_response=filter_response_data(),
    )

    if tracer:
        from urllib.parse import urlparse

        config["ignore_hosts"] = [urlparse(tracer._writer.agent_url).hostname]

    return config


@pytest.fixture(scope="module")
def config():
    custom_client = CustomClient(None, {"apiKeyAuth": "123", "appKeyAuth": "123"}, None, None, True)

    cfg = Configuration(
        logger=logging.getLogger(__name__),
        max_workers=100,
        source_client=custom_client,
        destination_client=custom_client,
        filters={},
        filter_operator="OR",
        force_missing_dependencies=False,
        skip_failed_resource_connections=True,
        cleanup=False,
        create_global_downtime=False,
        validate=False,
        state=State(),
        verify_ddr_status=True,
        send_metrics=True,
        backup_before_reset=True,
    )

    resources = init_resources(cfg)

    cfg.resources = resources
    cfg.resources_arg = list(resources.keys())

    return cfg


@pytest.fixture
def default_cassette_name(default_cassette_name):
    return PATTERN_DOUBLE_UNDERSCORE.sub("_", default_cassette_name)


@pytest.fixture
def freezed_time(vcr):
    if get_record_mode() in {"new_episodes", "rewrite"}:
        tzinfo = datetime.now().astimezone().tzinfo
        freeze_at = datetime.now().replace(tzinfo=tzinfo).isoformat()
        if get_record_mode() == "rewrite":
            pathlib.Path(vcr._path).parent.mkdir(parents=True, exist_ok=True)
            with pathlib.Path(vcr._path).with_suffix(".frozen").open("w+") as f:
                f.write(freeze_at)
    else:
        freeze_file = pathlib.Path(vcr._path).with_suffix(".frozen")
        if not freeze_file.exists():
            msg = (
                "Time file '{}' not found: create one setting `RECORD=true` or "
                "ignore it using `RECORD=none`".format(freeze_file)
            )
            raise RuntimeError(msg)
        with freeze_file.open("r") as f:
            freeze_at = f.readline().strip()

    with freeze_time(freeze_at, real_asyncio=True):
        yield
