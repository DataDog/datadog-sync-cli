# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import asyncio
from unittest.mock import AsyncMock, call

from datadog_sync.model.logs_restriction_queries import LogsRestrictionQueries


def test_reassign_role_scans_paginated_query_list(mock_config):
    restriction_queries = LogsRestrictionQueries(mock_config)
    restriction_queries.get_resources = AsyncMock(
        return_value=[
            {"id": "target-query"},
            {"id": "query-from-later-page"},
        ]
    )
    restriction_queries.remove_log_restriction_query_role = AsyncMock()
    restriction_queries.add_log_restriction_query_role = AsyncMock()

    async def get_roles(path):
        if path == "/api/v2/logs/config/restriction_queries/target-query/roles":
            return {"data": []}
        if path == "/api/v2/logs/config/restriction_queries/query-from-later-page/roles":
            return {"data": [{"id": "role-1", "type": "roles"}]}
        raise AssertionError(f"unexpected path: {path}")

    mock_config.destination_client.get = AsyncMock(side_effect=get_roles)

    asyncio.run(restriction_queries._reassign_role("target-query", "role-1"))

    restriction_queries.get_resources.assert_awaited_once_with(mock_config.destination_client)
    assert mock_config.destination_client.get.await_args_list == [
        call("/api/v2/logs/config/restriction_queries/target-query/roles"),
        call("/api/v2/logs/config/restriction_queries/query-from-later-page/roles"),
    ]
    restriction_queries.remove_log_restriction_query_role.assert_awaited_once_with("query-from-later-page", "role-1")
    restriction_queries.add_log_restriction_query_role.assert_awaited_once_with("target-query", "role-1")
