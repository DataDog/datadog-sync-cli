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
