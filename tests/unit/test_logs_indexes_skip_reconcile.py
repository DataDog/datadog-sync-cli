# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""logs_indexes: regression guard + framework safety-net proof.

logs_indexes' exists-path is already correct (write state.destination, delegate to
update_resource PUT). The framework is a pure no-op for it; the safety-net test
proves the framework WOULD reconcile logs_indexes if its exists-path regressed.
logs_indexes keys _existing_resources_map by name, and import_resource returns
resource["name"] as the source id, so source _id == name == map key.

All identifiers are obviously synthetic (``index-test``).
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.logs_indexes import LogsIndexes
from datadog_sync.utils.resource_utils import SkipResource


def _index(name):
    return {
        "name": name,
        "daily_limit": None,
    }


def _make_logs_indexes(existing_map=None, destination_client=None):
    config = MagicMock()
    config.destination_client = destination_client or MagicMock()
    config.state = MagicMock()
    config.state.source = defaultdict(dict)
    config.state.destination = defaultdict(dict)
    config.logger = MagicMock()
    li = LogsIndexes(config=config)
    li._existing_resources_map = existing_map or {}
    return li, config


class TestLogsIndexesSkipReconcile:
    def test_existing_resource_create_writes_state_destination(self):
        # source _id == name == map key.
        _id = "index-test"
        dest = _index("index-test")
        put_resp = {"name": "index-test", "daily_limit": 42}
        destination_client = MagicMock()
        destination_client.put = AsyncMock(return_value=put_resp)
        li, config = _make_logs_indexes(
            existing_map={"index-test": dest},
            destination_client=destination_client,
        )
        resource = _index("index-test")

        asyncio.run(li._create_resource(_id, resource))

        destination_client.put.assert_called_once()
        # update_resource does state.destination[_id].update(resp); wrapper rewrites.
        assert config.state.destination["logs_indexes"][_id]["daily_limit"] == 42

    def test_framework_reconciles_if_create_raised_skip(self):
        _id = "index-test"
        dest = _index("index-test")
        li, config = _make_logs_indexes(existing_map={"index-test": dest})
        li.create_resource = AsyncMock(side_effect=SkipResource(_id, "logs_indexes", "exists"))
        resource = _index("index-test")

        with pytest.raises(SkipResource):
            asyncio.run(li._create_resource(_id, resource))

        assert config.state.destination["logs_indexes"][_id] == dest

    def test_create_non_existing_still_posts(self):
        _id = "index-test"
        posted = _index("index-test")
        destination_client = MagicMock()
        destination_client.post = AsyncMock(return_value=posted)
        li, config = _make_logs_indexes(
            existing_map={},
            destination_client=destination_client,
        )
        resource = _index("index-test")

        out_id, out_r = asyncio.run(li.create_resource(_id, resource))

        destination_client.post.assert_called_once()
        assert out_id == _id
        assert out_r == posted
