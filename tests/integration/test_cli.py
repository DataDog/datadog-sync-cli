import os
import json

from click.testing import CliRunner
from datadog_sync.cli import cli
import pytest


CLEANUP_RESOURCES = [
    "monitor",
    "dashboard_json",
    "downtime",
    "synthetics_test",
    "synthetics_private_location",
    "logs_custom_pipeline",
    "integration_aws",
]


@pytest.fixture(autouse=True)
def with_cleanup():
    runner = CliRunner()
    resources = ','.join(CLEANUP_RESOURCES)
    with runner.isolated_filesystem():
        yield
        result = runner.invoke(cli, ["-v", "--resources={}".format(resources), "destroy"])
        assert result.exit_code == 0


@pytest.mark.skipif(os.getenv("DD_INTEGRATION") is None, reason="Set DD_INTEGRATION to run integration tests")
def test_cli():
    provider = {"terraform": {"required_providers": [{"datadog": {"source": "datadog/datadog"}}]}}
    runner = CliRunner()

    with open("provider.tf.json", "w") as f:
        json.dump(provider, f, indent=2)

    result = runner.invoke(cli, ["-v", "import"])
    assert result.exit_code == 0

    result = runner.invoke(cli, ["-v", "sync"])
    assert result.exit_code == 0
