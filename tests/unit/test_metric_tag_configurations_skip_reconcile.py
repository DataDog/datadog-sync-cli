# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""metric_tag_configurations: regression guard, non-existence guard, safety-net proof.

Two SkipResource classes here must NOT trigger a destination-state write:
  (a) "Metric not present on destination" (create_resource POST 400, or
      update_resource PATCH 400) -- the metric genuinely does not exist at the
      destination, the id is NOT in _existing_resources_map -> framework writes
      nothing (no false positive);
  (b) the exists-path (id in map) writes state.destination then delegates to
      update_resource -- already correct, framework no-ops.

All identifiers are obviously synthetic (``mtc-test``).
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.metric_tag_configurations import MetricTagConfigurations
from datadog_sync.utils.resource_utils import CustomClientHTTPError, SkipResource


class _FakeResponse:
    def __init__(self, status, message="error"):
        self.status = status
        self.message = message


def _mtc(metric_id):
    return {
        "id": metric_id,
        "type": "manage_tags",
        "attributes": {"tags": ["tag:src"]},
    }


def _make_mtc(existing_map=None, destination_client=None):
    config = MagicMock()
    config.destination_client = destination_client or MagicMock()
    config.state = MagicMock()
    config.state.source = defaultdict(dict)
    config.state.destination = defaultdict(dict)
    config.logger = MagicMock()
    mtc = MetricTagConfigurations(config=config)
    mtc._existing_resources_map = existing_map or {}
    return mtc, config


class TestMetricTagConfigurationsSkipReconcile:
    def test_existing_resource_create_writes_state_destination(self):
        # id in map -> write state.destination, delegate to update_resource (PATCH).
        _id = "mtc-test"
        dest = _mtc("mtc-test")
        patched = _mtc("mtc-test")
        patched["attributes"]["marker"] = "patched"
        destination_client = MagicMock()
        destination_client.patch = AsyncMock(return_value={"data": patched})
        mtc, config = _make_mtc(
            existing_map={"mtc-test": dest},
            destination_client=destination_client,
        )
        resource = _mtc("mtc-test")

        asyncio.run(mtc._create_resource(_id, resource))

        destination_client.patch.assert_called_once()
        assert config.state.destination["metric_tag_configurations"][_id] == patched

    def test_metric_not_present_skip_does_not_write(self):
        # id NOT in map -> POST -> 400 "metric that does not exist" -> SkipResource.
        # Framework must NOT write (genuine non-existence; no false positive).
        _id = "mtc-test"
        err = CustomClientHTTPError(_FakeResponse(400), "metric that does not exist")
        destination_client = MagicMock()
        destination_client.post = AsyncMock(side_effect=err)
        mtc, config = _make_mtc(
            existing_map={},
            destination_client=destination_client,
        )
        config.state.source["metric_tag_configurations"][_id] = _mtc("mtc-test")
        resource = _mtc("mtc-test")

        with pytest.raises(SkipResource, match="Metric not present"):
            asyncio.run(mtc._create_resource(_id, resource))

        assert config.state.destination["metric_tag_configurations"] == {}

    def test_update_metric_not_present_skip_preserves_state_destination(self):
        # update_resource PATCH 400 missing metric -> SkipResource. state.destination
        # was already written by the exists-path; insert-if-absent must preserve it.
        _id = "mtc-test"
        dest = _mtc("mtc-test")
        err = CustomClientHTTPError(_FakeResponse(400), "metric that does not exist")
        destination_client = MagicMock()
        destination_client.patch = AsyncMock(side_effect=err)
        mtc, config = _make_mtc(
            existing_map={"mtc-test": dest},
            destination_client=destination_client,
        )
        config.state.destination["metric_tag_configurations"][_id] = dest
        resource = _mtc("mtc-test")

        with pytest.raises(SkipResource, match="Metric not present"):
            asyncio.run(mtc._update_resource(_id, resource))

        assert config.state.destination["metric_tag_configurations"][_id] is dest

    def test_framework_reconciles_if_create_raised_skip(self):
        _id = "mtc-test"
        dest = _mtc("mtc-test")
        mtc, config = _make_mtc(existing_map={"mtc-test": dest})
        mtc.create_resource = AsyncMock(side_effect=SkipResource(_id, "metric_tag_configurations", "exists"))
        resource = _mtc("mtc-test")

        with pytest.raises(SkipResource):
            asyncio.run(mtc._create_resource(_id, resource))

        assert config.state.destination["metric_tag_configurations"][_id] == dest

    def test_create_non_existing_still_posts(self):
        _id = "mtc-test"
        posted = _mtc("mtc-test")
        destination_client = MagicMock()
        destination_client.post = AsyncMock(return_value={"data": posted})
        mtc, config = _make_mtc(
            existing_map={},
            destination_client=destination_client,
        )
        config.state.source["metric_tag_configurations"][_id] = _mtc("mtc-test")
        resource = _mtc("mtc-test")

        out_id, out_r = asyncio.run(mtc.create_resource(_id, resource))

        destination_client.post.assert_called_once()
        assert out_id == _id
        assert out_r == posted
