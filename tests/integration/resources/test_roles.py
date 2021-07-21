import pytest

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import Roles


class TestRolesResources(BaseResourcesTestClass):
    resource_type = Roles.resource_type
    field_to_update = "attributes.name"
