# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import asyncio
from unittest.mock import AsyncMock, patch

from datadog_sync.model.logs_pipelines import LogsPipelines


def _make_integration_resource(name="nginx"):
    return {
        "id": f"src-{name}",
        "name": name,
        "is_read_only": True,
        "filter": {"query": f"source:{name}"},
        "processors": [],
    }


def _make_dest_pipeline(name="nginx"):
    return {
        "id": f"dest-{name}",
        "name": name,
        "is_read_only": True,
        "processors": [],
        "is_enabled": True,
    }


# ── R/G 1: override URL used when destination_logs_intake_url is set ──────────


def test_override_url_used_when_set(mock_config):
    override_url = "https://evp.example/v2/track/logs/org/42"
    mock_config.destination_logs_intake_url = override_url

    lp = LogsPipelines(mock_config)
    lp.destination_integration_pipelines = {}

    dest_pipeline = _make_dest_pipeline()
    mock_config.destination_client.get = AsyncMock(return_value=[dest_pipeline])
    mock_config.destination_client.post_unauthenticated = AsyncMock()
    mock_config.destination_client.post = AsyncMock()
    mock_config.destination_client.put = AsyncMock(return_value=_make_dest_pipeline())

    resource = _make_integration_resource()

    with patch("datadog_sync.model.logs_pipelines.sleep", new_callable=AsyncMock):
        asyncio.run(lp.create_resource("src-nginx", resource))

    mock_config.destination_client.post_unauthenticated.assert_awaited_once()
    call_args = mock_config.destination_client.post_unauthenticated.call_args
    assert call_args[0][0] == override_url
    assert call_args[0][1]["ddsource"] == "nginx"

    # The subdomain path must NOT be used
    for call in mock_config.destination_client.post.call_args_list:
        assert call[0][0] != lp.logs_intake_path, "subdomain POST path must not be called when override is set"


# ── R/G 2: subdomain path used when no override ───────────────────────────────


def test_subdomain_path_used_when_no_override(mock_config):
    mock_config.destination_logs_intake_url = None
    mock_config.destination_client.url_object.subdomain = "api"

    lp = LogsPipelines(mock_config)
    lp.destination_integration_pipelines = {}

    dest_pipeline = _make_dest_pipeline()
    mock_config.destination_client.get = AsyncMock(return_value=[dest_pipeline])
    mock_config.destination_client.post = AsyncMock()
    mock_config.destination_client.post_unauthenticated = AsyncMock()
    mock_config.destination_client.put = AsyncMock(return_value=_make_dest_pipeline())

    resource = _make_integration_resource()

    with patch("datadog_sync.model.logs_pipelines.sleep", new_callable=AsyncMock):
        asyncio.run(lp.create_resource("src-nginx", resource))

    mock_config.destination_client.post_unauthenticated.assert_not_awaited()
    intake_calls = [c for c in mock_config.destination_client.post.call_args_list if c[0][0] == lp.logs_intake_path]
    assert len(intake_calls) == 1
    assert intake_calls[0][1]["subdomain"] == "http-intake.logs"


# ── R/G 3: custom pipeline (is_read_only=False) unaffected by override ────────


def test_custom_pipeline_uses_base_post_regardless_of_override(mock_config):
    mock_config.destination_logs_intake_url = "https://evp.example/v2/track/logs/org/42"

    lp = LogsPipelines(mock_config)

    custom_resource = {
        "id": "src-custom",
        "name": "My Custom Pipeline",
        "is_read_only": False,
        "processors": [],
    }
    mock_config.destination_client.post = AsyncMock(return_value=custom_resource)
    mock_config.destination_client.post_unauthenticated = AsyncMock()

    asyncio.run(lp.create_resource("src-custom", custom_resource))

    mock_config.destination_client.post_unauthenticated.assert_not_awaited()
    mock_config.destination_client.post.assert_awaited_once_with(lp.resource_config.base_path, custom_resource)


# ── G/G 1: pipeline already exists → no intake POST ──────────────────────────


def test_pipeline_already_exists_no_intake_post(mock_config):
    mock_config.destination_logs_intake_url = None

    lp = LogsPipelines(mock_config)
    dest_pipeline = _make_dest_pipeline()
    lp.destination_integration_pipelines = {"nginx": dest_pipeline}

    mock_config.destination_client.post = AsyncMock()
    mock_config.destination_client.post_unauthenticated = AsyncMock()
    mock_config.destination_client.put = AsyncMock(return_value=_make_dest_pipeline())
    mock_config.state.destination["logs_pipelines"]["src-nginx"] = dest_pipeline

    resource = _make_integration_resource()
    asyncio.run(lp.create_resource("src-nginx", resource))

    mock_config.destination_client.post.assert_not_awaited()
    mock_config.destination_client.post_unauthenticated.assert_not_awaited()


# ── G/G 2: invalid pipeline name → __datadog_sync_invalid, no intake POST ────


def test_invalid_pipeline_no_intake_post(mock_config):
    mock_config.destination_logs_intake_url = "https://evp.example/v2/track/logs/org/42"

    lp = LogsPipelines(mock_config)
    lp.destination_integration_pipelines = {}

    mock_config.destination_client.post_unauthenticated = AsyncMock()
    mock_config.destination_client.post = AsyncMock()

    resource = _make_integration_resource(name="cron")
    _, result = asyncio.run(lp.create_resource("src-cron", resource))

    assert result.get("__datadog_sync_invalid") is True
    mock_config.destination_client.post_unauthenticated.assert_not_awaited()
    mock_config.destination_client.post.assert_not_awaited()


# ── G/G 3: existing subdomain construction unchanged ─────────────────────────


def test_subdomain_construction_api_root(mock_config):
    """'api' subdomain → 'http-intake.logs'"""
    mock_config.destination_logs_intake_url = None
    mock_config.destination_client.url_object.subdomain = "api"

    lp = LogsPipelines(mock_config)
    lp.destination_integration_pipelines = {}

    dest_pipeline = _make_dest_pipeline()
    mock_config.destination_client.get = AsyncMock(return_value=[dest_pipeline])
    mock_config.destination_client.post = AsyncMock()
    mock_config.destination_client.put = AsyncMock(return_value=_make_dest_pipeline())

    with patch("datadog_sync.model.logs_pipelines.sleep", new_callable=AsyncMock):
        asyncio.run(lp.create_resource("src-nginx", _make_integration_resource()))

    intake_call = next(c for c in mock_config.destination_client.post.call_args_list if c[0][0] == lp.logs_intake_path)
    assert intake_call[1]["subdomain"] == "http-intake.logs"


def test_subdomain_construction_api_dot_prefix(mock_config):
    """'api.datadoghq.com' subdomain → 'http-intake.logs.datadoghq.com'"""
    mock_config.destination_logs_intake_url = None
    mock_config.destination_client.url_object.subdomain = "api.datadoghq.com"

    lp = LogsPipelines(mock_config)
    lp.destination_integration_pipelines = {}

    dest_pipeline = _make_dest_pipeline()
    mock_config.destination_client.get = AsyncMock(return_value=[dest_pipeline])
    mock_config.destination_client.post = AsyncMock()
    mock_config.destination_client.put = AsyncMock(return_value=_make_dest_pipeline())

    with patch("datadog_sync.model.logs_pipelines.sleep", new_callable=AsyncMock):
        asyncio.run(lp.create_resource("src-nginx", _make_integration_resource()))

    intake_call = next(c for c in mock_config.destination_client.post.call_args_list if c[0][0] == lp.logs_intake_path)
    assert intake_call[1]["subdomain"] == "http-intake.logs.datadoghq.com"


# ── T7: integration pipeline diverges from destination → skip update, warn ────
#
# Motivation: `PUT /api/v1/logs/config/pipelines/<id>` does not route
# integration-pipeline (read-only) IDs. Prior behaviour called update_resource
# whenever check_diff detected any difference against the destination's
# integration pipeline, producing an infinite loop of 404-HTML failures every
# dispatch. The new behaviour keeps the state mapping, emits a WARN with a
# diff summary, and fires a distinct action-metric so operators can dashboard
# the class.


def test_integration_pipeline_with_diff_skips_update_and_warns(mock_config, caplog):
    """Source diverges from destination on `is_enabled`; PUT must NOT fire."""
    from datadog_sync.model.logs_pipelines import LogsPipelines

    caplog.set_level("WARNING", logger="datadog_sync_cli")
    mock_config.destination_logs_intake_url = None

    lp = LogsPipelines(mock_config)
    # Destination has 'nginx' but with a different is_enabled value.
    dest_pipeline = _make_dest_pipeline(name="nginx")
    dest_pipeline["is_enabled"] = False
    lp.destination_integration_pipelines = {"nginx": dest_pipeline}

    mock_config.destination_client.post = AsyncMock()
    mock_config.destination_client.post_unauthenticated = AsyncMock()
    mock_config.destination_client.put = AsyncMock(return_value=_make_dest_pipeline())
    mock_config.destination_client.send_metric = AsyncMock()

    resource = _make_integration_resource(name="nginx")
    resource["is_enabled"] = True  # forces a diff
    asyncio.run(lp.create_resource("src-nginx", resource))

    # PUT / update_resource must NOT be called — the endpoint doesn't route
    # integration-pipeline IDs and would 404.
    mock_config.destination_client.put.assert_not_awaited()

    # WARN log must contain enough info to diagnose the divergence.
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    warn_msgs = [r.getMessage() for r in warnings]
    assert any("integration pipeline 'nginx'" in m for m in warn_msgs)
    assert any("src-nginx" in m for m in warn_msgs)
    assert any("is_enabled" in m for m in warn_msgs), warn_msgs

    # Distinct metric fires so the class is dashboardable.
    mock_config.destination_client.send_metric.assert_awaited()
    metric_call = mock_config.destination_client.send_metric.call_args
    tags = metric_call.kwargs.get("tags") or metric_call.args[1]
    assert "action_sub_type:integration_diff_skipped" in tags
    assert "resource_type:logs_pipelines" in tags
    assert "pipeline_name:nginx" in tags


def test_integration_pipeline_with_no_diff_skips_update_and_no_warn(mock_config, caplog):
    """Source == destination integration pipeline; no PUT, no WARN, no metric."""
    from datadog_sync.model.logs_pipelines import LogsPipelines

    caplog.set_level("WARNING", logger="datadog_sync_cli")
    mock_config.destination_logs_intake_url = None

    lp = LogsPipelines(mock_config)
    # Construct destination + source with identical shape so check_diff returns empty.
    identical_shape = {
        "id": "dest-nginx",
        "name": "nginx",
        "is_read_only": True,
        "is_enabled": True,
        "filter": {"query": "source:nginx"},
        "processors": [],
    }
    lp.destination_integration_pipelines = {"nginx": dict(identical_shape)}

    mock_config.destination_client.post = AsyncMock()
    mock_config.destination_client.post_unauthenticated = AsyncMock()
    mock_config.destination_client.put = AsyncMock(return_value=dict(identical_shape))
    mock_config.destination_client.send_metric = AsyncMock()

    resource = dict(identical_shape)
    resource["id"] = "src-nginx"  # source has different id; excluded_attributes drops it from diff
    asyncio.run(lp.create_resource("src-nginx", resource))

    mock_config.destination_client.put.assert_not_awaited()
    integ_warnings = [
        r for r in caplog.records
        if r.levelname == "WARNING" and "integration pipeline" in r.getMessage()
    ]
    assert integ_warnings == []
    mock_config.destination_client.send_metric.assert_not_awaited()


def test_summarize_diff_keys_extracts_top_level_fields():
    """The helper returns de-duplicated top-level field names from a DeepDiff."""
    from datadog_sync.model.logs_pipelines import _summarize_diff_keys

    fake_diff = {
        "values_changed": {
            "root['is_enabled']": {"old_value": False, "new_value": True},
            "root['filter']['query']": {"old_value": "a", "new_value": "b"},
        },
        "dictionary_item_added": {"root['tags']": ["new"]},
    }
    keys = _summarize_diff_keys(fake_diff)
    assert set(keys) == {"is_enabled", "filter", "tags"}


def test_summarize_diff_keys_empty_diff_returns_empty_list():
    from datadog_sync.model.logs_pipelines import _summarize_diff_keys

    assert _summarize_diff_keys({}) == []
    assert _summarize_diff_keys(None) == []


def test_custom_writable_pipeline_still_hits_update_path(mock_config):
    """Regression guard: is_read_only=False pipelines must NOT be affected by
    the T7 skip; they still go through the direct POST at line 65 (or PUT via
    update_resource when the caller dispatches an update)."""
    from datadog_sync.model.logs_pipelines import LogsPipelines

    mock_config.destination_logs_intake_url = None

    lp = LogsPipelines(mock_config)
    lp.destination_integration_pipelines = {}  # no integration cache needed for writable path

    mock_config.destination_client.post = AsyncMock(return_value={"id": "dest-custom", "is_read_only": False})

    resource = {
        "id": "src-custom",
        "name": "BIT_custom_pipeline",
        "is_read_only": False,
        "filter": {"query": "service:foo"},
        "processors": [],
    }
    asyncio.run(lp.create_resource("src-custom", resource))

    # Direct POST at line 66 fires; no branch into read-only handling.
    mock_config.destination_client.post.assert_awaited_once()


# ── T7: subsequent-sync path must also skip the futile PUT ────────────────────
#
# Motivation: PR #604 first-pass only guarded create_resource. But after the
# first sync, the source id is present in state.destination[type], so
# ResourcesHandler._apply_resource_cb dispatches directly to
# _update_resource (which wraps update_resource). Without a guard there, the
# second sync fires the same 404-generating PUT. Guard both entry points.


def test_update_read_only_pipeline_raises_skip_resource(mock_config, caplog):
    """update_resource on a read-only destination pipeline must raise
    SkipResource (so handler counts as skipped, not success) and must NOT
    issue any PUT."""
    from datadog_sync.utils.resource_utils import SkipResource
    from datadog_sync.model.logs_pipelines import LogsPipelines

    caplog.set_level("WARNING", logger="datadog_sync_cli")

    lp = LogsPipelines(mock_config)

    # Destination state already has the read-only pipeline from a prior sync.
    dest_state = {
        "id": "dest-nginx",
        "name": "nginx",
        "is_read_only": True,
        "is_enabled": True,
        "filter": {"query": "source:nginx"},
        "processors": [],
    }
    mock_config.state.destination["logs_pipelines"]["src-nginx"] = dest_state

    mock_config.destination_client.put = AsyncMock()
    mock_config.destination_client.send_metric = AsyncMock()

    resource = dict(dest_state)
    resource["id"] = "src-nginx"
    resource["is_enabled"] = False  # forces a diff

    raised = False
    try:
        asyncio.run(lp.update_resource("src-nginx", resource))
    except SkipResource as e:
        raised = True
        assert "integration pipeline" in str(e).lower()
    assert raised, "update_resource must raise SkipResource for read-only pipelines"

    # No PUT to the unsupported endpoint.
    mock_config.destination_client.put.assert_not_awaited()

    # Divergence still logged + metric fires.
    warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("integration pipeline 'nginx'" in m for m in warn_msgs)
    mock_config.destination_client.send_metric.assert_awaited()


def test_update_read_only_pipeline_no_diff_still_raises_skip(mock_config, caplog):
    """Even when source == destination (no diff), update_resource must skip
    rather than PUT. The handler shouldn't dispatch to update in that case,
    but the guard should be defensive."""
    from datadog_sync.utils.resource_utils import SkipResource
    from datadog_sync.model.logs_pipelines import LogsPipelines

    caplog.set_level("WARNING", logger="datadog_sync_cli")

    lp = LogsPipelines(mock_config)

    dest_state = {
        "id": "dest-nginx",
        "name": "nginx",
        "is_read_only": True,
        "is_enabled": True,
        "filter": {"query": "source:nginx"},
        "processors": [],
    }
    mock_config.state.destination["logs_pipelines"]["src-nginx"] = dest_state

    mock_config.destination_client.put = AsyncMock()
    mock_config.destination_client.send_metric = AsyncMock()

    resource = dict(dest_state)
    resource["id"] = "src-nginx"  # same shape → no diff after excluded_attributes

    raised = False
    try:
        asyncio.run(lp.update_resource("src-nginx", resource))
    except SkipResource:
        raised = True
    assert raised

    mock_config.destination_client.put.assert_not_awaited()
    # No diff → no divergence log or metric (guard fires silently).
    integ_warnings = [
        r for r in caplog.records
        if r.levelname == "WARNING" and "integration pipeline" in r.getMessage()
    ]
    assert integ_warnings == []
    mock_config.destination_client.send_metric.assert_not_awaited()


def test_update_writable_pipeline_still_puts(mock_config):
    """Regression guard: writable (is_read_only=False) pipelines must still
    hit the PUT path in update_resource."""
    from datadog_sync.model.logs_pipelines import LogsPipelines

    lp = LogsPipelines(mock_config)

    dest_state = {
        "id": "dest-custom",
        "name": "BIT_custom",
        "is_read_only": False,
        "filter": {"query": "service:foo"},
        "processors": [],
    }
    mock_config.state.destination["logs_pipelines"]["src-custom"] = dest_state

    mock_config.destination_client.put = AsyncMock(
        return_value={"id": "dest-custom", "is_read_only": False, "name": "BIT_custom"}
    )

    resource = dict(dest_state)
    resource["id"] = "src-custom"

    asyncio.run(lp.update_resource("src-custom", resource))
    mock_config.destination_client.put.assert_awaited_once()


def test_update_invalid_pipeline_short_circuit_unchanged(mock_config):
    """Regression guard: the pre-existing __datadog_sync_invalid short-circuit
    at the top of update_resource still fires ahead of the new read-only
    guard."""
    from datadog_sync.model.logs_pipelines import LogsPipelines

    lp = LogsPipelines(mock_config)

    # Invalid pipeline sentinel is set by the create-path for names in
    # invalid_integration_pipelines (e.g. 'cron').
    dest_state = {
        "id": "dest-cron",
        "name": "cron",
        "is_read_only": True,
        "__datadog_sync_invalid": True,
    }
    mock_config.state.destination["logs_pipelines"]["src-cron"] = dest_state

    mock_config.destination_client.put = AsyncMock()

    resource = {"id": "src-cron", "name": "cron", "is_read_only": True, "processors": []}

    # Should NOT raise SkipResource — the invalid short-circuit at line 195
    # returns cleanly with the merged local state.
    _id, result = asyncio.run(lp.update_resource("src-cron", resource))
    assert _id == "src-cron"
    assert result["__datadog_sync_invalid"] is True
    mock_config.destination_client.put.assert_not_awaited()
