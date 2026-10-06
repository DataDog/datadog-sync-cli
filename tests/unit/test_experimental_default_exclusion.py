# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Experimental resource types: registered and explicitly selectable, but
excluded from the default resource set until their family is accepted.

An experimental type (ResourceConfig.experimental=True) is:
- REGISTERED: present in init_resources / config.resources;
- EXPLICITLY SELECTABLE: `--resource <type>` works unchanged;
- DEFAULT-EXCLUDED: absent from the default (no --resource) set, unless the
  operator opts in via DD_INCLUDE_EXPERIMENTAL_RESOURCES=true (or the
  equivalent build_config kwarg).

Contract: docs/case-management-support.md §10 (support matrices) — new
families ship behind this gate until family acceptance flips it.
"""

import pytest

from datadog_sync.utils.base_resource import ResourceConfig


class _FakeResourceConfig:
    def __init__(self, experimental):
        self.experimental = experimental


class _FakeResource:
    def __init__(self, experimental=False):
        self.resource_config = _FakeResourceConfig(experimental)


def _fake_resources():
    # Includes the deprecated types the no---resources fallback path expects
    # to find in any registry (see _handle_deprecated's unconditional
    # .remove() of the legacy halves).
    return {
        "users": _FakeResource(experimental=False),
        "monitors": _FakeResource(experimental=False),
        "logs_pipelines": _FakeResource(experimental=False),
        "logs_custom_pipelines": _FakeResource(experimental=False),
        "downtimes": _FakeResource(experimental=False),
        "downtime_schedules": _FakeResource(experimental=False),
        "fake_experimental_type": _FakeResource(experimental=True),
    }


def _base_kwargs(tmp_path, **overrides):
    kwargs = dict(
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
    kwargs.update(overrides)
    return kwargs


# ─── ResourceConfig.experimental field ──────────────────────────────────────


class TestExperimentalField:
    def test_defaults_false(self):
        assert ResourceConfig(base_path="/x").experimental is False

    def test_explicit_true_accepted(self):
        assert ResourceConfig(base_path="/x", experimental=True).experimental is True


# ─── Flag resolution (kwargs > env > default) ────────────────────────────────


class TestResolveIncludeExperimental:
    def test_absent_everywhere_is_false(self, monkeypatch):
        monkeypatch.delenv("DD_INCLUDE_EXPERIMENTAL_RESOURCES", raising=False)
        from datadog_sync.utils.configuration import _resolve_include_experimental

        assert _resolve_include_experimental({}) is False

    def test_env_true_variants(self, monkeypatch):
        from datadog_sync.utils.configuration import _resolve_include_experimental

        for raw in ("true", "True", "1", "yes", "YES"):
            monkeypatch.setenv("DD_INCLUDE_EXPERIMENTAL_RESOURCES", raw)
            assert _resolve_include_experimental({}) is True, raw

    def test_env_false_variants(self, monkeypatch):
        from datadog_sync.utils.configuration import _resolve_include_experimental

        for raw in ("false", "0", "no", ""):
            monkeypatch.setenv("DD_INCLUDE_EXPERIMENTAL_RESOURCES", raw)
            assert _resolve_include_experimental({}) is False, raw

    def test_kwarg_overrides_env(self, monkeypatch):
        monkeypatch.setenv("DD_INCLUDE_EXPERIMENTAL_RESOURCES", "true")
        from datadog_sync.utils.configuration import _resolve_include_experimental

        assert _resolve_include_experimental({"include_experimental_resources": False}) is False
        monkeypatch.setenv("DD_INCLUDE_EXPERIMENTAL_RESOURCES", "false")
        assert _resolve_include_experimental({"include_experimental_resources": True}) is True


# ─── build_config wiring: default set / explicit selection ───────────────────


class TestBuildConfigWiring:
    @pytest.fixture(autouse=True)
    def _fake_registry(self, monkeypatch):
        import datadog_sync.utils.configuration as configuration_module

        monkeypatch.setattr(configuration_module, "init_resources", lambda cfg: _fake_resources())
        monkeypatch.delenv("DD_INCLUDE_EXPERIMENTAL_RESOURCES", raising=False)
        yield

    def test_default_set_excludes_experimental_types(self, tmp_path):
        from datadog_sync.constants import Command
        from datadog_sync.utils.configuration import build_config

        cfg = build_config(Command.IMPORT, **_base_kwargs(tmp_path))
        assert "fake_experimental_type" not in cfg.resources_arg
        # Non-experimental types all present (minus the deprecated halves the
        # legacy no---resources path always removes).
        assert set(cfg.resources_arg) == {"users", "monitors", "logs_pipelines", "downtime_schedules"}

    def test_experimental_type_registered_and_explicitly_selectable(self, tmp_path):
        from datadog_sync.constants import Command
        from datadog_sync.utils.configuration import build_config

        cfg = build_config(Command.IMPORT, resources="fake_experimental_type", **_base_kwargs(tmp_path))
        # Registered: present in config.resources even when excluded from the default set.
        assert "fake_experimental_type" in cfg.resources
        # Selectable: explicit --resource keeps it.
        assert cfg.resources_arg == ["fake_experimental_type"]

    def test_env_var_includes_experimental_in_default_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DD_INCLUDE_EXPERIMENTAL_RESOURCES", "true")
        from datadog_sync.constants import Command
        from datadog_sync.utils.configuration import build_config

        cfg = build_config(Command.IMPORT, **_base_kwargs(tmp_path))
        assert set(cfg.resources_arg) == {
            "users",
            "monitors",
            "logs_pipelines",
            "downtime_schedules",
            "fake_experimental_type",
        }

    def test_mixed_explicit_selection_unaffected_by_exclusion(self, tmp_path):
        from datadog_sync.constants import Command
        from datadog_sync.utils.configuration import build_config

        cfg = build_config(Command.IMPORT, resources="users,fake_experimental_type", **_base_kwargs(tmp_path))
        assert set(cfg.resources_arg) == {"users", "fake_experimental_type"}
