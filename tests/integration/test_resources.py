import pytest
import os

from datadog_sync import constants
from datadog_sync import models
from datadog_sync.utils.base_resource import BaseResource


all_resources = [
    cls.resource_type for cls in models.__dict__.values() if isinstance(cls, type) and issubclass(cls, BaseResource)
]


@pytest.mark.vcr
@pytest.mark.integration
@pytest.mark.parametrize("resource_type", [resource_type for resource_type in all_resources])
def test_import_resources(config, resource_type):
    resource = config.resources[resource_type]
    resource.import_resources()
    assert resource.source_resources


@pytest.mark.vcr
@pytest.mark.integration
@pytest.mark.parametrize("resource_type", [resource_type for resource_type in all_resources])
def test_sync_resources(config, resource_type):
    resource = config.resources[resource_type]
    resource.import_resources()
    resource.apply_resources()
    assert not resource.check_diffs()
