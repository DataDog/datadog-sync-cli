# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""
Unit tests for slo_corrections resource handling.

These tests verify that null-valued mutually-exclusive fields
(`slo_id` vs `slo_query`, and the optional `end`) are stripped from the
destination payload before POST, matching the destination API's
"Field may not be null" rejection semantics.

The destination `POST /api/v1/slo/correction` endpoint accepts either
a time-based correction (populated `slo_id`, populated `end`) or a
query-based correction (populated `slo_query`). It rejects payloads
that explicitly carry the alternative field with value `null` — the
error reads `API input validation failed: {'slo_query': ['Field may
not be null.']}` (or the corresponding key). Source resources round-
tripped from `GET /api/v1/slo/correction/{id}` carry both fields with
one of them explicitly null; those need to be stripped before the
destination POST.
"""

from datadog_sync.model.slo_corrections import SLOCorrections
from datadog_sync.utils.resource_utils import prep_resource


class TestSLOCorrectionsNonNullableStripping:
    """prep_resource must strip null-valued mutually-exclusive fields."""

    def test_time_based_correction_strips_null_slo_query(self):
        """A time-based correction (slo_id set, slo_query=null) must have
        slo_query removed from the payload before POST."""
        resource = {
            "type": "correction",
            "attributes": {
                "slo_id": "abc123",
                "slo_query": None,
                "start": 1700000000,
                "end": 1700010000,
                "category": "Scheduled Maintenance",
                "duration": None,
                "rrule": None,
                "description": "planned maintenance window",
            },
        }
        prep_resource(SLOCorrections.resource_config, resource)
        assert "slo_query" not in resource["attributes"]
        assert resource["attributes"]["slo_id"] == "abc123"
        assert resource["attributes"]["end"] == 1700010000

    def test_query_based_correction_strips_null_slo_id_and_null_end(self):
        """A query-based correction (slo_query set, slo_id=null, end=null)
        must have both slo_id and end removed from the payload."""
        resource = {
            "type": "correction",
            "attributes": {
                "slo_id": None,
                "slo_query": {"query_id": "query-123"},
                "start": 1700000000,
                "end": None,
                "category": "Scheduled Maintenance",
                "duration": None,
                "rrule": None,
                "description": "recurring correction",
            },
        }
        prep_resource(SLOCorrections.resource_config, resource)
        assert "slo_id" not in resource["attributes"]
        assert "end" not in resource["attributes"]
        assert resource["attributes"]["slo_query"] == {"query_id": "query-123"}

    def test_populated_fields_are_preserved(self):
        """Fields that ARE populated must NOT be stripped."""
        resource = {
            "type": "correction",
            "attributes": {
                "slo_id": "def456",
                "slo_query": None,
                "start": 1700000000,
                "end": 1700010000,
                "category": "Other",
                "duration": 3600,
                "rrule": "FREQ=DAILY",
                "description": "populated correction",
            },
        }
        prep_resource(SLOCorrections.resource_config, resource)
        assert resource["attributes"]["slo_id"] == "def456"
        assert resource["attributes"]["end"] == 1700010000
        assert resource["attributes"]["duration"] == 3600
        assert resource["attributes"]["rrule"] == "FREQ=DAILY"
        assert "slo_query" not in resource["attributes"]

    def test_zero_end_is_preserved_not_stripped(self):
        """del_null_attr uses `is None` so integer 0 and empty string "" on
        end/slo_id/slo_query are preserved. Regression pin — a stricter strip
        would break time-based corrections whose end unix-timestamp is 0.
        (No such correction should exist in practice, but the type semantics
        should not conflate 0 with null.)"""
        resource = {
            "type": "correction",
            "attributes": {
                "slo_id": "abc123",
                "slo_query": None,
                "start": 1700000000,
                "end": 0,
            },
        }
        prep_resource(SLOCorrections.resource_config, resource)
        assert resource["attributes"]["end"] == 0
        assert resource["attributes"]["slo_id"] == "abc123"
        assert "slo_query" not in resource["attributes"]

    def test_check_diff_symmetric_strip_no_spurious_diff(self):
        """When both `state` and `resource` are `prep_resource`'d before
        `check_diff` (as done in resources_handler.py:503-507 and :657-659),
        the diff is empty even if only one side has null slo_query/end.
        Regression pin against a future asymmetric-prep refactor that could
        cause spurious no-op updates on every dispatch."""
        from datadog_sync.utils.resource_utils import check_diff

        state = {
            "type": "correction",
            "attributes": {
                "slo_id": "abc123",
                "slo_query": None,  # would round-trip as null from destination GET
                "start": 1700000000,
                "end": 1700010000,
                "duration": None,
                "rrule": None,
                "category": "Scheduled Maintenance",
            },
        }
        resource = {
            "type": "correction",
            "attributes": {
                "slo_id": "abc123",
                "slo_query": None,
                "start": 1700000000,
                "end": 1700010000,
                "duration": None,
                "rrule": None,
                "category": "Scheduled Maintenance",
            },
        }
        prep_resource(SLOCorrections.resource_config, state)
        prep_resource(SLOCorrections.resource_config, resource)
        diff = check_diff(SLOCorrections.resource_config, resource, state)
        assert not diff, f"expected empty diff after symmetric strip, got: {diff}"

    def test_null_duration_and_rrule_still_stripped_regression(self):
        """Regression pin for existing behavior: duration and rrule
        remain in non_nullable_attr and null values are stripped."""
        resource = {
            "type": "correction",
            "attributes": {
                "slo_id": "abc123",
                "slo_query": None,
                "start": 1700000000,
                "end": 1700010000,
                "duration": None,
                "rrule": None,
            },
        }
        prep_resource(SLOCorrections.resource_config, resource)
        assert "duration" not in resource["attributes"]
        assert "rrule" not in resource["attributes"]
