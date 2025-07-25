# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import pytest

from tests.conftest import get_record_mode
from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import SensitiveDataScannerRules


@pytest.mark.skipif(get_record_mode() in ["all", "none", "once"], reason="test is run in integration mode only")
class TestSensitiveDataScannerRulesResources(BaseResourcesTestClass):
    resource_type = SensitiveDataScannerRules.resource_type
    dependencies = list(SensitiveDataScannerRules.resource_config.resource_connections.keys())
    field_to_update = "attributes.is_enabled"
    force_missing_deps = True
