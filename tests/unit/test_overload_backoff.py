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
