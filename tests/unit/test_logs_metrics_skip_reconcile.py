# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""logs_metrics: regression guard + framework safety-net proof.

logs_metrics' exists-path is already correct (write state.destination, delegate to
update_resource PATCH). The framework is a pure no-op for it; the safety-net test
proves the framework WOULD reconcile logs_metrics if its exists-path regressed.

All identifiers are obviously synthetic (``metric-src``, ``metric-dst``).
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.logs_metrics import LogsMetrics
from datadog_sync.utils.resource_utils import SkipResource


def _metric(metric_id, name=None):
    return {
        "id": metric_id,
        "type": "logs_metrics",
        "attributes": {"name": name or metric_id},
    }


def _make_logs_metrics(existing_map=None, destination_client=None):
    config = MagicMock()
    config.destination_client = destination_client or MagicMock()
    config.state = MagicMock()
    config.state.source = defaultdict(dict)
    config.state.destination = defaultdict(dict)
    config.logger = MagicMock()
    lm = LogsMetrics(config=config)
    lm._existing_resources_map = existing_map or {}
    return lm, config


class TestLogsMetricsSkipReconcile:
    def test_existing_resource_create_writes_state_destination(self):
        # logs_metrics ids are stable across orgs, so source _id == map key.
        _id = "metric-test"
        dest = _metric("metric-test")
        patched = _metric("metric-test")
        patched["attributes"]["marker"] = "patched"
        destination_client = MagicMock()
        destination_client.patch = AsyncMock(return_value={"data": patched})
        lm, config = _make_logs_metrics(
            existing_map={"metric-test": dest},
            destination_client=destination_client,
        )
        resource = _metric("metric-test")

        asyncio.run(lm._create_resource(_id, resource))

        destination_client.patch.assert_called_once()
        assert config.state.destination["logs_metrics"][_id] == patched

    def test_framework_reconciles_if_create_raised_skip(self):
        _id = "metric-test"
        dest = _metric("metric-test")
        lm, config = _make_logs_metrics(existing_map={"metric-test": dest})
        lm.create_resource = AsyncMock(side_effect=SkipResource(_id, "logs_metrics", "exists"))
        resource = _metric("metric-test")

        with pytest.raises(SkipResource):
            asyncio.run(lm._create_resource(_id, resource))

        assert config.state.destination["logs_metrics"][_id] == dest

    def test_create_non_existing_still_posts(self):
        _id = "metric-test"
        posted = _metric("metric-test")
        destination_client = MagicMock()
        destination_client.post = AsyncMock(return_value={"data": posted})
        lm, config = _make_logs_metrics(
            existing_map={},
            destination_client=destination_client,
        )
        resource = _metric("metric-test")

        out_id, out_r = asyncio.run(lm.create_resource(_id, resource))

        destination_client.post.assert_called_once()
        assert out_id == _id
        assert out_r == posted
