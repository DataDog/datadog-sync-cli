# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import LogsIndexes


class TestLogsIndexesResources(BaseResourcesTestClass):
    resource_type = LogsIndexes.resource_type
    field_to_update = "filter.query"

    @pytest.mark.skip(reason="delete is not supported for logs-indexes")
    def test_resource_cleanup(self):
        pass
