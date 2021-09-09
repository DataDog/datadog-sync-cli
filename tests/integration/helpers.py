# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import os
import re
import json
import logging

import pytest

from datadog_sync.constants import RESOURCE_FILE_PATH
from datadog_sync.cli import cli


RESOURCE_TO_ADD_RE = re.compile("Resource to be added")
RESOURCE_SKIPPED_RE = re.compile("Skipping resource")


@pytest.mark.vcr
@pytest.mark.integration
class BaseResourcesTestClass:
    resource_type = None
    field_to_update = None

    @pytest.fixture(autouse=True, scope="class")
    def setup(self, tmpdir_factory):
        my_tmpdir = tmpdir_factory.mktemp("tmp")
        os.chdir(my_tmpdir)

    def test_resource_import(self, runner):
        ret = runner.invoke(cli, ["import", f"--resources={self.resource_type}"])
        assert 0 == ret.exit_code

        # Assert at lease one resource is imported
        source_resources, _ = open_resources(self.resource_type)
        assert len(source_resources) > 0

        # Disable skipping on resource connection failure
        # From stdout, count the  number of resources to be added and ensure they match the import len()
        ret = runner.invoke(
            cli, ["diffs", f"--resources={self.resource_type}", "--skip-failed-resource-connections=false"]
        )
        assert 0 == ret.exit_code

        num_resources_to_add = len(RESOURCE_TO_ADD_RE.findall(ret.output))
        assert num_resources_to_add == len(source_resources)

    def test_resource_sync(self, runner, caplog):
        ret = runner.invoke(cli, ["sync", f"--resources={self.resource_type}"])
        assert 0 == ret.exit_code

        # By default, resources  with failed connections are skipped. Hence count number of skipped + success
        num_resources_skipped = len(RESOURCE_SKIPPED_RE.findall(caplog.text))
        source_resources, destination_resources = open_resources(self.resource_type)

        assert len(source_resources) == (len(destination_resources) + num_resources_skipped)

    def test_resource_update_sync(self, runner, caplog):
        source_resources, _ = open_resources(self.resource_type)

        # update fields and save the file.
        for resource in source_resources.values():
            try:
                current_value = pathLookup(resource, self.field_to_update)
                if current_value is None:
                    current_value = ""

                pathUpdate(resource, self.field_to_update, current_value + "Updated")
            except Exception as e:
                pytest.fail(e)

        save_source_resources(self.resource_type, source_resources)

        # assert diff is produced
        ret = runner.invoke(cli, ["diffs", f"--resources={self.resource_type}"])
        assert ret.output
        assert 0 == ret.exit_code

        # sync the updated resources
        ret = runner.invoke(cli, ["sync", f"--resources={self.resource_type}"])
        assert 0 == ret.exit_code
        caplog.clear()

        # assert diff is no longer produced
        ret = runner.invoke(cli, ["diffs", f"--resources={self.resource_type}"])
        assert 0 == ret.exit_code
        assert not ret.output

        # Assert number of synced and imported resources match
        num_resources_skipped = len(RESOURCE_SKIPPED_RE.findall(caplog.text))
        source_resources, destination_resources = open_resources(self.resource_type)
        assert len(source_resources) == (len(destination_resources) + num_resources_skipped)

    def test_no_resource_diffs(self, runner, caplog):
        ret = runner.invoke(cli, ["diffs", f"--resources={self.resource_type}"])
        assert not ret.output
        assert 0 == ret.exit_code

        num_resources_skipped = len(RESOURCE_SKIPPED_RE.findall(caplog.text))
        source_resources, destination_resources = open_resources(self.resource_type)
        assert len(source_resources) == (len(destination_resources) + num_resources_skipped)


def save_source_resources(resource_type, resources):
    source_path = RESOURCE_FILE_PATH.format("source", resource_type)
    with open(source_path, "w") as f:
        json.dump(resources, f, indent=2)


def open_resources(resource_type):
    source_resources = dict()
    destination_resources = dict()

    source_path = RESOURCE_FILE_PATH.format("source", resource_type)
    destination_path = RESOURCE_FILE_PATH.format("destination", resource_type)

    if os.path.exists(source_path):
        with open(source_path, "r") as f:
            try:
                source_resources = json.load(f)
            except json.decoder.JSONDecodeError as e:
                pytest.fail(e)

    if os.path.exists(destination_path):
        with open(destination_path, "r") as f:
            try:
                destination_resources = json.load(f)
            except json.decoder.JSONDecodeError as e:
                pytest.fail(e)

    return source_resources, destination_resources


def pathLookup(obj, path):
    path = path.split(".", 1)
    if len(path) == 1:
        if path[0] in obj:
            return obj[path[0]]
        else:
            raise Exception(f"pathLookup error: invalid key {path}")
    else:
        if path[0] in obj:
            return pathLookup(obj[path[0]], path[1])
        else:
            raise Exception(f"pathLookup error: invalid key {path}")


def pathUpdate(obj, path, value):
    path = path.split(".", 1)
    if len(path) == 1:
        if path[0] in obj:
            obj[path[0]] = value
        else:
            raise Exception(f"pathUpdate error: invalid key {path}")
    else:
        if path[0] in obj:
            pathUpdate(obj[path[0]], path[1], value)
        else:
            raise Exception(f"pathUpdate error: invalid key {path}")
