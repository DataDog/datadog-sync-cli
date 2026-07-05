# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for ResourceConfig.max_concurrent per-resource-type semaphore.

Motivating case: HAMR-392 T5 — the destination monitor-create edge/proxy
returned HTTP 512 when sync-cli's 32-way worker pool queued too many
concurrent POST/PUT /api/v1/monitor requests. ResourceConfig gains an
optional per-type semaphore so specific resources can be throttled without
touching --max-workers globally.
"""

import asyncio

from datadog_sync.utils.base_resource import ResourceConfig


def test_max_concurrent_none_leaves_semaphore_unset():
    """Default behaviour: no cap installed, no semaphore attribute value."""
    cfg = ResourceConfig(base_path="/x")
    assert cfg.max_concurrent is None
    asyncio.run(cfg.init_async())
    assert cfg.async_semaphore is None


def test_max_concurrent_positive_installs_semaphore_after_init_async():
    """Positive int installs a Semaphore in init_async (not __post_init__,
    which runs outside the event loop)."""
    cfg = ResourceConfig(base_path="/x", max_concurrent=4)
    assert cfg.async_semaphore is None  # not created eagerly
    asyncio.run(cfg.init_async())
    assert isinstance(cfg.async_semaphore, asyncio.Semaphore)


def test_max_concurrent_zero_leaves_semaphore_unset():
    """0 is treated as "no cap" — same as None."""
    cfg = ResourceConfig(base_path="/x", max_concurrent=0)
    asyncio.run(cfg.init_async())
    assert cfg.async_semaphore is None


def test_semaphore_limits_concurrent_acquisitions():
    """Semaphore actually gates the number of concurrent holders."""

    async def scenario():
        cfg = ResourceConfig(base_path="/x", max_concurrent=2)
        await cfg.init_async()
        sem = cfg.async_semaphore
        # Fill both slots synchronously.
        await sem.acquire()
        await sem.acquire()
        # A third acquire must not resolve until we release.
        third = asyncio.create_task(sem.acquire())
        try:
            await asyncio.wait_for(asyncio.shield(third), timeout=0.05)
        except asyncio.TimeoutError:
            pass  # Expected: the semaphore is saturated.
        else:
            raise AssertionError("Expected third acquire to block")
        # Release one slot; third should now resolve.
        sem.release()
        await asyncio.wait_for(third, timeout=0.5)
        sem.release()
        sem.release()

    asyncio.run(scenario())


def test_max_concurrent_and_concurrent_false_coexist():
    """concurrent=False installs a Lock (serial). max_concurrent still
    populates a Semaphore, but the apply path picks the lock when
    concurrent=False (verified in _apply_resource_cb, not here).

    Runs under asyncio.run so the pre-existing eager-Lock creation for the
    concurrent=False path has a loop to bind to (Python 3.9 behavior).
    """

    async def scenario():
        cfg = ResourceConfig(base_path="/x", concurrent=False, max_concurrent=4)
        # Lock is installed for concurrent=False.
        assert cfg.async_lock is not None
        # Semaphore only after init_async.
        await cfg.init_async()
        assert isinstance(cfg.async_semaphore, asyncio.Semaphore)

    asyncio.run(scenario())
