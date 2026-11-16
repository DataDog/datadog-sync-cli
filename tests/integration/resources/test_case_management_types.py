# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import CaseManagementTypes


@pytest.mark.skip(
    reason="cassettes pending recording: case management endpoints require the internal "
    "test-org fixture pair (synthetic case_type_test fixtures seeded first)"
)
class TestCaseManagementTypesResources(BaseResourcesTestClass):
    resource_type = CaseManagementTypes.resource_type
    field_to_update = "attributes.description"
    resources_to_preserve_filter = (
        "Type=case_management_types;Name=attributes.name;Value=case_type_test;Operator=SubString"
    )
