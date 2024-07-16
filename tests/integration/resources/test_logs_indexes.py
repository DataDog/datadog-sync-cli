# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import os
import json
import logging
import urllib.request

from datadog_sync.models import LogsIndexes
from datadog_sync.cli import cli
from tests.integration.helpers import BaseResourcesTestClass, open_resources, save_source_resources


class TestLogsIndexesResources(BaseResourcesTestClass):
    resource_type = LogsIndexes.resource_type
    field_to_update = "filter.query"

    def test_resource_cleanup(self, runner, caplog):
        caplog.set_level(logging.DEBUG)

        api_key = os.environ.get("DD_DESTINATION_API_KEY")
        app_key = os.environ.get("DD_DESTINATION_APP_KEY")
        api_url = os.environ.get("DD_DESTINATION_API_URL")
        logs_index_order_req = urllib.request.Request(
            f"{api_url}/api/v1/logs/config/index-order", headers={"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key}
        )

        # Get the initial logs index order
        with urllib.request.urlopen(logs_index_order_req) as response:
            order = json.loads(response.read())
        assert len(order["index_names"]) > 1

        # Remove the first resource from the source state file
        source_resources, _ = open_resources(self.resource_type)
        first_index = order["index_names"][0]
        source_resources.pop(first_index)
        save_source_resources(self.resource_type, source_resources)

        # Sync with cleanup
        ret = runner.invoke(
            cli,
            [
                "sync",
                "--validate=false",
                f"--resources={self.resource_type}",
                f"--filter={self.filter}",
                "--cleanup=force",
            ],
        )
        assert 0 == ret.exit_code

        # Get the updated logs index order
        with urllib.request.urlopen(logs_index_order_req) as response:
            order = json.loads(response.read())

        # assert the first index removed from source organization
        # is now the last index in the destination index order
        assert first_index == order["index_names"][-1]
