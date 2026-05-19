# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from datadog_sync.model.logs_archives import LogsArchives
from datadog_sync.utils.resource_utils import CustomClientHTTPError


def _http_error(status, message):
    return CustomClientHTTPError(SimpleNamespace(status=status, message="err"), message=message)


def test_duplicate_archive_adopts_existing_and_updates_it(mock_config):
    archive = LogsArchives(mock_config)
    source = {
        "id": "src-archive",
        "type": "archives",
        "attributes": {
            "name": "shared archive",
            "query": "service:api",
        },
    }
    existing = {
        "id": "dest-archive",
        "type": "archives",
        "attributes": {
            "name": "shared archive",
            "query": "service:old",
        },
    }
    updated = {
        "id": "dest-archive",
        "type": "archives",
        "attributes": {
            "name": "shared archive",
            "query": "service:api",
        },
    }

    mock_config.destination_client.post = AsyncMock(side_effect=_http_error(400, "already exists"))
    mock_config.destination_client.get = AsyncMock(return_value={"data": [existing]})
    mock_config.destination_client.put = AsyncMock(return_value={"data": updated})

    _id, result = asyncio.run(archive.create_resource("src-archive", source))

    assert _id == "src-archive"
    assert result == updated
    assert mock_config.state.destination["logs_archives"]["src-archive"] == updated
    mock_config.destination_client.post.assert_awaited_once_with("/api/v2/logs/config/archives", {"data": source})
    mock_config.destination_client.get.assert_awaited_once_with("/api/v2/logs/config/archives")
    mock_config.destination_client.put.assert_awaited_once_with(
        "/api/v2/logs/config/archives/dest-archive",
        {"data": source},
    )
