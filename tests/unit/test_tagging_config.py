# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import pytest

from datadog_sync.utils.base_resource import TaggingConfig
from datadog_sync.utils.resource_utils import DEFAULT_TAGS


@pytest.mark.parametrize(
    "path, r_obj, expected",
    [
        (
            "tags",
            {"tags": ["test:true"]},
            {"tags": ["test:true", "managed_by:datadog-sync"]},
        ),
        (
            "tags",
            {"tags": []},
            {"tags": ["managed_by:datadog-sync"]},
        ),
        (
            "tags",
            {},
            {"tags": ["managed_by:datadog-sync"]},
        ),
        (
            "nested.tags",
            {"nested": {"tags": ["test:true"]}},
            {"nested": {"tags": ["test:true", "managed_by:datadog-sync"]}},
        ),
        (
            "nested.tags",
            {"nested": {"tags": []}},
            {"nested": {"tags": ["managed_by:datadog-sync"]}},
        ),
        (
            "nested.tags",
            {"nested": {}},
            {"nested": {"tags": ["managed_by:datadog-sync"]}},
        ),
        (
            "nested.missing.tags",
            {"nested": {}},
            {"nested": {}},
        ),
    ],
)
def test_tagging_config(path, r_obj, expected):
    c = TaggingConfig(path)
    c.add_default_tags(r_obj)

    assert r_obj == expected


def test_add_default_tags_to_empty_resource():
    """Resource without the tags path should be populated with DEFAULT_TAGS."""
    c = TaggingConfig("tags")
    r_obj = {}
    c.add_default_tags(r_obj)

    assert r_obj == {"tags": list(DEFAULT_TAGS)}


def test_add_default_tags_to_resource_with_existing_unrelated_tags():
    """Defaults should be appended after the resource's existing unrelated tags."""
    c = TaggingConfig("tags")
    r_obj = {"tags": ["env:prod", "team:chaos"]}
    c.add_default_tags(r_obj)

    assert r_obj == {"tags": ["env:prod", "team:chaos", "managed_by:datadog-sync"]}


def test_add_default_tags_idempotent():
    """Regression: calling add_default_tags twice must not duplicate DEFAULT_TAGS."""
    c = TaggingConfig("tags")
    r_obj = {"tags": ["env:prod"]}

    c.add_default_tags(r_obj)
    once = list(r_obj["tags"])

    c.add_default_tags(r_obj)

    assert r_obj["tags"] == once
    # Explicit check that managed_by:datadog-sync only appears once.
    assert r_obj["tags"].count("managed_by:datadog-sync") == 1


def test_add_default_tags_preserves_order_of_existing_tags():
    """Existing tags must remain in their original positions; defaults append at end."""
    c = TaggingConfig("tags")
    r_obj = {"tags": ["z:last", "a:first", "m:middle"]}
    c.add_default_tags(r_obj)

    assert r_obj["tags"] == ["z:last", "a:first", "m:middle", "managed_by:datadog-sync"]


def test_add_default_tags_does_not_mutate_default_tags_class_attribute():
    """Setting tags on a tagless resource must defensively copy default_tags;
    later mutations to the resource's list must not bleed into other instances."""
    c1 = TaggingConfig("tags")
    c2 = TaggingConfig("tags")

    r1 = {}
    c1.add_default_tags(r1)

    # Mutate the resource's tag list — this simulates downstream code editing it.
    r1["tags"].append("extra:tag")

    # The other instance's default_tags must remain pristine.
    assert c2.default_tags == DEFAULT_TAGS
    # And applying it to a fresh resource must not carry the mutation.
    r2 = {}
    c2.add_default_tags(r2)
    assert r2 == {"tags": list(DEFAULT_TAGS)}
