# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for dashboards resource configuration and short-circuit.

Pins the list_omitted_attr_prefixes opt-in and the prefetched-body
short-circuit shared with notebooks.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from datadog_sync.model.dashboards import Dashboards


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
