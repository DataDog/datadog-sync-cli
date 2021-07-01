import pytest
import os
import copy

from datadog_sync import constants
from datadog_sync import models
from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.cli import get_import_order


# TODO: Fix order of sync

str_to_class = dict(
        (cls.resource_type, cls)
        for cls in models.__dict__.values()
        if isinstance(cls, type) and issubclass(cls, BaseResource)
    )


all_resources = get_import_order([cls for cls in models.__dict__.values() if isinstance(cls, type) and issubclass(cls, BaseResource)], str_to_class)


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

