# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from pathlib import Path

import pytest

from datadog_sync.constants import Origin
from datadog_sync.utils.storage._base_storage import BaseStorage, StorageData
from datadog_sync.utils.storage.local_file import LocalFile


class TestSanitizeIdForFilename:
    def test_sanitize_id_with_colon(self):
        assert BaseStorage._sanitize_id_for_filename("abc:123") == "abc.123"

    def test_sanitize_id_multiple_colons(self):
        assert BaseStorage._sanitize_id_for_filename("a:b:c") == "a.b.c"

    def test_sanitize_id_no_colons(self):
        assert BaseStorage._sanitize_id_for_filename("abc-123_def") == "abc-123_def"

    def test_sanitize_id_empty(self):
        assert BaseStorage._sanitize_id_for_filename("") == ""

    def test_sanitize_id_non_string_raises(self):
        with pytest.raises(AttributeError):
            BaseStorage._sanitize_id_for_filename(None)

    def test_all_backends_produce_same_sanitized_segment(self):
        resource_id = "resource:123"
        expected_segment = "resource.123"
        assert BaseStorage._sanitize_id_for_filename(resource_id) == expected_segment

    def test_sanitize_id_uuid_unchanged(self):
        """UUIDs (most Datadog resource IDs) are not modified."""
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        assert BaseStorage._sanitize_id_for_filename(uuid) == uuid

    def test_sanitize_id_dots_unchanged(self):
        """Dots in IDs are preserved — only colons are replaced."""
        assert BaseStorage._sanitize_id_for_filename("abc.def") == "abc.def"


class TestRoundTripColonId:
    def test_round_trip_colon_id_put_then_get_single(self, tmp_path):
        """Round-trip: put() writes sanitized filename; get_single() reads back via original ID."""
        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()

        backend = LocalFile(
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            resource_per_file=True,
        )
        data = StorageData()
        data.source["dashboards"]["dash:123"] = {"id": "dash:123", "title": "Test"}
        backend.put(Origin.SOURCE, data)

        # Filename must use sanitized ID
        files = list(Path(src_path).iterdir())
        assert any("dash.123" in f.name for f in files), "Sanitized filename expected"
        assert not any("dash:123" in f.name for f in files), "Unsanitized filename must not exist"

        # get_single() must return content using original (unsanitized) ID
        src, dst = backend.get_single("dashboards", "dash:123")
        assert src == {"id": "dash:123", "title": "Test"}
        assert dst is None

    def test_round_trip_no_colon_id_unchanged(self, tmp_path):
        """IDs without colons are unaffected by sanitization."""
        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()

        backend = LocalFile(
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            resource_per_file=True,
        )
        data = StorageData()
        data.source["monitors"]["12345"] = {"id": "12345", "name": "My Monitor"}
        backend.put(Origin.SOURCE, data)

        files = list(Path(src_path).iterdir())
        assert any("monitors.12345.json" == f.name for f in files)

        src, dst = backend.get_single("monitors", "12345")
        assert src == {"id": "12345", "name": "My Monitor"}
