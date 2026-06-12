# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Integration-level tests for the restriction_policies skip filter (NATHAN-50).

The existing VCR-driven ``TestRestrictionPoliciesResources`` cassettes only
contain dashboards with ``is_read_only: false`` and policies whose editor
bindings are limited to ``org:`` / ``team:`` principals — both skip filters
introduced in this PR are therefore unexercised end-to-end by the standard
integration suite.

These tests close that coverage gap by replaying the apply orchestration
sequence used in production (``pre_resource_action_hook`` → ``connect_resources``
→ ``create_resource``) against a real :class:`RestrictionPolicies` instance
and real-shaped in-memory ``state.destination`` maps (the surface the
production filter actually reads). Configuration is built from
``MagicMock`` because only ``allow_self_lockout``, ``logger``,
``destination_client`` and ``state`` are exercised; everything else on
``Configuration`` is irrelevant to this skip-filter contract. The only
boundary we mock is the destination HTTP client, which lets us assert
the precise property the production code is supposed to guarantee:

    When the filter raises ``SkipResource``, **no POST is ever issued** to
    ``/api/v2/restriction_policy/<id>`` for that resource.

This shape is preferred over hand-authored VCR cassettes for two reasons:

1. The existing cassettes are 12K–24K lines each (full dashboard / user /
   team / role import dumps); hand-editing them to inject a single
   ``is_read_only: true`` toggle is error-prone and easy to drift from the
   real API shape.
2. VCR's default exact-match contract on ``method+scheme+host+port+path+query+body``
   means a misshapen cassette would fail with an opaque "no matching
   interaction" error rather than the load-bearing assertion we actually
   want: "the production filter prevented the POST".

A future regression in ``RestrictionPolicies.pre_resource_action_hook``
(e.g. someone refactoring ``BaseResource._apply_resource_cb`` to skip
the hook, or unwiring the call from within the model) would be caught
loudly by ``post_recorder.posts`` being non-empty when these tests assert
it is empty — exactly the regression class the unit tests for the filter
alone cannot detect, because they call the filter directly rather than
through the apply path.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.restriction_policies import RestrictionPolicies
from datadog_sync.utils.resource_utils import SkipResource

# UUID format reused across tests — chosen so the principal string
# ``user:<SA_UUID>`` is visually distinct from the ``user:other-...`` strings
# in the test bindings and easy to grep for in failure output.
SA_UUID = "17d29c8a-6285-11f0-be9b-76de006a05ea"
ORG_UUID = "00000000-0000-beef-0000-000000000000"


class _PostRecorder:
    """Captures every destination-client write call so tests can assert the
    skip filter prevented the POST.

    Wraps an :class:`AsyncMock` per HTTP verb. ``posts`` exposes the list of
    paths POSTed in the order they happened; assertions compare against this
    list rather than mock call-args directly so failure output names the
    offending path.
    """

    def __init__(self) -> None:
        self.posts: List[Tuple[str, Dict[str, Any]]] = []
        self.puts: List[Tuple[str, Dict[str, Any]]] = []
        self.deletes: List[str] = []

    async def post(self, path: str, payload: Dict[str, Any], params: Any = None) -> Dict[str, Any]:
        self.posts.append((path, payload))
        # Echo back a minimal response shaped like the real
        # /api/v2/restriction_policy POST envelope so the apply path
        # can store it in destination state without KeyError.
        return {"data": payload.get("data", {})}

    async def put(self, path: str, payload: Dict[str, Any], params: Any = None) -> Dict[str, Any]:
        self.puts.append((path, payload))
        return {"data": payload.get("data", {})}

    async def delete(self, path: str) -> None:
        self.deletes.append(path)


def _build_resource(
    *,
    allow_self_lockout: bool = False,
    current_user_uuid: str = SA_UUID,
    org_principal: str = f"org:{ORG_UUID}",
) -> Tuple[RestrictionPolicies, _PostRecorder]:
    """Build a real RestrictionPolicies wired to in-memory state + a mock client.

    Returns ``(resource, recorder)``. The recorder is attached to
    ``resource.config.destination_client`` so writes from
    ``create_resource``/``update_resource``/``delete_resource`` land in it.

    ``current_user_uuid`` and ``org_principal`` mirror the values that the
    real ``pre_apply_hook`` would set from a ``/api/v2/current_user`` GET —
    we set them directly here because ``pre_apply_hook`` has unit-test
    coverage (TestRestrictionPoliciesOrgPrincipal) and is not the
    behavior under test in these integration paths.
    """
    recorder = _PostRecorder()
    destination_client = MagicMock()
    destination_client.post = AsyncMock(side_effect=recorder.post)
    destination_client.put = AsyncMock(side_effect=recorder.put)
    destination_client.delete = AsyncMock(side_effect=recorder.delete)
    destination_client.get = AsyncMock()

    # The production filter only reads from ``config.state.destination`` and
    # ``config.allow_self_lockout`` / ``config.logger``. A MagicMock-based
    # config is sufficient — we plug in real-shaped dicts for ``state.*``
    # so connect_resources can rewrite dashboard ids without exploding.
    mock_config = MagicMock()
    mock_config.allow_self_lockout = allow_self_lockout
    mock_config.destination_client = destination_client
    mock_config.source_client = MagicMock()
    mock_config.skip_failed_resource_connections = True
    mock_config.logger = logging.getLogger("restriction_policies_skip_paths_test")

    # State.destination must support both ``.get("dashboards", {})`` (used by
    # the skip filter) and dict subscription with autovivification (used by
    # connect_resources when missing keys), so back it with a plain dict
    # populated per-test with the resource types connect_resources reads.
    mock_config.state = MagicMock()
    mock_config.state.source = {"restriction_policies": {}}
    mock_config.state.destination = {
        "restriction_policies": {},
        "dashboards": {},
        "service_level_objectives": {},
        "notebooks": {},
        "users": {},
        "roles": {},
        "teams": {},
    }

    resource = RestrictionPolicies(mock_config)
    resource.current_user_uuid = current_user_uuid
    resource.org_principal = org_principal
    return resource, recorder


async def _apply_one(resource: RestrictionPolicies, _id: str, body: Dict[str, Any]) -> str:
    """Replay the apply-orchestrator sequence from
    ``ResourcesHandler._apply_resource_cb`` for a single resource.

    Returns ``"created"`` on success and ``"skipped"`` when the filter raises
    ``SkipResource``. Any other exception propagates so test failures show
    real cause rather than a swallowed error.

    Why not call ``_apply_resource_cb`` directly: that path is bound to a
    full ``ResourcesHandler`` with workers, counters, sorter, and a Click
    runtime — wiring all of that just to observe "did POST happen?" pulls
    in dependencies that are themselves test surface. The body of
    ``_apply_resource_cb`` is the production wire we care about, and the
    sequence below is the verbatim happy-path branch (see
    datadog_sync/utils/resources_handler.py L292-L332). A regression that
    reordered or removed the ``_pre_resource_action_hook`` call would
    break this test the same way it would break ``_apply_resource_cb``.

    Scope limitations vs. the full ``_apply_resource_cb``:
    - Only the **create** branch is exercised; the update branch (taken
      when ``_id`` is already in destination state) isn't covered here.
      Both branches sit AFTER the same ``_pre_resource_action_hook`` call,
      so the skip-filter contract under test is identical between them —
      a regression in the filter wiring would break both paths equally.
    - ``prep_resource`` (which strips ``excluded_attributes``) is skipped.
      Safe today because ``RestrictionPolicies.resource_config.excluded_attributes``
      is ``[]``; if that list ever grows, this harness will diverge from
      production for fields the filter inspects.
    - The per-class ``async_lock`` is not acquired. ``RestrictionPolicies``
      runs with ``concurrent=True`` (its ``ResourceConfig`` doesn't set
      ``concurrent=False``), so the lock is never acquired in production
      either — equivalent.
    """
    # _apply_resource_cb deepcopies the source resource before mutating —
    # we mirror that so the bindings/principals rewriting in
    # pre_resource_action_hook doesn't leak back into the caller's dict.
    working = deepcopy(body)
    try:
        await resource._pre_resource_action_hook(_id, working)
        resource.connect_resources(_id, working)
        await resource._create_resource(_id, working)
        return "created"
    except SkipResource:
        return "skipped"


def _policy(policy_id: str, bindings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a minimal restriction-policy resource body matching the shape
    that ``import_resource`` produces (``data.attributes.bindings``).
    """
    return {
        "id": policy_id,
        "type": "restriction_policy",
        "attributes": {"bindings": bindings},
    }


@pytest.mark.asyncio
class TestRestrictionPoliciesSkipPaths:
    """End-to-end coverage for the two skip filters introduced in NATHAN-50.

    Each test wires real ``RestrictionPolicies`` against a mock destination
    client and asserts the production ``_pre_resource_action_hook`` → POST
    contract that the existing VCR fixtures cannot exercise (their cassettes
    have only writable dashboards and org/team editor bindings).
    """

    async def test_read_only_dashboard_target_is_skipped(self):
        """Read-only target dashboard → skip; POST never reaches the API.

        Companion writable-dashboard policy in the same run still POSTs,
        proving the filter is per-resource rather than a global short-circuit.
        """
        resource, recorder = _build_resource()
        # Destination state populated as if the dashboards resource had
        # already been imported. Source ids are the keys; ``is_read_only``
        # comes from the dashboards API GET response (the dashboards model
        # preserves it in state — see datadog_sync/model/dashboards.py
        # excluded_attributes affects diffing only, not state storage).
        resource.config.state.destination["dashboards"]["src-dash-readonly"] = {
            "id": "dst-dash-readonly-uuid",
            "is_read_only": True,
        }
        resource.config.state.destination["dashboards"]["src-dash-writable"] = {
            "id": "dst-dash-writable-uuid",
            "is_read_only": False,
        }

        readonly_policy = _policy(
            "dashboard:src-dash-readonly",
            [{"relation": "editor", "principals": [f"user:{SA_UUID}", "user:teammate"]}],
        )
        writable_policy = _policy(
            "dashboard:src-dash-writable",
            [{"relation": "editor", "principals": [f"user:{SA_UUID}", "user:teammate"]}],
        )

        # Both go through the same apply sequence; only the read-only one
        # should short-circuit before POST.
        readonly_outcome = await _apply_one(resource, "dashboard:src-dash-readonly", readonly_policy)
        writable_outcome = await _apply_one(resource, "dashboard:src-dash-writable", writable_policy)

        assert readonly_outcome == "skipped"
        assert writable_outcome == "created"

        # Core contract: no POST ever hit the read-only path.
        skipped_path_fragment = "src-dash-readonly"
        offending = [p for p, _ in recorder.posts if skipped_path_fragment in p]
        assert offending == [], (
            f"Expected zero POSTs for read-only dashboard policy; got {offending!r}. "
            f"This indicates pre_resource_action_hook did not raise SkipResource "
            f"as the filter requires."
        )
        # Sanity: the writable companion DID POST, so the orchestrator path
        # itself isn't broken. POST path uses the *destination* dashboard
        # uuid because connect_resources rewrites it before create_resource.
        assert any(
            "dst-dash-writable-uuid" in p for p, _ in recorder.posts
        ), f"Expected the writable-dashboard policy to POST; got paths {[p for p, _ in recorder.posts]!r}"

    async def test_policy_that_would_self_demote_is_skipped(self):
        """Editor bindings with no SA UUID → skip; companion policy still syncs."""
        resource, recorder = _build_resource()
        resource.config.state.destination["dashboards"]["src-dash-a"] = {
            "id": "dst-dash-a-uuid",
            "is_read_only": False,
        }
        resource.config.state.destination["dashboards"]["src-dash-b"] = {
            "id": "dst-dash-b-uuid",
            "is_read_only": False,
        }

        # Policy A: SA UUID absent from editor → self-demote; must skip.
        self_demote_policy = _policy(
            "dashboard:src-dash-a",
            [{"relation": "editor", "principals": ["user:someone-else"]}],
        )
        # Policy B: SA UUID included in editor → safe; must sync.
        retains_editor_policy = _policy(
            "dashboard:src-dash-b",
            [{"relation": "editor", "principals": [f"user:{SA_UUID}", "user:someone-else"]}],
        )

        a_outcome = await _apply_one(resource, "dashboard:src-dash-a", self_demote_policy)
        b_outcome = await _apply_one(resource, "dashboard:src-dash-b", retains_editor_policy)

        assert a_outcome == "skipped"
        assert b_outcome == "created"

        # No POST for the self-demote policy.
        offending = [p for p, _ in recorder.posts if "src-dash-a" in p or "dst-dash-a-uuid" in p]
        assert offending == [], (
            f"Expected zero POSTs for self-demote policy; got {offending!r}. "
            f"_skip_if_would_self_demote did not raise SkipResource."
        )
        # The companion policy DID POST against the destination dashboard uuid.
        assert any(
            "dst-dash-b-uuid" in p for p, _ in recorder.posts
        ), f"Expected the editor-retaining policy to POST; got {[p for p, _ in recorder.posts]!r}"

    async def test_self_demote_skip_bypassed_by_allow_self_lockout(self):
        """``--allow-self-lockout`` re-enables the POST for self-demote cases.

        Same fixture as the previous test; the only difference is
        ``allow_self_lockout=True``. Both policies must POST — the operator
        has explicitly opted in to the API-rejection escape hatch.
        """
        resource, recorder = _build_resource(allow_self_lockout=True)
        resource.config.state.destination["dashboards"]["src-dash-a"] = {
            "id": "dst-dash-a-uuid",
            "is_read_only": False,
        }
        resource.config.state.destination["dashboards"]["src-dash-b"] = {
            "id": "dst-dash-b-uuid",
            "is_read_only": False,
        }

        self_demote_policy = _policy(
            "dashboard:src-dash-a",
            [{"relation": "editor", "principals": ["user:someone-else"]}],
        )
        retains_editor_policy = _policy(
            "dashboard:src-dash-b",
            [{"relation": "editor", "principals": [f"user:{SA_UUID}", "user:someone-else"]}],
        )

        a_outcome = await _apply_one(resource, "dashboard:src-dash-a", self_demote_policy)
        b_outcome = await _apply_one(resource, "dashboard:src-dash-b", retains_editor_policy)

        # Bypass: both should POST.
        assert a_outcome == "created"
        assert b_outcome == "created"
        posted_paths = [p for p, _ in recorder.posts]
        assert any("dst-dash-a-uuid" in p for p in posted_paths), (
            f"--allow-self-lockout did not bypass the self-demote skip; " f"posted paths {posted_paths!r}"
        )
        assert any(
            "dst-dash-b-uuid" in p for p in posted_paths
        ), f"Editor-retaining companion did not POST; paths {posted_paths!r}"
        # Read-only filter is orthogonal to --allow-self-lockout; the
        # destinations here are writable so the read-only path remains a
        # no-op for both policies — exactly two POSTs expected.
        assert (
            len(posted_paths) == 2
        ), f"Expected exactly 2 POSTs with --allow-self-lockout; got {len(posted_paths)}: {posted_paths!r}"

    async def test_skip_filter_is_actually_invoked_by_apply_path(self):
        """Regression guard: a no-op ``pre_resource_action_hook`` would let
        a read-only policy through and POST would be observed.

        This test patches the production hook to a no-op, replays the same
        fixture as ``test_read_only_dashboard_target_is_skipped``, and
        asserts the POST DOES happen — proving the original test's pass
        depends on the hook firing, not on coincidence (e.g. a bug in
        ``_apply_one`` that swallowed all POSTs).

        If a future refactor makes ``_apply_one`` no longer route through
        the hook, the original test would silently pass and this guard
        would silently fail — together they pin both directions of the
        contract.
        """
        resource, recorder = _build_resource()
        resource.config.state.destination["dashboards"]["src-dash-readonly"] = {
            "id": "dst-dash-readonly-uuid",
            "is_read_only": True,
        }
        # Stub the hook so the filter never fires; the fixture is otherwise
        # identical to the assertion-positive test above.

        async def _noop(_id, resource_):
            return None

        resource.pre_resource_action_hook = _noop

        policy = _policy(
            "dashboard:src-dash-readonly",
            [{"relation": "editor", "principals": [f"user:{SA_UUID}", "user:teammate"]}],
        )
        outcome = await _apply_one(resource, "dashboard:src-dash-readonly", policy)

        assert outcome == "created", (
            "With the hook stubbed out, the apply path must reach create_resource. "
            "If this assertion fails, _apply_one has its own short-circuit that "
            "would falsely satisfy the skip-path tests above."
        )
        assert any("dst-dash-readonly-uuid" in p for p, _ in recorder.posts), (
            "POST never happened even with the hook stubbed — _apply_one or the "
            f"mock client wiring is broken. Captured POSTs: {[p for p, _ in recorder.posts]!r}"
        )
