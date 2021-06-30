import pytest


@pytest.mark.vcr
@pytest.mark.integration
def test_import(config):
    for resource in config.resources:
        resource.import_resources()
        assert resource.source_resources
