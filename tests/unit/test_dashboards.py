# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for dashboards resource configuration and short-circuit.

Pins the list_omitted_attr_prefixes opt-in, the prefetched-body
short-circuit shared with notebooks, and the read-only-conflict
clone-on-update fallback.
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.dashboards import Dashboards
from datadog_sync.utils.resource_utils import CustomClientHTTPError, ResourceConnectionError, SkipResource
from datadog_sync.utils.workers import Counter


def _read_only_error() -> CustomClientHTTPError:
    resp = MagicMock()
    resp.status = 403
    resp.message = "Forbidden"
    return CustomClientHTTPError(resp, message='{"errors": ["This dashboard is read-only"]}')


def _user_no_permission_error() -> CustomClientHTTPError:
    resp = MagicMock()
    resp.status = 403
    resp.message = "Forbidden"
    return CustomClientHTTPError(
        resp,
        message=(
            '{"errors":[{"status":"403","title":"Forbidden",'
            '"detail":"user does not have permission to edit this dashboard"}]}'
        ),
    )


def _unrelated_403_error() -> CustomClientHTTPError:
    resp = MagicMock()
    resp.status = 403
    resp.message = "Forbidden"
    return CustomClientHTTPError(
        resp,
        # Generic "does not have permission" wording that must NOT match — the
        # marker is "user does not have permission to edit", not just "permission".
        message='{"errors":["user does not have permission for widget datasource"]}',
    )


def test_resource_config_list_omitted_attr_prefixes():
    """Dashboards LIST omits widgets; --filter on widgets.* needs the post-GET
    re-filter to evaluate against the per-id GET payload.
    """
    mock_config = MagicMock()
    dashboards = Dashboards(mock_config)
    assert "widgets" in dashboards.resource_config.list_omitted_attr_prefixes, (
        "dashboards LIST omits widgets; the widgets prefix must be declared "
        "so the handler defers widgets.* filters to the post-GET pass"
    )


def test_import_resource_short_circuits_when_caller_supplies_full_body():
    """Pre-fetched body short-circuit: when the --id-file path drives
    get_resources_by_ids → import_resource(_id=id_), the returned body is
    placed in tmp_storage and the queue handler later calls
    _import_resource(resource=full_body). Without the short-circuit,
    dashboards.import_resource would do a SECOND GET per dashboard,
    doubling rate-limit pressure on id-file runs. Detection by 'widgets'
    presence — the LIST endpoint never returns widgets, so a body with
    widgets came from a prior per-id GET. Pre-existing latent issue in
    this model; addressed alongside the notebooks lightweight-LIST change.
    """
    mock_config = MagicMock()
    mock_config.source_client = AsyncMock()
    dashboards = Dashboards(mock_config)
    full_body = {
        "id": "abc-123",
        "title": "prefetched",
        "widgets": [{"definition": {"type": "timeseries"}}],
    }

    _id, resource = asyncio.run(dashboards.import_resource(resource=full_body))

    mock_config.source_client.get.assert_not_awaited()
    assert _id == "abc-123"
    assert resource["widgets"]


def _make_dashboards_with_destination(dst_state: dict) -> Dashboards:
    mock_config = MagicMock()
    mock_config.state = MagicMock()
    mock_config.state.destination = {"dashboards": {"abc-123": dst_state}}
    mock_config.destination_client = AsyncMock()
    mock_config.logger = MagicMock()
    dashboards = Dashboards(mock_config)
    return dashboards


class TestDashboardsReadOnlyConflict:
    """Coverage for the PUT-403-read-only fallback path introduced in T29.

    When the destination refuses the update because the copy on that side is
    read-only for the sync identity, we fall back to POST (clone) so the
    customer's source content still lands on destination. If the destination
    is an unmodified Datadog-suggested preset AND source matches it byte-for-
    byte on the sync-relevant fields, we SkipResource instead.
    """

    def test_update_falls_back_to_clone_on_read_only_error(self):
        """PUT returns 403 'This dashboard is read-only' -> POST is issued
        with the source body and its response is returned as the new state."""
        dashboards = _make_dashboards_with_destination({"id": "dst-old"})
        dashboards.config.destination_client.put = AsyncMock(side_effect=_read_only_error())
        dashboards.config.destination_client.post = AsyncMock(return_value={"id": "dst-new", "title": "src"})
        source = {"title": "src", "widgets": [], "tags": []}

        _id, resp = asyncio.run(dashboards.update_resource("abc-123", source))

        dashboards.config.destination_client.put.assert_awaited_once()
        dashboards.config.destination_client.post.assert_awaited_once_with("/api/v1/dashboard", source)
        assert _id == "abc-123"
        assert resp == {"id": "dst-new", "title": "src"}

    def test_update_falls_back_to_clone_on_user_permission_error(self):
        """The alternate JSON:API-shaped 403 body triggers the same clone path."""
        dashboards = _make_dashboards_with_destination({"id": "dst-old"})
        dashboards.config.destination_client.put = AsyncMock(side_effect=_user_no_permission_error())
        dashboards.config.destination_client.post = AsyncMock(return_value={"id": "dst-new"})
        source = {"title": "src", "widgets": []}

        _id, resp = asyncio.run(dashboards.update_resource("abc-123", source))

        dashboards.config.destination_client.post.assert_awaited_once()
        assert resp["id"] == "dst-new"

    def test_update_reraises_unrelated_403(self):
        """A 403 that is not the read-only class must not silently clone —
        the caller upstream needs to see it as a real failure."""
        dashboards = _make_dashboards_with_destination({"id": "dst-old"})
        err = _unrelated_403_error()
        dashboards.config.destination_client.put = AsyncMock(side_effect=err)
        dashboards.config.destination_client.post = AsyncMock()

        with pytest.raises(CustomClientHTTPError):
            asyncio.run(dashboards.update_resource("abc-123", {"title": "src"}))
        dashboards.config.destination_client.post.assert_not_awaited()

    def test_update_reraises_generic_permission_403_without_edit_marker(self):
        """A 403 whose body says 'does not have permission' but WITHOUT the
        read-only edit marker must propagate. Pins the narrowed marker so a
        future change back to a broad substring doesn't silently start
        cloning dashboards for unrelated RBAC / scope failures."""
        resp = MagicMock()
        resp.status = 403
        resp.message = "Forbidden"
        err = CustomClientHTTPError(
            resp,
            message='{"errors":["scope check failed: does not have permission"]}',
        )
        dashboards = _make_dashboards_with_destination({"id": "dst-old"})
        dashboards.config.destination_client.put = AsyncMock(side_effect=err)
        dashboards.config.destination_client.post = AsyncMock()

        with pytest.raises(CustomClientHTTPError):
            asyncio.run(dashboards.update_resource("abc-123", {"title": "src"}))
        dashboards.config.destination_client.post.assert_not_awaited()

    def test_suggested_preset_matching_source_is_skipped(self):
        """Unmodified suggested-preset on destination with matching source
        content across all diffed fields: skip. Nothing to sync."""
        dst_state = {
            "id": "dst-old",
            "description": "some intro [[suggested_dashboards]]",
            "title": "preset title",
            "widgets": [{"definition": {"type": "timeseries"}}],
            "template_variables": [],
            "tags": [],
            "layout_type": "ordered",
            "reflow_type": "auto",
        }
        source = {
            "title": "preset title",
            "widgets": [{"definition": {"type": "timeseries"}}],
            "template_variables": [],
            "tags": [],
            "description": "some intro [[suggested_dashboards]]",
            "layout_type": "ordered",
            "reflow_type": "auto",
        }
        dashboards = _make_dashboards_with_destination(dst_state)
        dashboards.config.destination_client.put = AsyncMock(side_effect=_read_only_error())
        dashboards.config.destination_client.post = AsyncMock()

        with pytest.raises(SkipResource) as exc:
            asyncio.run(dashboards.update_resource("abc-123", source))
        assert "suggested" in str(exc.value).lower()
        dashboards.config.destination_client.post.assert_not_awaited()

    def test_suggested_preset_with_layout_type_divergence_is_cloned(self):
        """Regression pin: the preset-match predicate must not restrict itself
        to a hand-picked subset of fields. If source diverges from destination
        on layout_type (a dashboard-shape field), the customer forked the
        preset — clone, do not skip."""
        dst_state = {
            "id": "dst-old",
            "description": "[[suggested_dashboards]]",
            "title": "preset title",
            "widgets": [],
            "template_variables": [],
            "tags": [],
            "layout_type": "ordered",
            "reflow_type": "auto",
        }
        dashboards = _make_dashboards_with_destination(dst_state)
        dashboards.config.destination_client.put = AsyncMock(side_effect=_read_only_error())
        dashboards.config.destination_client.post = AsyncMock(return_value={"id": "dst-new"})
        source = {
            "title": "preset title",
            "widgets": [],
            "template_variables": [],
            "tags": [],
            "description": "[[suggested_dashboards]]",
            # layout_type changed on source; the old five-field match would
            # incorrectly declare "unchanged" and skip. check_diff catches it.
            "layout_type": "free",
            "reflow_type": "auto",
        }

        _id, resp = asyncio.run(dashboards.update_resource("abc-123", source))
        dashboards.config.destination_client.post.assert_awaited_once()
        assert resp["id"] == "dst-new"

    def test_suggested_preset_with_diverged_source_is_cloned(self):
        """Source customized on top of a suggested preset: clone, don't skip
        (would otherwise lose customer edits)."""
        dst_state = {
            "id": "dst-old",
            "description": "[[suggested_dashboards]]",
            "title": "preset title",
            "widgets": [],
            "template_variables": [],
            "tags": [],
        }
        dashboards = _make_dashboards_with_destination(dst_state)
        dashboards.config.destination_client.put = AsyncMock(side_effect=_read_only_error())
        dashboards.config.destination_client.post = AsyncMock(return_value={"id": "dst-new"})
        source = {
            "title": "preset title",
            "widgets": [{"definition": {"type": "timeseries"}}],  # customer added a widget
            "template_variables": [],
            "tags": [],
            "description": "[[suggested_dashboards]]",
        }

        _id, resp = asyncio.run(dashboards.update_resource("abc-123", source))
        dashboards.config.destination_client.post.assert_awaited_once_with("/api/v1/dashboard", source)
        assert resp["id"] == "dst-new"

    def test_read_only_conflict_with_no_description_still_clones(self):
        """If destination state has no `description` field, the
        suggested-preset check must not crash and must clone."""
        dashboards = _make_dashboards_with_destination({"id": "dst-old"})
        dashboards.config.destination_client.put = AsyncMock(side_effect=_read_only_error())
        dashboards.config.destination_client.post = AsyncMock(return_value={"id": "dst-new"})
        source = {"title": "src"}

        _id, resp = asyncio.run(dashboards.update_resource("abc-123", source))
        assert resp["id"] == "dst-new"

    def test_non_403_error_reraised(self):
        """A 500 during update propagates as-is (no clone attempt)."""
        dashboards = _make_dashboards_with_destination({"id": "dst-old"})
        resp = MagicMock()
        resp.status = 500
        resp.message = "Internal Server Error"
        err = CustomClientHTTPError(resp, message="boom")
        dashboards.config.destination_client.put = AsyncMock(side_effect=err)
        dashboards.config.destination_client.post = AsyncMock()

        with pytest.raises(CustomClientHTTPError):
            asyncio.run(dashboards.update_resource("abc-123", {"title": "src"}))
        dashboards.config.destination_client.post.assert_not_awaited()


class TestDashboardsConnectResourcesDrop:
    """connect_resources drop/keep/hard-fail for dashboards' flat `restricted_roles`."""

    def _make_dashboard(self, drop=False, skip_failed=False):
        config = MagicMock()
        config.state = MagicMock()
        config.state.source = defaultdict(dict)
        config.state.destination = defaultdict(dict)
        config.state.ensure_resource_loaded = MagicMock()
        config.drop_unresolvable_principals = drop
        config.skip_failed_resource_connections = skip_failed
        config.counter = Counter()
        config.logger = MagicMock()
        return Dashboards(config)

    def _seed_valid_role(self, d, src="role-good", dst="role-good-dst"):
        d.config.state.destination["roles"][src] = {"id": dst}

    def test_flag_off_stale_role_hard_fails(self):
        d = self._make_dashboard(drop=False)
        resource = {"id": "abc-def-ghi", "restricted_roles": ["role-gone"]}
        with pytest.raises(ResourceConnectionError):
            d.connect_resources("abc-def-ghi", resource)
        assert resource["restricted_roles"] == ["role-gone"]  # not dropped when flag off

    def test_flag_on_drops_stale_keeps_valid(self):
        d = self._make_dashboard(drop=True)
        self._seed_valid_role(d)
        resource = {"id": "abc-def-ghi", "restricted_roles": ["role-good", "role-gone"]}
        d.connect_resources("abc-def-ghi", resource)  # no raise
        assert resource["restricted_roles"] == ["role-good-dst"]
        assert d.config.counter.stale_principals_dropped_by_type["dashboards"] == ["abc-def-ghi"]

    def test_flag_on_all_stale_empty_list_raises_risk(self):
        d = self._make_dashboard(drop=True)
        resource = {"id": "abc-def-ghi", "restricted_roles": ["role-gone"]}
        with pytest.raises(ResourceConnectionError) as exc_info:
            d.connect_resources("abc-def-ghi", resource)
        assert exc_info.value.empty_binding_risk is True
        assert resource["restricted_roles"] == []

    def test_empty_list_risk_is_returned_when_connection_failure_is_suppressed(self):
        d = self._make_dashboard(drop=True, skip_failed=True)
        resource = {"id": "abc-def-ghi", "restricted_roles": ["role-gone"]}

        assert d.connect_resources("abc-def-ghi", resource).empty_binding_escalation is True
        assert resource["restricted_roles"] == []

    def test_flag_on_middle_drop_keeps_neighbors(self):
        d = self._make_dashboard(drop=True)
        self._seed_valid_role(d, src="ra", dst="ra-dst")
        self._seed_valid_role(d, src="rc", dst="rc-dst")
        resource = {"id": "abc", "restricted_roles": ["ra", "role-gone", "rc"]}
        d.connect_resources("abc", resource)  # no raise
        assert resource["restricted_roles"] == ["ra-dst", "rc-dst"]

    def test_no_restricted_roles_is_noop(self):
        d = self._make_dashboard(drop=True)
        resource = {"id": "abc"}  # no restricted_roles at all
        d.connect_resources("abc", resource)  # no raise, nothing to do
