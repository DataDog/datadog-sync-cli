# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for the case management VCR sanitizer (tests/conftest.py).

The case management list endpoints return every case type / custom attribute
config in the org. Cassettes recorded against them must not leak real org data
into this public repository. These tests pin the sanitizer helpers AND the
configured VCR pipeline end-to-end: a fake interaction is pushed through the
actual ``vcr_config`` hooks and the serialized cassette is searched for the
original UUIDs, unrelated rows, and the synthetic-token consistency across the
request path and response body.

All ids below are deliberately fake (fixed RFC-4122-shaped constants, never
real org ids).
"""

import json

from tests.conftest import (
    CASE_MANAGEMENT_ATTRIBUTE_KEY_MARKER,
    CASE_MANAGEMENT_TYPE_NAME_MARKER,
    filter_request_data,
    filter_response_data,
    sanitize_case_management_request,
    _cm_synthetic_token,
)

# Fake, obviously-constant UUIDs (nil-ish test constants; never real ids).
TYPE_UUID = "00000000-1111-2222-3333-444444444444"
OTHER_TYPE_UUID = "00000000-aaaa-bbbb-cccc-dddddddddddd"
ATTR_UUID = "00000000-eeee-ffff-0000-111111111111"


def _make_request(uri, body=None, method="GET"):
    from vcr.request import Request

    headers = {"Content-Type": ["application/json"]}
    return Request(method, uri, body if body is not None else "", headers)


def _make_response(body_dict, status=200):
    return {
        "status": {"code": status, "message": "OK"},
        "headers": {"Content-Type": ["application/vnd.api+json"]},
        "body": {"string": str.encode(json.dumps(body_dict))},
    }


def _types_list_response():
    return _make_response(
        {
            "data": [
                {
                    "id": TYPE_UUID,
                    "type": "case_type",
                    "attributes": {
                        "name": f"{CASE_MANAGEMENT_TYPE_NAME_MARKER}_src",
                        "description": "synthetic fixture",
                        "emoji": ":mag:",
                    },
                },
                {
                    "id": OTHER_TYPE_UUID,
                    "type": "case_type",
                    "attributes": {"name": "Real Org Type", "description": "internal"},
                },
            ]
        }
    )


def _attributes_list_response():
    return _make_response(
        {
            "data": [
                {
                    "id": ATTR_UUID,
                    "type": "custom_attribute",
                    "attributes": {
                        "case_type_id": TYPE_UUID,
                        "key": f"{CASE_MANAGEMENT_ATTRIBUTE_KEY_MARKER}_src",
                        "display_name": "Test Attribute",
                        "is_multi": False,
                        "type": "TEXT",
                    },
                },
                {
                    "id": OTHER_TYPE_UUID,
                    "type": "custom_attribute",
                    "attributes": {
                        "case_type_id": OTHER_TYPE_UUID,
                        "key": "real_org_key",
                        "display_name": "Real Attribute",
                        "is_multi": True,
                        "type": "NUMBER",
                    },
                },
            ]
        }
    )


# ---------------------------------------------------------------------------
# Helper-level tests
# ---------------------------------------------------------------------------


class TestRowFiltering:
    def test_types_list_rows_filtered_by_marker(self):
        resp = filter_response_data()(_types_list_response())
        data = json.loads(resp["body"]["string"])["data"]
        assert len(data) == 1
        assert CASE_MANAGEMENT_TYPE_NAME_MARKER in data[0]["attributes"]["name"]

    def test_attributes_list_rows_filtered_by_key_prefix(self):
        resp = filter_response_data()(_attributes_list_response())
        data = json.loads(resp["body"]["string"])["data"]
        assert len(data) == 1
        assert data[0]["attributes"]["key"].startswith(CASE_MANAGEMENT_ATTRIBUTE_KEY_MARKER)

    def test_non_case_management_response_unchanged(self):
        body = {"data": [{"id": TYPE_UUID, "type": "team", "attributes": {"name": "some team"}}]}
        before = _make_response(body)
        resp = filter_response_data()(before)
        assert json.loads(resp["body"]["string"]) == body

    def test_error_envelope_unchanged(self):
        body = {"errors": ["Bad Request"]}
        resp = filter_response_data()(_make_response(body, status=400))
        assert json.loads(resp["body"]["string"]) == body


class TestTokenRewrite:
    def test_token_deterministic_and_visible(self):
        t1 = _cm_synthetic_token("case-type", TYPE_UUID)
        t2 = _cm_synthetic_token("case-type", TYPE_UUID)
        assert t1 == t2
        assert t1.startswith("case-type-test-")

    def test_response_ids_tokenized(self):
        resp = filter_response_data()(_types_list_response())
        row = json.loads(resp["body"]["string"])["data"][0]
        assert row["id"] == _cm_synthetic_token("case-type", TYPE_UUID)

    def test_attribute_case_type_reference_tokenized_as_type(self):
        resp = filter_response_data()(_attributes_list_response())
        row = json.loads(resp["body"]["string"])["data"][0]
        assert row["id"] == _cm_synthetic_token("attr", ATTR_UUID)
        assert row["attributes"]["case_type_id"] == _cm_synthetic_token("case-type", TYPE_UUID)

    def test_request_uri_and_response_tokens_agree(self):
        request = _make_request(f"https://api.datadoghq.eu/api/v2/cases/types/{TYPE_UUID}")
        sanitized = filter_request_data()(request)
        expected = f"/api/v2/cases/types/{_cm_synthetic_token('case-type', TYPE_UUID)}"
        assert sanitized.uri.endswith(expected)

        resp = filter_response_data()(_types_list_response())
        row = json.loads(resp["body"]["string"])["data"][0]
        assert row["id"] in sanitized.uri

    def test_request_body_tokenized(self):
        body = json.dumps(
            {
                "data": {
                    "type": "custom_attribute",
                    "id": ATTR_UUID,
                    "attributes": {"case_type_id": TYPE_UUID, "key": f"{CASE_MANAGEMENT_ATTRIBUTE_KEY_MARKER}_x"},
                }
            }
        ).encode()
        request = _make_request(
            f"https://api.us5.datadoghq.com/api/v2/cases/types/{TYPE_UUID}/custom_attributes",
            body=body,
            method="POST",
        )
        sanitized = filter_request_data()(request)
        parsed = json.loads(sanitized.body)
        assert parsed["data"]["attributes"]["case_type_id"] == _cm_synthetic_token("case-type", TYPE_UUID)
        assert parsed["data"]["id"] == _cm_synthetic_token("attr", ATTR_UUID)

    def test_non_case_management_request_unchanged(self):
        uri = f"https://api.datadoghq.eu/api/v2/team/{TYPE_UUID}"
        request = _make_request(uri)
        sanitized = filter_request_data()(request)
        assert sanitized.uri == uri

    def test_already_tokenized_uri_unchanged(self):
        token = _cm_synthetic_token("case-type", TYPE_UUID)
        request = _make_request(f"https://api.datadoghq.eu/api/v2/cases/types/{token}")
        sanitized = filter_request_data()(request)
        assert sanitized.uri.endswith(f"/api/v2/cases/types/{token}")


# ---------------------------------------------------------------------------
# End-to-end pipeline test: the configured hooks + vcrpy's own serializer
# ---------------------------------------------------------------------------


def test_configured_vcr_pipeline_produces_clean_cassette():
    """A complete fake interaction is run through the ACTUAL hooks wired into
    vcr_config (filter_request_data / filter_response_data), serialized with
    vcrpy's yaml serializer, and the resulting cassette is searched for:

    - the original UUIDs (must be absent),
    - the unrelated (non-synthetic) row (must be absent),
    - consistent synthetic tokens in BOTH the request path and the response
      body (must be present).
    """
    import vcr.serialize as vcr_serialize
    import vcr.serializers.yamlserializer as vcr_yaml_serializer

    request = _make_request(f"https://api.datadoghq.eu/api/v2/cases/types/{TYPE_UUID}", method="PUT")
    response = _types_list_response()

    sanitized_request = sanitize_case_management_request(request)
    sanitized_response = filter_response_data()(response)

    # The exact serialization path vcrpy itself uses: serialize() zips
    # requests/responses, converts to unicode, and hands the dict to the
    # yaml serializer module.
    cassette = vcr_serialize.serialize(
        {"requests": [sanitized_request], "responses": [sanitized_response]},
        vcr_yaml_serializer,
    )

    type_token = _cm_synthetic_token("case-type", TYPE_UUID)

    assert TYPE_UUID not in cassette, "original type UUID must not appear anywhere in the cassette"
    assert OTHER_TYPE_UUID not in cassette, "unrelated row UUID must not appear anywhere in the cassette"
    assert "Real Org Type" not in cassette, "non-synthetic row content must be filtered out of the cassette"
    assert "real org" not in cassette.lower()
    assert type_token in cassette
    # Token consistency: the token appears both as the request path segment
    # and as the response row id.
    assert f"/api/v2/cases/types/{type_token}" in cassette
    assert f'"id": "{type_token}"' in cassette
