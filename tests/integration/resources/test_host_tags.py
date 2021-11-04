# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from datadog_sync.models import HostTags
from tests.integration.helpers import BaseResourcesTestClass


class TestHostTagsResources(BaseResourcesTestClass):
    resource_type = HostTags.resource_type
    field_to_update = ""
