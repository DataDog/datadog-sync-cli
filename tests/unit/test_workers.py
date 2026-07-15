# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Unit tests for the Counter buckets added for --drop-unresolvable-principals.

Guards two things: (1) the new fields default empty and record correctly, and
(2) reset_counter() zeroes them so repeated import/sync invocations in one process
don't double-count.
"""

from datadog_sync.utils.workers import Counter


def test_new_counter_fields_default_empty():
    counter = Counter()
    assert counter.stale_principals_dropped_by_type == {}
    assert counter.empty_binding_risk_by_type == {}
    assert counter.empty_binding_escalation_by_type == {}


def test_record_stale_principal_dropped_appends_by_type():
    counter = Counter()
    counter.record_stale_principal_dropped(resource_type="restriction_policies", _id="dashboard:abc")
    counter.record_stale_principal_dropped(resource_type="restriction_policies", _id="dashboard:def")
    counter.record_stale_principal_dropped(resource_type="monitors", _id="mon-1")

    assert counter.stale_principals_dropped_by_type["restriction_policies"] == [
        "dashboard:abc",
        "dashboard:def",
    ]
    assert counter.stale_principals_dropped_by_type["monitors"] == ["mon-1"]


def test_record_empty_binding_risk_appends_by_type():
    counter = Counter()
    counter.record_empty_binding_risk(resource_type="restriction_policies", _id="dashboard:dash-1")

    assert counter.empty_binding_risk_by_type["restriction_policies"] == ["dashboard:dash-1"]


def test_record_empty_binding_escalation_appends_by_type():
    counter = Counter()
    counter.record_empty_binding_escalation(resource_type="restriction_policies", _id="dashboard:dash-1")

    assert counter.empty_binding_escalation_by_type["restriction_policies"] == ["dashboard:dash-1"]


def test_record_methods_ignore_none_args():
    counter = Counter()
    counter.record_stale_principal_dropped(resource_type=None, _id="x")
    counter.record_stale_principal_dropped(resource_type="roles", _id=None)
    counter.record_empty_binding_risk(resource_type=None, _id=None)
    counter.record_empty_binding_escalation(resource_type=None, _id=None)

    assert counter.stale_principals_dropped_by_type == {}
    assert counter.empty_binding_risk_by_type == {}
    assert counter.empty_binding_escalation_by_type == {}


def test_reset_counter_clears_new_fields():
    counter = Counter()
    counter.record_stale_principal_dropped(resource_type="roles", _id="r1")
    counter.record_empty_binding_risk(resource_type="roles", _id="r2")
    counter.record_empty_binding_escalation(resource_type="roles", _id="r3")

    counter.reset_counter()

    assert counter.stale_principals_dropped_by_type == {}
    assert counter.empty_binding_risk_by_type == {}
    assert counter.empty_binding_escalation_by_type == {}
