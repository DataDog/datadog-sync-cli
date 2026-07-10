# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for the counter's per-resource-type failure / missing-deps id tracking.

Motivation: managed-sync runs sync-cli once per resource type as separate
processes. When a downstream type (e.g. monitors) fails to remap a role
UUID from an earlier roles-sync process, an operator has to correlate the
cascade back to the specific source ids that failed in the earlier process.
Previously only aggregate counts were logged. These tests verify that the
counter records the specific source ids by resource type and that
apply_resources emits a targeted per-type summary.
"""

from datadog_sync.utils.workers import Counter


def test_counter_defaults_have_empty_id_maps():
    c = Counter()
    assert c.failed_ids_by_type == {}
    assert c.skipped_missing_deps_by_type == {}


def test_increment_failure_records_source_id_by_type():
    c = Counter()
    c.increment_failure(resource_type="roles", _id="uuid-a")
    c.increment_failure(resource_type="roles", _id="uuid-b")
    c.increment_failure(resource_type="monitors", _id="mon-1")
    assert c.failure == 3
    assert c.failed_ids_by_type["roles"] == ["uuid-a", "uuid-b"]
    assert c.failed_ids_by_type["monitors"] == ["mon-1"]


def test_increment_failure_still_counts_without_ids():
    """Backwards compat: legacy callers passing no args still increment the
    numeric counter (they just don't populate the id map)."""
    c = Counter()
    c.increment_failure()
    c.increment_failure()
    assert c.failure == 2
    assert c.failed_ids_by_type == {}


def test_increment_skipped_records_missing_deps_only_when_flagged():
    c = Counter()
    # Regular skip: numeric only, no id tracked.
    c.increment_skipped(resource_type="monitors", _id="mon-2")
    # Missing-deps skip: id tracked so downstream cascade correlation works.
    c.increment_skipped(resource_type="monitors", _id="mon-3", missing_deps=True)
    c.increment_skipped(resource_type="dashboards", _id="dsh-1", missing_deps=True)
    assert c.skipped == 3
    assert c.skipped_missing_deps_by_type["monitors"] == ["mon-3"]
    assert c.skipped_missing_deps_by_type["dashboards"] == ["dsh-1"]


def test_increment_skipped_still_counts_without_ids():
    c = Counter()
    c.increment_skipped()
    c.increment_skipped()
    assert c.skipped == 2
    assert c.skipped_missing_deps_by_type == {}


def test_reset_clears_id_maps():
    c = Counter()
    c.increment_failure(resource_type="roles", _id="uuid-a")
    c.increment_skipped(resource_type="monitors", _id="mon-1", missing_deps=True)
    c.reset_counter()
    assert c.failure == 0
    assert c.skipped == 0
    assert c.failed_ids_by_type == {}
    assert c.skipped_missing_deps_by_type == {}


def test_string_representation_unchanged():
    """The __str__ format is consumed by logs downstream; keep it stable so
    log-based dashboards don't break."""
    c = Counter(successes=10, failure=2, skipped=1, filtered=3)
    assert str(c) == "Successes: 10, Failures: 2, Skipped: 1, Filtered: 3"
