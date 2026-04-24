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


class TestIdCollisionDetection:
    def test_collision_is_logged_as_error(self, tmp_path, caplog):
        """Two IDs that differ only by ':' vs '.' collide on the same filename and trigger an error log."""
        import logging

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
        data.source["monitors"]["foo:bar"] = {"id": "foo:bar"}
        data.source["monitors"]["foo.bar"] = {"id": "foo.bar"}

        with caplog.at_level(logging.ERROR):
            backend.put(Origin.SOURCE, data)

        assert any(
            "foo:bar" in r.message and "foo.bar" in r.message for r in caplog.records
        ), "Expected a collision error mentioning both conflicting IDs"

    def test_no_collision_no_error(self, tmp_path, caplog):
        """IDs that don't collide produce no error logs."""
        import logging

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
        data.source["monitors"]["abc-123"] = {"id": "abc-123"}
        data.source["monitors"]["def-456"] = {"id": "def-456"}

        with caplog.at_level(logging.ERROR):
            backend.put(Origin.SOURCE, data)

        assert not caplog.records


class TestGetByIdsRequiresResourcePerFile:
    def test_raises_when_resource_per_file_false(self, tmp_path):
        """get_by_ids() must raise ValueError when resource_per_file=False."""
        backend = LocalFile(
            source_resources_path=str(tmp_path / "source"),
            destination_resources_path=str(tmp_path / "dest"),
            resource_per_file=False,
        )
        with pytest.raises(ValueError, match="--resource-per-file"):
            backend.get_by_ids(Origin.SOURCE, {"dashboards": ["123"]})

    def test_succeeds_when_resource_per_file_true(self, tmp_path):
        """get_by_ids() does not raise when resource_per_file=True."""
        src_path = str(tmp_path / "source")
        Path(src_path).mkdir()
        Path(str(tmp_path / "dest")).mkdir()

        backend = LocalFile(
            source_resources_path=src_path,
            destination_resources_path=str(tmp_path / "dest"),
            resource_per_file=True,
        )
        # No files exist — should return empty StorageData without raising
        result = backend.get_by_ids(Origin.SOURCE, {"dashboards": ["123"]})
        assert result.source == {} or "dashboards" not in result.source or result.source["dashboards"] == {}
