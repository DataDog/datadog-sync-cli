# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
"""get_single must return (None, None) when a per-resource file holds valid
JSON that is not an object, instead of raising AttributeError."""

import json

from datadog_sync.utils.storage.local_file import LocalFile


def _write(base, resource_type, resource_id, payload):
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{resource_type}.{resource_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_get_single_list_payload_returns_none(tmp_path):
    src = tmp_path / "source"
    dst = tmp_path / "destination"
    _write(src, "dashboards", "abc", [{"id": "abc"}])
    backend = LocalFile(
        source_resources_path=str(src), destination_resources_path=str(dst)
    )
    assert backend.get_single("dashboards", "abc") == (None, None)


def test_get_single_scalar_payload_returns_none(tmp_path):
    src = tmp_path / "source"
    dst = tmp_path / "destination"
    _write(dst, "monitors", "42", 42)
    backend = LocalFile(
        source_resources_path=str(src), destination_resources_path=str(dst)
    )
    assert backend.get_single("monitors", "42") == (None, None)


def test_get_single_object_payload_still_reads(tmp_path):
    src = tmp_path / "source"
    dst = tmp_path / "destination"
    _write(src, "dashboards", "abc", {"abc": {"id": "abc", "title": "t"}})
    backend = LocalFile(
        source_resources_path=str(src), destination_resources_path=str(dst)
    )
    assert backend.get_single("dashboards", "abc") == ({"id": "abc", "title": "t"}, None)
