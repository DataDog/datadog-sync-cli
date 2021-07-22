import pytest

from tests.integration.helpers import BaseResourcesTestClass
from datadog_sync.models import SLOCorrections


class TestSLOCorrections(BaseResourcesTestClass):
    resource_type = SLOCorrections.resource_type
    field_to_update = "attributes.description"

    @pytest.mark.skip(reason="nested attribute update is not currently supported in tests")
    def test_resource_update_sync(self):
        pass
