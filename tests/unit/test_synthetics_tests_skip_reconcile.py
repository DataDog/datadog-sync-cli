# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""synthetics_tests: regression guard + framework safety-net proof.

The exists-path writes state.destination from the map entry (keyed by
metadata.disaster_recovery.source_public_id) then delegates to update_resource.
The framework is a pure no-op for it; the safety-net test proves the framework
WOULD reconcile this resource if its exists-path regressed. Internal helpers
(_replicate_files, _update_test, _replace_variable_public_id) are stubbed to
keep the regression guard focused on the state-destination write contract.

All identifiers are obviously synthetic (``src-pub-id``, ``dst-pub-id``).
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.synthetics_tests import SyntheticsTests
from datadog_sync.utils.resource_utils import SkipResource


def _test(public_id, source_public_id=None, test_type="browser"):
    return {
        "public_id": public_id,
        "type": test_type,
        "name": f"test-{public_id}",
        "metadata": {"disaster_recovery": {"source_public_id": source_public_id or public_id}},
    }


def _make_synthetics_tests(existing_map=None, destination_client=None):
    config = MagicMock()
    config.destination_client = destination_client or MagicMock()
    config.state = MagicMock()
    config.state.source = defaultdict(dict)
    config.state.destination = defaultdict(dict)
    config.logger = MagicMock()
    st = SyntheticsTests(config=config)
    st._existing_resources_map = existing_map or {}
    # Stub network/complex internals so the regression guard stays focused.
    st._replicate_files = AsyncMock()
    st._replace_variable_public_id = MagicMock(return_value=False)
    return st, config


class TestSyntheticsTestsSkipReconcile:
    def test_existing_resource_create_writes_state_destination(self):
        # _id is "<source_public_id>#<rest>"; map keyed by source_public_id.
        _id = "src-pub-id#config-1"
        dest_test = _test("dst-pub-id", source_public_id="src-pub-id")
        updated = _test("dst-pub-id", source_public_id="src-pub-id")
        updated["marker"] = "updated"
        destination_client = MagicMock()
        st, config = _make_synthetics_tests(
            existing_map={"src-pub-id": dest_test},
            destination_client=destination_client,
        )
        st._update_test = AsyncMock(return_value=updated)
        resource = _test("src-pub-id", source_public_id="src-pub-id")
        resource["name"] = "test-src-pub-id"

        asyncio.run(st._create_resource(_id, resource))

        st._update_test.assert_called_once()
        assert config.state.destination["synthetics_tests"][_id] == updated

    def test_framework_reconciles_if_create_raised_skip(self):
        _id = "src-pub-id#config-1"
        dest_test = _test("dst-pub-id", source_public_id="src-pub-id")
        st, config = _make_synthetics_tests(existing_map={"src-pub-id": dest_test})
        st.create_resource = AsyncMock(side_effect=SkipResource(_id, "synthetics_tests", "exists"))
        resource = _test("src-pub-id", source_public_id="src-pub-id")

        with pytest.raises(SkipResource):
            asyncio.run(st._create_resource(_id, resource))

        assert config.state.destination["synthetics_tests"][_id] == dest_test

    def test_create_non_existing_still_posts(self):
        _id = "src-pub-id#config-1"
        posted = _test("dst-pub-id", source_public_id="src-pub-id")
        destination_client = MagicMock()
        st, config = _make_synthetics_tests(
            existing_map={},
            destination_client=destination_client,
        )
        st._create_test = AsyncMock(return_value=posted)
        resource = _test("src-pub-id", source_public_id="src-pub-id")
        resource["name"] = "test-src-pub-id"

        out_id, out_r = asyncio.run(st.create_resource(_id, resource))

        st._create_test.assert_called_once()
        assert out_id == _id
        assert out_r == posted
