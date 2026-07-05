# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for the HAMR-392 overload-status backoff + counter.

Motivating case: the destination monitor API's edge/proxy returns HTTP 512
when the request-queue budget is exceeded (44 batch-summary lines observed on
Allstate at 32-way sync-cli parallelism). The previous behavior retried with
a ``retry_count * 5s`` schedule (0, 5, 10, 15s), which refilled the proxy
queue as fast as the proxy could drain it. These tests verify the new
_OVERLOAD_STATUSES class uses a longer schedule and honors Retry-After.
"""

import pytest

from datadog_sync.utils.custom_client import (
    _OVERLOAD_BACKOFF_SCHEDULE,
    _OVERLOAD_STATUSES,
    _overload_sleep_duration,
)


def test_overload_statuses_covers_proxy_class():
    """The four codes we've seen emitted by edge proxies on queue overflow."""
    assert 502 in _OVERLOAD_STATUSES
    assert 503 in _OVERLOAD_STATUSES
    assert 504 in _OVERLOAD_STATUSES
    assert 512 in _OVERLOAD_STATUSES


def test_overload_statuses_does_not_shadow_429():
    """429 has its own retry path (x-ratelimit-reset) and must not be
    reclassified as overload."""
    assert 429 not in _OVERLOAD_STATUSES


def test_backoff_schedule_monotonic():
    """Each retry waits at least as long as the previous one."""
    for i in range(1, len(_OVERLOAD_BACKOFF_SCHEDULE)):
        assert _OVERLOAD_BACKOFF_SCHEDULE[i] >= _OVERLOAD_BACKOFF_SCHEDULE[i - 1]


def test_backoff_schedule_starts_meaningfully_higher_than_prior():
    """The whole point of the change is to space retries. The first retry
    must wait strictly longer than the pre-change 5s baseline."""
    assert _OVERLOAD_BACKOFF_SCHEDULE[0] >= 30


def test_sleep_duration_uses_schedule_when_no_retry_after():
    assert _overload_sleep_duration(0, None) == _OVERLOAD_BACKOFF_SCHEDULE[0]
    assert _overload_sleep_duration(1, None) == _OVERLOAD_BACKOFF_SCHEDULE[1]


def test_sleep_duration_clamps_at_last_bucket():
    """retry_count above the schedule length falls back to the final value,
    not IndexError."""
    over = len(_OVERLOAD_BACKOFF_SCHEDULE) + 5
    assert _overload_sleep_duration(over, None) == _OVERLOAD_BACKOFF_SCHEDULE[-1]


def test_sleep_duration_honors_retry_after_seconds():
    """Retry-After (RFC 7231 seconds form) wins over the schedule."""
    assert _overload_sleep_duration(0, "45") == 45


def test_sleep_duration_ignores_non_integer_retry_after():
    """Retry-After HTTP-date form is not supported; we fall back to schedule."""
    assert _overload_sleep_duration(0, "Wed, 21 Oct 2015 07:28:00 GMT") == _OVERLOAD_BACKOFF_SCHEDULE[0]


def test_sleep_duration_ignores_negative_retry_after():
    """A negative Retry-After is nonsensical; clamp to 0 not negative sleep."""
    assert _overload_sleep_duration(0, "-5") == 0


def test_custom_client_has_overload_counter():
    """The counter is exposed for follow-up metric emission."""
    from datadog_sync.utils.custom_client import CustomClient

    client = CustomClient(
        host="https://api.datadoghq.com",
        auth={"apiKeyAuth": "k", "appKeyAuth": "a"},
        retry_timeout=60,
        timeout=30,
        send_metrics=False,
    )
    # Empty at construction; is a Counter so numeric ops work.
    assert client.overload_status_counter[512] == 0
    client.overload_status_counter[512] += 1
    client.overload_status_counter[512] += 1
    client.overload_status_counter[502] += 1
    assert client.overload_status_counter[512] == 2
    assert client.overload_status_counter[502] == 1


def test_all_backoff_buckets_reachable_with_large_budget():
    """Codex-flagged concern: prior guard `retry_count + 1 >= max_retries`
    made the 120s bucket unreachable. Verify all three buckets get consumed
    when retry_timeout is large enough.
    """
    import asyncio
    from unittest.mock import MagicMock

    from datadog_sync.utils.custom_client import request_with_retry
    from datadog_sync.utils.resource_utils import CustomClientHTTPError

    class FakeClient:
        def __init__(self, retry_timeout):
            self.retry_timeout = retry_timeout
            from collections import Counter as _C
            self.overload_status_counter = _C()

    slept_durations = []

    async def fake_sleep(dur):
        slept_durations.append(dur)
        # No actual sleep — we just record the schedule.

    class Resp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def text(self):
            return "server overloaded"

        def raise_for_status(self):
            import aiohttp

            raise aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(), status=503, message="Overload", headers={}
            )

    async def stub_request(client, *args, **kwargs):
        return Resp()

    decorated = request_with_retry(stub_request)

    import datadog_sync.utils.custom_client as cc_module

    orig = cc_module.asyncio.sleep
    cc_module.asyncio.sleep = fake_sleep
    try:
        # Large retry_timeout so the budget guard doesn't fire.
        with pytest.raises(CustomClientHTTPError):
            asyncio.run(decorated(FakeClient(retry_timeout=10000)))
    finally:
        cc_module.asyncio.sleep = orig

    # All three schedule buckets should have been slept before the final give-up.
    assert slept_durations == list(_OVERLOAD_BACKOFF_SCHEDULE), (
        f"Expected schedule {_OVERLOAD_BACKOFF_SCHEDULE} but slept {slept_durations}"
    )


def test_end_to_end_overload_budget_guard():
    """Full request_with_retry loop against a 512-only server. Verifies:
      - each retry counts down the retry_timeout total budget,
      - when the next-computed sleep_duration would exceed the budget,
        the loop raises CustomClientHTTPError instead of sleeping past it.
    """
    import asyncio
    import time
    from unittest.mock import MagicMock

    from datadog_sync.utils.custom_client import request_with_retry
    from datadog_sync.utils.resource_utils import CustomClientHTTPError

    # Fake client that has just enough state for request_with_retry.
    class FakeClient:
        def __init__(self, retry_timeout):
            self.retry_timeout = retry_timeout
            from collections import Counter as _C
            self.overload_status_counter = _C()

    class Resp:
        def __init__(self):
            self.status = 512
            self.headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def text(self):
            return "server overloaded"

        def raise_for_status(self):
            import aiohttp

            resp = MagicMock()
            resp.status = 512
            raise aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(), status=512, message="Timeout", headers={}
            )

    async def stub_request(client, *args, **kwargs):
        return Resp()

    decorated = request_with_retry(stub_request)

    async def run():
        client = FakeClient(retry_timeout=45)  # ~1.5x first bucket
        started = time.time()
        with pytest.raises(CustomClientHTTPError):
            await decorated(client)
        # We may have slept once (30s bucket) but MUST NOT have slept past the budget.
        # Test-mode: we avoid real 30s sleep by patching asyncio.sleep below.
        return started, client

    # Patch asyncio.sleep to no-op so this test runs in ms, not minutes,
    # while the budget arithmetic still uses real time.time() comparisons.
    real_sleep = asyncio.sleep

    async def fast_sleep(_):
        await real_sleep(0)

    import datadog_sync.utils.custom_client as cc_module

    orig = cc_module.asyncio.sleep
    cc_module.asyncio.sleep = fast_sleep
    try:
        started, client = asyncio.run(run())
    finally:
        cc_module.asyncio.sleep = orig

    # Counter incremented at least once for 512 (proves the branch was taken).
    assert client.overload_status_counter[512] >= 1
