# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from datadog_sync.utils.state import State
import base64
import hashlib
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

# ---------------------------------------------------------------------------
# Case management VCR sanitizer
#
# The case management list endpoints (``/api/v2/cases/types`` and
# ``/api/v2/cases/types/custom_attributes``) return EVERY case type /
# custom attribute config in the org. A cassette recorded against them would
# embed real org data (names, descriptions, keys, UUIDs) into this public
# repository. The sanitizer:
#
#   1. filters case management list-response rows down to the synthetic
#      fixtures only (``attributes.name`` containing "case_type_test",
#      ``attributes.key`` starting with "attr_key_test"), and
#   2. rewrites every case management UUID in request URIs, request bodies
#      and response bodies to visibly synthetic, deterministic tokens
#      ("case-type-test-<hash>" / "attr-test-<hash>").
#
# Detection: responses are identified by JSON:API schema markers
# ("type": "case_type" / "custom_attribute") — the same body-shape technique
# as filter_private_location_data — because the response hook cannot see the
# request URI. Requests are identified by the endpoint path. The two hooks
# share no mutable state (async requests interleave). Token rewriting is
# idempotent: already-synthetic tokens do not match the UUID shape and are
# never rewritten twice, so re-running the hooks on a sanitized cassette is
# stable (replay safety).
# ---------------------------------------------------------------------------

CASE_MANAGEMENT_TYPES_PATH_PREFIX = "/api/v2/cases/types"
CASE_MANAGEMENT_TYPE_NAME_MARKER = "case_type_test"
CASE_MANAGEMENT_ATTRIBUTE_KEY_MARKER = "attr_key_test"
_CM_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _cm_synthetic_token(kind, real_id):
    """Deterministic, visibly synthetic replacement for a case management UUID.

    ``kind`` is "case-type" or "attr". The token is a pure function of the
    original id, so request-side and response-side rewriting always agree.
    """
    digest = hashlib.sha256(real_id.encode("utf-8")).hexdigest()[:8]
    return f"{kind}-test-{digest}"


def _cm_tokenize_case_management_request_uri(uri):
    """Rewrite UUIDs in a case management request URI path to synthetic tokens.

    Path positions decide the token kind:
      /api/v2/cases/types/{type_id}                          -> case-type token
      /api/v2/cases/types/{type_id}/custom_attributes       -> case-type token
      /api/v2/cases/types/{type_id}/custom_attributes/{attr_id} -> both
    """
    from urllib.parse import urlsplit, urlunsplit

    split = urlsplit(uri)
    path = split.path
    marker = CASE_MANAGEMENT_TYPES_PATH_PREFIX
    idx = path.find(marker)
    if idx == -1:
        return uri
    tail = path[idx + len(marker) :].strip("/")
    segments = [s for s in tail.split("/") if s] if tail else []
    if not segments or segments[0] == "custom_attributes":
        # The org-wide list or the bare collection: no ids in the path.
        return uri
    type_token = _CM_UUID_RE.sub(lambda m: _cm_synthetic_token("case-type", m.group(0)), segments[0])
    new_segments = [type_token]
    if len(segments) > 2 and segments[1] == "custom_attributes":
        new_segments.append("custom_attributes")
        new_segments.append(_CM_UUID_RE.sub(lambda m: _cm_synthetic_token("attr", m.group(0)), segments[2]))
    elif len(segments) > 1:
        # Unknown extra segments: tokenize any UUIDs defensively.
        for seg in segments[1:]:
            new_segments.append(_CM_UUID_RE.sub(lambda m: _cm_synthetic_token("case-type", m.group(0)), seg))
    new_path = path[: idx + len(marker)] + "/" + "/".join(new_segments)
    return urlunsplit((split.scheme, split.netloc, new_path, split.query, split.fragment))


def _cm_is_uuid(value):
    return isinstance(value, str) and _CM_UUID_RE.fullmatch(value) is not None


def _cm_rewrite_ids(obj, kind_for_id):
    """Key-aware UUID rewrite over a parsed JSON structure.

    ``kind_for_id`` maps a row's JSON:API ``type`` marker to the token kind
    used for that row's ``id`` field ("case_type" -> "case-type",
    "custom_attribute" -> "attr"). ``case_type_id`` references always map to
    the case-type kind.
    """
    if isinstance(obj, dict):
        row_kind = kind_for_id.get(obj.get("type"))
        for key, value in list(obj.items()):
            if key == "case_type_id" and _cm_is_uuid(value):
                obj[key] = _cm_synthetic_token("case-type", value)
            elif key == "id" and row_kind and _cm_is_uuid(value):
                obj[key] = _cm_synthetic_token(row_kind, value)
            else:
                _cm_rewrite_ids(value, kind_for_id)
    elif isinstance(obj, list):
        for item in obj:
            _cm_rewrite_ids(item, kind_for_id)
    return obj


_CM_ROW_KIND_BY_TYPE = {"case_type": "case-type", "custom_attribute": "attr"}


def _cm_row_is_synthetic(row):
    attributes = row.get("attributes") or {}
    if row.get("type") == "case_type":
        return CASE_MANAGEMENT_TYPE_NAME_MARKER in (attributes.get("name") or "")
    if row.get("type") == "custom_attribute":
        return str(attributes.get("key") or "").startswith(CASE_MANAGEMENT_ATTRIBUTE_KEY_MARKER)
    return False


def _cm_contains_case_management_markers(parsed):
    """True when the parsed body carries case management JSON:API markers."""
    if not isinstance(parsed, dict):
        return False
    data = parsed.get("data")
    rows = data if isinstance(data, list) else [data]
    return any(isinstance(r, dict) and r.get("type") in _CM_ROW_KIND_BY_TYPE for r in rows if r is not None)


def sanitize_case_management_request(request):
    """before_record_request hook: tokenize case management URIs and bodies."""
    from urllib.parse import urlsplit

    if CASE_MANAGEMENT_TYPES_PATH_PREFIX not in urlsplit(request.uri).path:
        return request

    request.uri = _cm_tokenize_case_management_request_uri(request.uri)

    body = getattr(request, "body", None)
    if body:
        text = body.decode("utf-8") if isinstance(body, bytes) else body
        try:
            parsed = json.loads(text)
        except (JSONDecodeError, TypeError, ValueError):
            return request
        _cm_rewrite_ids(parsed, _CM_ROW_KIND_BY_TYPE)
        new_text = json.dumps(parsed)
        request.body = new_text.encode("utf-8") if isinstance(body, bytes) else new_text
    return request


def sanitize_case_management_response_data(response):
    """before_record_response hook body: filter rows + tokenize UUIDs.

    Only bodies carrying case management JSON:API markers are touched; every
    other response passes through unchanged (including error envelopes, which
    never carry those markers).
    """
    if response["status"]["code"] == 204:
        return response
    if "body" not in response or "string" not in response["body"]:
        return response

    try:
        parsed = json.loads(response["body"]["string"])
    except (JSONDecodeError, TypeError, ValueError):
        return response

    if not _cm_contains_case_management_markers(parsed):
        return response

    data = parsed.get("data")
    if isinstance(data, list):
        parsed["data"] = [row for row in data if isinstance(row, dict) and _cm_row_is_synthetic(row)]
    _cm_rewrite_ids(parsed, _CM_ROW_KIND_BY_TYPE)

    response["body"]["string"] = str.encode(json.dumps(parsed))
    return response


@pytest.fixture()
def runner(freezed_time):
    from click.testing import CliRunner

    # Suppress the progress bar in tests by default — it pollutes captured output
    # and tqdm's terminal escape sequences confuse pytest's output handling.
    # Tests that need to verify progress-bar behavior can override DD_SHOW_PROGRESS_BAR
    # in their own runner.invoke env= argument.
    return CliRunner(mix_stderr=False, env={"DD_SHOW_PROGRESS_BAR": "false"})


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
        response = _deal_with_binary(response)
        response = filter_private_location_data(response)
        response = sanitize_case_management_response_data(response)
        return response

    return before_record_response


def filter_request_data():
    def before_record_request(request):
        request = sanitize_case_management_request(request)
        return request

    return before_record_request


def _filter_response_headers(response):
    for key in list(response["headers"].keys()):
        if key not in HEADERS_TO_PERSISTS:
            response["headers"].pop(key, None)


def _is_binary(data):
    # A simple heuristic to check for binary data
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _deal_with_binary(response):
    if "body" in response:
        body = response["body"].get("string")
        if body and _is_binary(body):
            response["body"]["string"] = base64.b64encode(body).decode("ascii")
            response["body"]["encoding"] = "base64"
    return response


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
        before_record_request=filter_request_data(),
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
        show_progress_bar=True,
        allow_self_lockout=False,
        command="sync",
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
