# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import AuthNMappings


class TestAuthNMappingsResources(BaseResourcesTestClass):
    resource_type = AuthNMappings.resource_type
    dependencies = list(AuthNMappings.resource_config.resource_connections.keys())
    field_to_update = "attributes.attribute_value"
    force_missing_deps = True
