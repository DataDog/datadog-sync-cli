# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import LogsArchivesOrder


class TestLogsArchivesOrder(BaseResourcesTestClass):
    resource_type = LogsArchivesOrder.resource_type
    dependencies = list(LogsArchivesOrder.resource_config.resource_connections.keys())
    force_missing_deps = True

    @pytest.mark.skip(reason="resource is only updated by default")
    def test_resource_update_sync(self):
        pass

    @pytest.mark.skip(reason="resource is only updated by default")
    def test_resource_update_sync_per_file(self):
        pass

    @pytest.mark.skip(reason="Depends on logs_archives sync, which is skipped due to missing S3 bucket in test org")
    def test_resource_sync(self, runner, caplog):
        pass

    @pytest.mark.skip(reason="Depends on logs_archives sync, which is skipped due to missing S3 bucket in test org")
    def test_resource_sync_per_file(self, runner, caplog):
        pass
