# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for the paginated_request truncation warning.

Motivation: when the source API returns a non-5xx error (e.g. 403 Forbidden)
mid-pagination, paginated_request silently break()s and returns the partial
list. Downstream syncs cannot distinguish "source has N resources" from
"source has N+K resources but K couldn't be read". The error log must
therefore say TRUNCATED explicitly so operators correlating a cascade of
missing-dependency failures downstream can find the truncation event.
"""

import asyncio
import logging

from datadog_sync.utils.custom_client import CustomClient, PaginationConfig
from datadog_sync.utils.resource_utils import CustomClientHTTPError


class _FakeResp:
    """Minimal aiohttp.ClientResponseError-shaped object for
    CustomClientHTTPError.__init__ (which reads response.status +
    response.message)."""
    def __init__(self, status):
        self.status = status
        self.message = "Forbidden"


def _make_client():
    return CustomClient(
        host="https://api.datadoghq.com",
        auth={"apiKeyAuth": "k", "appKeyAuth": "a"},
        retry_timeout=60,
        timeout=30,
        send_metrics=False,
    )


def test_paginated_request_truncation_error_says_truncated(caplog):
    """A 403 mid-pagination must log with 'TRUNCATED' explicitly."""
    caplog.set_level(logging.ERROR, logger="datadog_sync_cli")

    async def scenario():
        client = _make_client()

        # First page: returns 2 rows successfully.
        # Second page: raises 403.
        page = 0

        async def flaky_get(path, **kwargs):
            nonlocal page
            page += 1
            if page == 1:
                # Return two rows so remaining_func is triggered.
                return {"data": [{"id": "a"}, {"id": "b"}], "meta": {"page": {"total_count": 10}}}
            raise CustomClientHTTPError(_FakeResp(403), message="Forbidden")

        cfg = PaginationConfig(page_size=2)
        wrapped = client.paginated_request(flaky_get)
        resources = await wrapped("/api/v2/roles", pagination_config=cfg)
        # Truncation returns the partial page collected before the 403.
        assert len(resources) == 2

    asyncio.run(scenario())

    err_messages = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    joined = " | ".join(err_messages)
    assert "TRUNCATED" in joined, f"Expected 'TRUNCATED' in error log, got: {joined!r}"
    assert "/api/v2/roles" in joined
    # The message should also flag downstream cascade so an operator scanning
    # missing-dependency errors can search for this event by keyword.
    assert "cascade" in joined.lower() or "missing" in joined.lower()


def test_paginated_request_500_still_isolates_not_truncates(caplog):
    """5xx errors trigger the isolation-by-halving path, NOT the truncation
    break. Regression guard: the truncation log change must not fire for
    5xx isolation errors, which are recoverable."""
    caplog.set_level(logging.WARNING, logger="datadog_sync_cli")

    async def scenario():
        client = _make_client()
        calls = {"n": 0}

        async def flaky_get(path, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise CustomClientHTTPError(_FakeResp(500), message="server error")
            # Second call at reduced page_size succeeds with empty page.
            return {"data": [], "meta": {"page": {"total_count": 0}}}

        cfg = PaginationConfig(page_size=2)
        wrapped = client.paginated_request(flaky_get)
        # Should not raise; isolation kicks in.
        await wrapped("/api/v2/logs/config/pipelines", pagination_config=cfg)

    asyncio.run(scenario())

    joined = " | ".join(r.getMessage() for r in caplog.records)
    # The isolation warning should fire, but NOT the truncation ERROR.
    assert "TRUNCATED" not in joined
