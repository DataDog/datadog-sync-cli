import pytest
import copy

from tests.utils.helpers import *

@pytest.mark.vcr
@pytest.mark.integration
@pytest.mark.parametrize("resource_type", [resource_type for resource_type in all_resources])
def test_import_resources(config, resource_type):
    resource = config.resources[resource_type]

    resource.import_resources()
    assert resource.source_resources

    original = copy.deepcopy(resource.source_resources)
    resource.import_resources()

    assert resource.source_resources == original


@pytest.mark.vcr
@pytest.mark.integration
@pytest.mark.parametrize("resource_type", [resource_type for resource_type in all_resources])
def test_sync_resources(config, resource_type):
    resource = config.resources[resource_type]

    resource.import_resources()
    resource.apply_resources()

    assert not resource.check_diffs()

    original = copy.deepcopy(resource.destination_resources)
    resource.apply_resources()

    assert resource.destination_resources == original
