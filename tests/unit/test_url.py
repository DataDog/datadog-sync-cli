# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

from datadog_sync.utils.custom_client import UrlObject


@pytest.mark.parametrize(
    "url, path, params, expected",
    [
        (
            "https://api.datadoghq.com",
            "/api/v1/tags/hosts",
            {},
            "https://api.datadoghq.com/api/v1/tags/hosts",
        ),
        (
            "https://foo.bar.example.com",
            "/api/v1/tags/hosts",
            {},
            "https://foo.bar.example.com/api/v1/tags/hosts",
        ),
        (
            "https://foo.bar.example.com",
            "/api/v1/tags/hosts",
            {"subdomain": "intake"},
            "https://intake.example.com/api/v1/tags/hosts",
        ),
        (
            "https://foo.bar.example.com",
            "/api/v1/tags/hosts",
            {"domain": "datadoghq.com"},
            "https://foo.bar.datadoghq.com/api/v1/tags/hosts",
        ),
        (
            "https://api.datadoghq.com",
            "/api/v1/tags/hosts",
            {"domain": "example.com"},
            "https://api.example.com/api/v1/tags/hosts",
        ),
        (
            "https://api.datadoghq.com",
            "/api/v1/tags/hosts",
            {"subdomain": "intake"},
            "https://intake.datadoghq.com/api/v1/tags/hosts",
        ),
        (
            "https://api.datadoghq.com",
            "/api/v1/tags/hosts",
            {"subdomain": "intake", "domain": "example.com"},
            "https://intake.example.com/api/v1/tags/hosts",
        ),
    ],
)
def test_url_object(url, path, params, expected):
    c = UrlObject.from_str(url)

    assert c.build_url(path, **params) == expected
