# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for the RUMConfig resource model.

RUM config is a singleton org setting (no DELETE, no per-id path). Only
``enforced_application_tags`` is configurable; all other attributes are
server-managed and excluded from diffs. ``create_resource`` checks whether the
destination singleton already exists and delegates to ``update_resource`` if so
(mirroring ``logs_archives_order``); ``delete_resource`` is a no-op.
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.rum_config import RUMConfig


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _cfg(enforced=True):
    return {
        "id": "rum-config",
        "type": "rum_config",
        "attributes": {
            "enforced_application_tags": enforced,
            "disabled": False,
            "retention_filters_enabled": True,
            "ootb_metrics_version": 2,
        },
    }


def test_get_resources_returns_single_element_list():
    cfg = RUMConfig(MagicMock())
    client = AsyncMock()
    client.get = AsyncMock(return_value={"data": _cfg(True)})

    resources = _run(cfg.get_resources(client))

    assert len(resources) == 1
    assert resources[0]["attributes"]["enforced_application_tags"] is True
    client.get.assert_awaited_once_with("/api/v2/rum/config")


def test_import_resource_returns_default_id():
    cfg = RUMConfig(MagicMock())
    cfg.config.source_client = AsyncMock()
    resource = _cfg(True)
    _id, data = _run(cfg.import_resource(resource=resource))
    assert _id == "rum-config"
    assert data is resource


def test_create_resource_posts_when_destination_absent():
    cfg = RUMConfig(MagicMock())
    dest = AsyncMock()
    dest.get = AsyncMock(side_effect=Exception("404 not found"))
    dest.post = AsyncMock(return_value={"data": _cfg(True)})
    cfg.config.destination_client = dest

    resource = _cfg(True)
    _id, data = _run(cfg.create_resource("rum-config", resource))

    assert _id == "rum-config"
    dest.post.assert_awaited_once()
    post_url, post_payload = dest.post.await_args.args
    assert post_url == "/api/v2/rum/config"
    # only enforced_application_tags is sent on create
    assert post_payload == {"data": {"type": "rum_config", "attributes": {"enforced_application_tags": True}}}


def test_create_resource_delegates_to_update_when_destination_exists():
    cfg = RUMConfig(MagicMock())
    dest = AsyncMock()
    dest.get = AsyncMock(return_value={"data": _cfg(False)})
    dest.patch = AsyncMock(return_value={"data": _cfg(True)})
    dest.post = AsyncMock()
    cfg.config.destination_client = dest
    cfg.config.state = MagicMock()
    cfg.config.state.destination = defaultdict(dict)

    resource = _cfg(True)
    _id, data = _run(cfg.create_resource("rum-config", resource))

    assert _id == "rum-config"
    dest.post.assert_not_awaited()
    dest.patch.assert_awaited_once()
    # state.destination hydrated with the existing dest config so update can proceed
    assert cfg.config.state.destination["rum_config"]["rum-config"]["attributes"]["enforced_application_tags"] is False


def test_update_resource_patches_enforced_application_tags_only():
    cfg = RUMConfig(MagicMock())
    dest = AsyncMock()
    dest.patch = AsyncMock(return_value={"data": _cfg(True)})
    cfg.config.destination_client = dest

    resource = _cfg(True)
    _id, data = _run(cfg.update_resource("rum-config", resource))

    assert _id == "rum-config"
    dest.patch.assert_awaited_once()
    patch_url, patch_payload = dest.patch.await_args.args
    assert patch_url == "/api/v2/rum/config"
    assert patch_payload == {"data": {"type": "rum_config", "attributes": {"enforced_application_tags": True}}}


def test_delete_resource_is_noop():
    cfg = RUMConfig(MagicMock())
    cfg.config.destination_client = AsyncMock()
    cfg.config.logger = MagicMock()
    _run(cfg.delete_resource("rum-config"))
    cfg.config.destination_client.delete.assert_not_awaited()
