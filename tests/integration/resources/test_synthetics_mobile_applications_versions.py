# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import pytest

from datadog_sync.models import SyntheticsMobileApplicationsVersions
from tests.integration.helpers import BaseResourcesTestClass


@pytest.mark.skip(reason="The tests work but the cassettes are too large for git")
class TestSyntheticsMobileApplicationsVersionsResources(BaseResourcesTestClass):
    resource_type = SyntheticsMobileApplicationsVersions.resource_type
    dependencies = list(SyntheticsMobileApplicationsVersions.resource_config.resource_connections.keys())
    field_to_update = "version_name"
    force_missing_deps = True
