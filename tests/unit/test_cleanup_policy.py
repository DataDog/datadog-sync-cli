# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Cleanup capability metadata: cleanup_policy, scope guards, and the
fail-closed unordered-fallback opt-out.

Contract (docs/case-management-support.md §5):

- ``cleanup_policy``: ``delete`` (default) | ``retain`` | ``unsupported``.
  Retained/unsupported resources are never queued or reported as deleted;
  retained resources keep blocking their ancestors through the dependency
  scan (retention closure). The no-delete case API makes cases ``retain``
  by definition.
- Filtered destination resources are excluded from BOTH the desired and
  deletion scopes by construction: a resource outside the import scope is
  not a deletion candidate.
- id-file-scoped types are non-authoritative for the run: their partial
  source state cannot drive deletion, so cleanup for those types is
  suppressed (type-wide authority; the conservative channel).
- ``cleanup_fail_closed``: when any cleanup-candidate type opts in, an
  ordered-cleanup failure must ABORT rather than fall back to unordered
  deletion — the family never takes the unordered fallback.
"""

from collections import defaultdict
from unittest.mock import MagicMock

import pytest

from datadog_sync.utils.base_resource import ResourceConfig

# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_model(resource_type: str, **config_overrides):
    r_class = MagicMock()
    r_class.resource_type = resource_type
    r_class.resource_config = MagicMock()
    # Explicit defaults so MagicMock attribute-auto-generation can't leak
    # truthy Mocks into the guards under test.
    r_class.resource_config.cleanup_policy = "delete"
    r_class.resource_config.cleanup_fail_closed = False
    for key, value in config_overrides.items():
        setattr(r_class.resource_config, key, value)
    r_class.filter = MagicMock(return_value=True)
    return r_class


def _make_config(models: dict, source: dict = None, destination: dict = None, id_payload: dict = None):
    from datadog_sync.utils.log import Log

    config = MagicMock()
    config.logger = Log(verbose=False)
    config.resources_arg = list(models.keys())
    config.resources = models
    config.filters = None
    config.filter_operator = None
    config.emit_json = False
    config.id_payload = id_payload
    config.state = MagicMock()
    config.state.source = defaultdict(dict, source or {})
    config.state.destination = defaultdict(dict, destination or {})
    return config


def _make_handler(config):
    from datadog_sync.utils.resources_handler import ResourcesHandler

    handler = ResourcesHandler(config)
    return handler


# ─── ResourceConfig.cleanup_policy field ─────────────────────────────────────


class TestCleanupPolicyField:
    def test_defaults_to_delete(self):
        assert ResourceConfig(base_path="/x").cleanup_policy == "delete"

    def test_valid_values_accepted(self):
        for policy in ("delete", "retain", "unsupported"):
            assert ResourceConfig(base_path="/x", cleanup_policy=policy).cleanup_policy == policy

    def test_invalid_value_rejected(self):
        with pytest.raises(ValueError, match="cleanup_policy"):
            ResourceConfig(base_path="/x", cleanup_policy="bogus")

    def test_cleanup_fail_closed_defaults_false(self):
        assert ResourceConfig(base_path="/x").cleanup_fail_closed is False


# ─── Candidate filtering: _filter_cleanup_candidates ─────────────────────────


class TestFilterCleanupCandidates:
    def _run(self, models, candidates, source=None, destination=None, id_payload=None):
        config = _make_config(models, source=source, destination=destination, id_payload=id_payload)
        handler = _make_handler(config)
        return handler._filter_cleanup_candidates(dict(candidates))

    def test_delete_policy_resources_kept(self):
        models = {"monitors": _make_model("monitors", cleanup_policy="delete")}
        candidates = {("monitors", "mon-1"): None}
        filtered, accounting = self._run(models, candidates)
        assert filtered == {("monitors", "mon-1"): None}
        assert accounting["retained"] == 0

    def test_retained_resources_never_queued(self):
        models = {
            "cases": _make_model("cases", cleanup_policy="retain"),
            "monitors": _make_model("monitors", cleanup_policy="delete"),
        }
        candidates = {("cases", "case-1"): None, ("cases", "case-2"): None, ("monitors", "mon-1"): None}
        filtered, accounting = self._run(models, candidates)
        assert filtered == {("monitors", "mon-1"): None}
        assert accounting["retained"] == 2

    def test_unsupported_policy_excludes_from_candidates(self):
        models = {
            "family_type": _make_model("family_type", cleanup_policy="unsupported"),
            "monitors": _make_model("monitors", cleanup_policy="delete"),
        }
        candidates = {("family_type", "f-1"): None, ("monitors", "mon-1"): None}
        filtered, accounting = self._run(models, candidates)
        assert filtered == {("monitors", "mon-1"): None}
        assert accounting["unsupported"] == 1

    def test_filtered_destination_resources_excluded_by_construction(self):
        """A destination resource outside the import scope (the type's
        filter rejects it) is not a deletion candidate: it was never in the
        desired scope, so deleting it would be out-of-scope destruction."""
        in_scope = {"id": "keep-me", "name": "wanted"}
        out_of_scope = {"id": "leave-me", "name": "unwanted"}
        models = {"monitors": _make_model("monitors")}
        models["monitors"].filter = MagicMock(side_effect=lambda r: r["name"] == "wanted")
        candidates = {("monitors", "keep-me"): None, ("monitors", "leave-me"): None}
        filtered, accounting = self._run(
            models,
            candidates,
            destination={"monitors": {"keep-me": in_scope, "leave-me": out_of_scope}},
        )
        assert filtered == {("monitors", "keep-me"): None}
        assert accounting["out_of_scope_filtered"] == 1

    def test_id_scoped_type_deletion_suppressed(self):
        """id-file-scoped types are non-authoritative for the run: their
        partial source state must not drive deletion (type-wide authority)."""
        models = {
            "scoped_type": _make_model("scoped_type", cleanup_policy="delete"),
            "monitors": _make_model("monitors", cleanup_policy="delete"),
        }
        candidates = {("scoped_type", "s-1"): None, ("monitors", "mon-1"): None}
        filtered, accounting = self._run(
            models,
            candidates,
            id_payload={"scoped_type": ["parent-a"]},
        )
        assert filtered == {("monitors", "mon-1"): None}
        assert accounting["id_scoped"] == 1

    def test_missing_destination_body_still_candidate(self):
        """A candidate with no destination body available (state shape
        varies) must not crash the filter — it stays a candidate and the
        scope check is skipped for it."""
        models = {"monitors": _make_model("monitors")}
        models["monitors"].filter = MagicMock(return_value=False)
        candidates = {("monitors", "no-body"): None}
        filtered, accounting = self._run(models, candidates)
        # No body to evaluate the filter against: cannot prove out-of-scope,
        # so the candidate is kept (fail-open only for the scope check —
        # cleanup_policy/id-scope guards still apply).
        assert filtered == {("monitors", "no-body"): None}

    def test_all_guards_combined(self):
        models = {
            "cases": _make_model("cases", cleanup_policy="retain"),
            "family_type": _make_model("family_type", cleanup_policy="unsupported"),
            "scoped_type": _make_model("scoped_type", cleanup_policy="delete"),
            "monitors": _make_model("monitors", cleanup_policy="delete"),
        }
        candidates = {
            ("cases", "case-1"): None,
            ("family_type", "f-1"): None,
            ("scoped_type", "s-1"): None,
            ("monitors", "mon-1"): None,
        }
        filtered, accounting = self._run(models, candidates, id_payload={"scoped_type": ["parent-a"]})
        assert filtered == {("monitors", "mon-1"): None}
        assert accounting["retained"] == 1
        assert accounting["unsupported"] == 1
        assert accounting["id_scoped"] == 1


# ─── Fail-closed unordered-fallback opt-out ──────────────────────────────────


class TestUnorderedFallbackAllowed:
    def test_allowed_by_default(self):
        models = {"monitors": _make_model("monitors")}
        config = _make_config(models)
        handler = _make_handler(config)
        assert handler._unordered_fallback_allowed({("monitors", "mon-1"): None}) is True

    def test_blocked_when_any_candidate_type_opts_in(self):
        """The case family never takes the unordered fallback: any
        cleanup_fail_closed type in the candidate set aborts instead."""
        models = {
            "rules": _make_model("rules", cleanup_fail_closed=True),
            "monitors": _make_model("monitors"),
        }
        config = _make_config(models)
        handler = _make_handler(config)
        candidates = {("rules", "rule-1"): None, ("monitors", "mon-1"): None}
        assert handler._unordered_fallback_allowed(candidates) is False

    def test_empty_candidate_set_allowed(self):
        models = {"rules": _make_model("rules", cleanup_fail_closed=True)}
        config = _make_config(models)
        handler = _make_handler(config)
        assert handler._unordered_fallback_allowed({}) is True
