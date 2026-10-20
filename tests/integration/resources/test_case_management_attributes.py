# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Integration tests for the case_management_attributes resource.

Follows the team_memberships precedent: the base-class import step imports
ONLY attributes (helpers.import_resources never passes
--force-missing-dependencies), so the referenced parent case type is absent
from local source state and the sync-time force-missing-dependency callback
resolves it through CaseManagementTypes' list-backed _id import — the same
real CLI flow as `sync --resources=case_management_attributes
--force-missing-dependencies`.
"""

import json
import logging
import os

import pytest

from tests.integration.helpers import BaseResourcesTestClass, open_resources
from datadog_sync.cli import cli
from datadog_sync.models import CaseManagementAttributes


@pytest.mark.skip(
    reason="cassettes pending recording: case management endpoints require the internal "
    "test-org fixture pair (synthetic case_type_test / attr_key_test fixtures seeded first)"
)
class TestCaseManagementAttributesResources(BaseResourcesTestClass):
    resource_type = CaseManagementAttributes.resource_type
    dependencies = list(CaseManagementAttributes.resource_config.resource_connections.keys())
    force_missing_deps = True
    field_to_update = "attributes.description"
    resources_to_preserve_filter = (
        "Type=case_management_attributes;Name=attributes.key;Value=attr_key_test;Operator=SubString"
    )

    def test_sync_from_empty_state_imports_type_then_attribute(self, runner, caplog):
        """Start with NO local state: import attributes only (the parent type
        is not in source state), then sync with --force-missing-dependencies.
        Asserts destination state ends up with BOTH the parent case type
        (created/mapped first via the force-missing-dep list-backed import)
        and the attribute created after it."""
        caplog.set_level(logging.DEBUG)

        # Start from empty local state.
        self.clean_resource_files()

        self.import_resources(runner, caplog)
        caplog.clear()
        self.sync_resources(runner, caplog)

        _, destination_attributes = open_resources(self.resource_type)
        _, destination_types = open_resources("case_management_types")
        assert len(destination_attributes) > 0
        assert len(destination_types) > 0

    def test_delete_and_recreate_flow(self, runner, caplog):
        """Exercise real deletion ordering (the base-class cleanup preserves
        fixtures by re-importing the preserve filter, so DELETE is otherwise
        never exercised): sync the synthetic type + attribute, remove ONLY
        their local source-state entries, run --cleanup=force over both
        resource types, assert the attribute is deleted BEFORE its parent
        type (reverse partial order), then re-import/re-sync from the
        still-existing source-org fixtures and verify recreation succeeds
        (the tombstoned-name-reuse check).

        Scoped narrowly to ids already in local destination state, which is
        all --cleanup can see.
        """
        caplog.set_level(logging.DEBUG)

        # Ensure both resources exist locally and at the destination.
        self.import_resources(runner, caplog)
        caplog.clear()
        self.sync_resources(runner, caplog)
        caplog.clear()

        # Remove ONLY the local source entries for the ids we synced —
        # without re-importing the preserve filter — so --cleanup=force
        # deletes exactly those destination resources.
        for resource_type in (self.resource_type, "case_management_types"):
            _, destination_resources = open_resources(resource_type)
            for _id in destination_resources:
                combined = f"resources/source/{resource_type}.json"
                if os.path.exists(combined):
                    with open(combined, encoding="utf-8") as f:
                        state = json.load(f)
                    state.pop(_id, None)
                    with open(combined, "w", encoding="utf-8") as f:
                        json.dump(state, f, indent=2)
                else:
                    # resource-per-file layout
                    safe_id = _id.replace(":", ".")
                    path = f"resources/source/{resource_type}.{safe_id}.json"
                    if os.path.exists(path):
                        os.remove(path)

        # Cleanup both resource types (dependents before dependencies).
        sync_cmd = [
            "sync",
            "--validate=false",
            "--verify-ddr-status=False",
            "--cleanup=force",
            "--resources=case_management_attributes,case_management_types",
            "--force-missing-dependencies",
            "--create-global-downtime=False",
            "--send-metrics=False",
        ]
        ret = runner.invoke(cli, sync_cmd)
        assert 0 == ret.exit_code

        # Attribute DELETE must occur before the parent type DELETE.
        delete_records = [
            record
            for record in caplog.records
            if record.message == "deleting resource" or "deleting resource" in record.getMessage()
        ]
        ordered_types = [record.resource_type for record in delete_records]
        assert (
            "case_management_attributes" in ordered_types and "case_management_types" in ordered_types
        ), f"expected deletes for both resource types, got: {ordered_types}"
        assert ordered_types.index("case_management_attributes") < ordered_types.index(
            "case_management_types"
        ), "attributes must be deleted before their parent case types"

        # Recreate from the still-existing source-org fixtures: the deleted
        # type's name is re-created after tombstoning.
        caplog.clear()
        self.clean_resource_files()
        self.import_resources(runner, caplog)
        caplog.clear()
        self.sync_resources(runner, caplog)

        _, destination_attributes = open_resources(self.resource_type)
        _, destination_types = open_resources("case_management_types")
        assert len(destination_attributes) > 0
        assert len(destination_types) > 0
