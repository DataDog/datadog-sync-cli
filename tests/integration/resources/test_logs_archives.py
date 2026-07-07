# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import LogsArchives


class TestLogsArchivesResources(BaseResourcesTestClass):
    resource_type = LogsArchives.resource_type
    field_to_update = "attributes.name"

    @pytest.mark.skip(reason="Logs archive require an AWS, GCP, or Azure account")
    def test_resource_import(self):
        pass

    @pytest.mark.skip(reason="Logs archive require an AWS, GCP, or Azure account")
    def test_resource_import_per_file(self):
        pass

    @pytest.mark.skip(reason="Test-org archive points to a missing S3 bucket; sync fails 400")
    def test_resource_sync(self, runner, caplog):
        pass

    @pytest.mark.skip(reason="Test-org archive points to a missing S3 bucket; sync fails 400")
    def test_resource_sync_per_file(self, runner, caplog):
        pass

    @pytest.mark.skip(reason="Test-org archive points to a missing S3 bucket; sync fails 400")
    def test_resource_update_sync(self, runner, caplog):
        pass

    @pytest.mark.skip(reason="Test-org archive points to a missing S3 bucket; sync fails 400")
    def test_resource_update_sync_per_file(self, runner, caplog):
        pass

    @pytest.mark.skip(reason="No preceding sync ran (all skipped above); cleanup mkdir fails")
    def test_resource_cleanup(self, runner, caplog):
        pass

    @pytest.mark.skip(reason="No preceding sync ran (all skipped above); no diffs to check")
    def test_no_resource_diffs(self, runner, caplog):
        pass
