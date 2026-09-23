# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""synthetics_global_variables: regression guard + framework safety-net proof.

The exists-path writes state.destination from the map entry then delegates to
update_resource (PUT). The framework is a pure no-op for it; the safety-net test
proves the framework WOULD reconcile this resource if its exists-path regressed.

All identifiers are obviously synthetic (``gv-src``, ``gv-dst``).
"""

import asyncio
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

import pytest

from datadog_sync.model.synthetics_global_variables import SyntheticsGlobalVariables
from datadog_sync.utils.resource_utils import SkipResource


def _gv(name, gv_type="global", gv_id=None, value="v"):
    return {
        "id": gv_id or f"gv-id-{name}",
        "name": name,
        "type": gv_type,
        "value": {"value": value},
    }


def _make_gvs(existing_map=None, destination_client=None, source_client=None):
    config = MagicMock()
    config.destination_client = destination_client or MagicMock()
    config.source_client = source_client or MagicMock()
    config.state = MagicMock()
    config.state.source = defaultdict(dict)
    config.state.destination = defaultdict(dict)
    config.logger = MagicMock()
    gvs = SyntheticsGlobalVariables(config=config)
    gvs._existing_resources_map = existing_map or {}
    return gvs, config


class TestSyntheticsGlobalVariablesSkipReconcile:
    def test_existing_resource_create_writes_state_destination(self):
        _id = "gv-src"
        dest = _gv("gv-dst", gv_id="gv-dst")
        put_resp = {"id": "gv-dst", "name": "gv-dst", "type": "global", "value": {}}
        destination_client = MagicMock()
        destination_client.put = AsyncMock(return_value=put_resp)
        gvs, config = _make_gvs(
            existing_map={"gv-dst:global": dest},
            destination_client=destination_client,
        )
        # Pre-set value so _inject_secret_value skips the source clear-value fetch.
        resource = _gv("gv-dst", gv_id=_id)

        asyncio.run(gvs._create_resource(_id, resource))

        destination_client.put.assert_called_once()
        # update_resource does state.destination[_id].update(resp); wrapper rewrites.
        assert config.state.destination["synthetics_global_variables"][_id]["id"] == "gv-dst"

    def test_framework_reconciles_if_create_raised_skip(self):
        _id = "gv-src"
        dest = _gv("gv-dst", gv_id="gv-dst")
        gvs, config = _make_gvs(existing_map={"gv-dst:global": dest})
        gvs.create_resource = AsyncMock(side_effect=SkipResource(_id, "synthetics_global_variables", "exists"))
        resource = _gv("gv-dst", gv_id=_id)

        with pytest.raises(SkipResource):
            asyncio.run(gvs._create_resource(_id, resource))

        assert config.state.destination["synthetics_global_variables"][_id] == dest

    def test_create_non_existing_still_posts(self):
        _id = "gv-src"
        posted = _gv("gv-dst", gv_id="gv-dst")
        destination_client = MagicMock()
        destination_client.post = AsyncMock(return_value=posted)
        gvs, config = _make_gvs(
            existing_map={},
            destination_client=destination_client,
        )
        resource = _gv("gv-src", gv_id=_id)

        out_id, out_r = asyncio.run(gvs.create_resource(_id, resource))

        destination_client.post.assert_called_once()
        assert out_id == _id
        assert out_r == posted
