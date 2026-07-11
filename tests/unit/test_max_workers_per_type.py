# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for --max-workers-per-type parsing and override wiring.

Covers the CLI-parse layer (_parse_max_workers_per_type) and the apply
layer (build_config replacing resource_config.max_concurrent on match).
"""

import click
import pytest

from datadog_sync.utils.configuration import _parse_max_workers_per_type


KNOWN = ["monitors", "dashboards", "rum_applications", "roles"]


def test_empty_string_returns_empty_dict():
    assert _parse_max_workers_per_type("", KNOWN) == {}


def test_none_returns_empty_dict():
    assert _parse_max_workers_per_type(None, KNOWN) == {}


def test_single_pair():
    assert _parse_max_workers_per_type("monitors=20", KNOWN) == {"monitors": 20}


def test_multiple_pairs():
    got = _parse_max_workers_per_type("rum_applications=5,monitors=20,dashboards=25", KNOWN)
    assert got == {"rum_applications": 5, "monitors": 20, "dashboards": 25}


def test_whitespace_tolerant():
    # Operators paste these into shell configs; a stray space around '=' or ','
    # shouldn't be a rejection.
    got = _parse_max_workers_per_type(" monitors = 20 ,  dashboards=25 ", KNOWN)
    assert got == {"monitors": 20, "dashboards": 25}


def test_rejects_malformed_no_equals():
    with pytest.raises(click.UsageError, match="malformed pair"):
        _parse_max_workers_per_type("monitors20", KNOWN)


def test_rejects_malformed_multiple_equals():
    with pytest.raises(click.UsageError, match="malformed pair"):
        _parse_max_workers_per_type("monitors=20=30", KNOWN)


def test_rejects_empty_type():
    with pytest.raises(click.UsageError, match="empty resource type"):
        _parse_max_workers_per_type("=20", KNOWN)


def test_rejects_unknown_type():
    with pytest.raises(click.UsageError, match="unknown resource type 'not_a_type'"):
        _parse_max_workers_per_type("not_a_type=5", KNOWN)


def test_rejects_non_integer_value():
    with pytest.raises(click.UsageError, match="non-integer value"):
        _parse_max_workers_per_type("monitors=abc", KNOWN)


def test_rejects_zero():
    # Zero workers = permanent stall. Fail-fast rather than let the sync hang.
    with pytest.raises(click.UsageError, match="value must be positive"):
        _parse_max_workers_per_type("monitors=0", KNOWN)


def test_rejects_negative():
    with pytest.raises(click.UsageError, match="value must be positive"):
        _parse_max_workers_per_type("monitors=-1", KNOWN)


def test_rejects_duplicate_type():
    with pytest.raises(click.UsageError, match="duplicate resource type"):
        _parse_max_workers_per_type("monitors=5,monitors=10", KNOWN)


def test_trailing_comma_tolerated():
    # A trailing comma from string-concat mistakes shouldn't crash.
    assert _parse_max_workers_per_type("monitors=5,", KNOWN) == {"monitors": 5}


# ------------------------------------------------------------------
# build_config end-to-end: kwargs -> Configuration -> resources
# ------------------------------------------------------------------


def _base_kwargs(tmp_path):
    return dict(
        resources="users",
        resource_per_file=True,
        source_api_key="k",
        source_app_key="k",
        destination_api_key="k",
        destination_app_key="k",
        source_api_url="https://example.com",
        destination_api_url="https://example.com",
        storage_type="local",
        source_resources_path=str(tmp_path / "source"),
        destination_resources_path=str(tmp_path / "dest"),
        max_workers=1,
        send_metrics=False,
        verify_ddr_status=False,
        validate=False,
        show_progress_bar=False,
        allow_self_lockout=False,
        force_missing_dependencies=False,
        skip_failed_resource_connections=False,
    )


def test_build_config_no_flag_leaves_resources_unchanged(tmp_path):
    """Backward-compat: not passing --max-workers-per-type is a no-op."""
    from datadog_sync.constants import Command
    from datadog_sync.utils.configuration import build_config

    cfg = build_config(Command.IMPORT, **_base_kwargs(tmp_path))
    assert cfg.max_workers_per_type == {}
    # No resource_config should have had its max_concurrent forced by us —
    # verify a common resource still has its hard-coded default (currently
    # None for monitors per model/monitors.py).
    assert cfg.resources["monitors"].resource_config.max_concurrent is None


def test_build_config_applies_override_to_resource_config(tmp_path):
    """Passing --max-workers-per-type must mutate resources[rt].resource_config."""
    from datadog_sync.constants import Command
    from datadog_sync.utils.configuration import build_config

    kwargs = _base_kwargs(tmp_path)
    kwargs["max_workers_per_type"] = "monitors=8,rum_applications=5"
    cfg = build_config(Command.IMPORT, **kwargs)

    assert cfg.max_workers_per_type == {"monitors": 8, "rum_applications": 5}
    assert cfg.resources["monitors"].resource_config.max_concurrent == 8
    assert cfg.resources["rum_applications"].resource_config.max_concurrent == 5
    # Untouched types keep whatever the model set (or None).
    assert cfg.resources["roles"].resource_config.max_concurrent is None


def test_build_config_rejects_unknown_type(tmp_path):
    from datadog_sync.constants import Command
    from datadog_sync.utils.configuration import build_config

    kwargs = _base_kwargs(tmp_path)
    kwargs["max_workers_per_type"] = "not_a_type=5"
    with pytest.raises(click.UsageError, match="unknown resource type"):
        build_config(Command.IMPORT, **kwargs)


def test_build_config_does_not_leak_across_calls(tmp_path):
    """CRITICAL isolation test. `resource_config` is a class-level attribute
    on each BaseResource subclass. A naive `cfg.resources[rt].resource_config
    .max_concurrent = N` would mutate the shared class-level ResourceConfig
    and leak into subsequent build_config calls in the same process. This
    test asserts the fix: the second no-flag call sees the model default,
    not the first call's override."""
    from datadog_sync.constants import Command
    from datadog_sync.model.monitors import Monitors
    from datadog_sync.utils.configuration import build_config

    class_level_default = Monitors.resource_config.max_concurrent

    # First call: override monitors=8.
    kwargs1 = _base_kwargs(tmp_path)
    kwargs1["max_workers_per_type"] = "monitors=8"
    cfg1 = build_config(Command.IMPORT, **kwargs1)
    assert cfg1.resources["monitors"].resource_config.max_concurrent == 8

    # The class-level ResourceConfig must NOT have been mutated.
    assert Monitors.resource_config.max_concurrent == class_level_default, \
        "class-level ResourceConfig was mutated — override leaks across build_config calls"

    # Second call: no flag. Must see the model default, not 8.
    kwargs2 = _base_kwargs(tmp_path)
    cfg2 = build_config(Command.IMPORT, **kwargs2)
    assert cfg2.resources["monitors"].resource_config.max_concurrent == class_level_default


def test_build_config_fails_fast_before_state_load(tmp_path):
    """Malformed --max-workers-per-type must raise BEFORE storage is touched
    (no State() construction, no file I/O). Storage errors would otherwise
    mask the flag error and make debugging harder."""
    from datadog_sync.constants import Command
    from datadog_sync.utils.configuration import build_config

    kwargs = _base_kwargs(tmp_path)
    kwargs["max_workers_per_type"] = "monitors=abc"
    # Point storage paths at nonexistent locations so if the parser lets us
    # through, the State() load would raise a DIFFERENT error first.
    kwargs["source_resources_path"] = "/nonexistent/does/not/exist/source"
    kwargs["destination_resources_path"] = "/nonexistent/does/not/exist/dest"

    with pytest.raises(click.UsageError, match="non-integer value"):
        build_config(Command.IMPORT, **kwargs)


def test_override_reflected_in_async_semaphore(tmp_path):
    """End-to-end: after build_config sets max_concurrent, awaiting
    Configuration.init_async() must construct the asyncio.Semaphore with
    that value. Otherwise the override lands on the field but never affects
    the actual concurrency cap."""
    import asyncio

    from datadog_sync.constants import Command
    from datadog_sync.utils.configuration import build_config

    kwargs = _base_kwargs(tmp_path)
    kwargs["max_workers_per_type"] = "monitors=3"
    cfg = build_config(Command.IMPORT, **kwargs)

    async def _run():
        await cfg.init_async(Command.IMPORT)
        sem = cfg.resources["monitors"].resource_config.async_semaphore
        # Access the underlying value slot. Semaphore keeps the initial value
        # in _value in CPython; if this ever breaks, switch to acquiring 3
        # and asserting the 4th acquire blocks.
        assert sem is not None
        assert sem._value == 3

    try:
        asyncio.run(_run())
    finally:
        # Best-effort teardown — the fake clients don't require closing but
        # awaited init_session may leave state.
        pass
