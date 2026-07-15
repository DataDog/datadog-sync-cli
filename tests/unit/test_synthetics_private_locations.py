# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Unit tests for synthetics_private_locations connect_resources drop behavior.

The only access-control connection is the flat `metadata.restricted_roles` list. Under
--drop-unresolvable-principals a permanently-stale role is dropped; an emptied list still
hard-fails as an access-elevation guard.
"""

from collections import defaultdict
from unittest.mock import MagicMock

import pytest

from datadog_sync.model.synthetics_private_locations import SyntheticsPrivateLocations
from datadog_sync.utils.resource_utils import ResourceConnectionError
from datadog_sync.utils.workers import Counter


def _make_spl(drop=False, skip_failed=False):
    config = MagicMock()
    config.state = MagicMock()
    config.state.source = defaultdict(dict)
    config.state.destination = defaultdict(dict)
    config.state.ensure_resource_loaded = MagicMock()
    config.drop_unresolvable_principals = drop
    config.skip_failed_resource_connections = skip_failed
    config.counter = Counter()
    config.logger = MagicMock()
    return SyntheticsPrivateLocations(config)


def _seed_valid_role(spl, src="role-good", dst="role-good-dst"):
    spl.config.state.destination["roles"][src] = {"id": dst}


def test_flag_off_stale_role_hard_fails():
    spl = _make_spl(drop=False)
    resource = {"id": "pl:abc", "metadata": {"restricted_roles": ["role-gone"]}}
    with pytest.raises(ResourceConnectionError):
        spl.connect_resources("pl:abc", resource)
    assert resource["metadata"]["restricted_roles"] == ["role-gone"]  # not dropped when off


def test_flag_on_drops_stale_keeps_valid():
    spl = _make_spl(drop=True)
    _seed_valid_role(spl)
    resource = {"id": "pl:abc", "metadata": {"restricted_roles": ["role-good", "role-gone"]}}
    spl.connect_resources("pl:abc", resource)  # no raise
    assert resource["metadata"]["restricted_roles"] == ["role-good-dst"]
    assert spl.config.counter.stale_principals_dropped_by_type["synthetics_private_locations"] == ["pl:abc"]


def test_flag_on_all_stale_empty_list_raises_risk():
    spl = _make_spl(drop=True)
    resource = {"id": "pl:abc", "metadata": {"restricted_roles": ["role-gone"]}}
    with pytest.raises(ResourceConnectionError) as exc_info:
        spl.connect_resources("pl:abc", resource)
    assert exc_info.value.empty_binding_risk is True
    assert resource["metadata"]["restricted_roles"] == []


def test_empty_list_risk_is_returned_when_connection_failure_is_suppressed():
    spl = _make_spl(drop=True, skip_failed=True)
    resource = {"id": "pl:abc", "metadata": {"restricted_roles": ["role-gone"]}}

    assert spl.connect_resources("pl:abc", resource).empty_binding_escalation is True
    assert resource["metadata"]["restricted_roles"] == []


def test_flag_on_middle_drop_keeps_neighbors():
    spl = _make_spl(drop=True)
    _seed_valid_role(spl, src="ra", dst="ra-dst")
    _seed_valid_role(spl, src="rc", dst="rc-dst")
    resource = {"id": "pl:abc", "metadata": {"restricted_roles": ["ra", "role-gone", "rc"]}}
    spl.connect_resources("pl:abc", resource)  # no raise
    assert resource["metadata"]["restricted_roles"] == ["ra-dst", "rc-dst"]


def test_no_metadata_is_noop():
    spl = _make_spl(drop=True)
    resource = {"id": "pl:abc"}  # no metadata/restricted_roles
    spl.connect_resources("pl:abc", resource)  # no raise
