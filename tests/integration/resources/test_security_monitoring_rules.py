# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import SecurityMonitoringRules


class TestSecurityMonitoringRules(BaseResourcesTestClass):
    """Filter out the deprecated security rules"""

    @staticmethod
    def compute_changes(resource_count, num_of_skips):
        """Subtract the skips from the resource count"""
        return resource_count - num_of_skips

    resource_type = SecurityMonitoringRules.resource_type
    field_to_update = "isEnabled"
    filter = "Type=security_monitoring_rules;Name=isDeprecated;Value=false;Operator=ExactMatch"
