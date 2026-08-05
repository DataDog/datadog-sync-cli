# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for PR 3: ID-targeted loading and targeted dependency loading."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from datadog_sync.constants import Origin
from datadog_sync.utils.storage._base_storage import StorageData
from datadog_sync.utils.storage.local_file import LocalFile
from datadog_sync.utils.storage.storage_types import StorageType


# ─── Helpers ────────────────────────────────────────────────────────────────


def make_exact_id_filter(resource_type: str, resource_id: str):
    """Build a Filter object matching ExactMatch id=resource_id.

    Mirrors process_filters() behavior: ExactMatch wraps value as ^...$
    without re.escape, since IDs are literal strings passed directly.
    """
    import re
    from datadog_sync.utils.filter import Filter

    return Filter(
        resource_type=resource_type,
        attr_name="id",
        attr_re=re.compile(f"^{resource_id}$"),
        operator="exactmatch",
    )


def make_title_filter(resource_type: str, title_value: str):
    """Build a Filter object matching by title (non-ID field)."""
    import re
    from datadog_sync.utils.filter import Filter

    return Filter(
        resource_type=resource_type,
        attr_name="title",
        attr_re=re.compile(f"^{title_value}$"),
        operator="exactmatch",
    )


# ─── extract_exact_id_filters ───────────────────────────────────────────────


class TestExtractExactIdFilters:
    def test_happy_path_single_type(self):
        from datadog_sync.utils.configuration import extract_exact_id_filters

        filters = {
            "dashboards": [make_exact_id_filter("dashboards", "dash-1"), make_exact_id_filter("dashboards", "dash-2")]
        }
        result = extract_exact_id_filters(filters, "or", ["dashboards"])
        assert result == {"dashboards": ["dash-1", "dash-2"]}

    def test_non_id_field_returns_none(self):
        from datadog_sync.utils.configuration import extract_exact_id_filters

        filters = {"dashboards": [make_title_filter("dashboards", "My Dashboard")]}
        assert extract_exact_id_filters(filters, "or", ["dashboards"]) is None

    def test_and_operator_returns_none(self):
        from datadog_sync.utils.configuration import extract_exact_id_filters

        filters = {"dashboards": [make_exact_id_filter("dashboards", "dash-1")]}
        assert extract_exact_id_filters(filters, "and", ["dashboards"]) is None

    def test_no_filters_returns_none(self):
        from datadog_sync.utils.configuration import extract_exact_id_filters

        assert extract_exact_id_filters({}, "or", ["dashboards"]) is None

    def test_missing_type_in_filters_returns_none(self):
        """If --resources=dashboards,monitors but only dashboard filters → fallback."""
        from datadog_sync.utils.configuration import extract_exact_id_filters

        filters = {"dashboards": [make_exact_id_filter("dashboards", "dash-1")]}
        # monitors has no filters → can't use ID-targeted for both types
        assert extract_exact_id_filters(filters, "or", ["dashboards", "monitors"]) is None

    def test_or_operator_case_insensitive(self):
        from datadog_sync.utils.configuration import extract_exact_id_filters

        filters = {"roles": [make_exact_id_filter("roles", "role-1")]}
        result = extract_exact_id_filters(filters, "OR", ["roles"])
        assert result == {"roles": ["role-1"]}

    def test_end_to_end_through_process_filters(self):
        """extract_exact_id_filters works end-to-end with process_filters output."""
        from datadog_sync.utils.configuration import extract_exact_id_filters
        from datadog_sync.utils.filter import process_filters

        filters = process_filters(
            [
                "Type=dashboards;Name=id;Value=dash-1;Operator=ExactMatch",
                "Type=dashboards;Name=id;Value=dash-2;Operator=ExactMatch",
            ]
        )
        result = extract_exact_id_filters(filters, "or", ["dashboards"])
        assert result == {"dashboards": ["dash-1", "dash-2"]}


# ─── Regex-escaped ExactMatch values (regexp.QuoteMeta / re.escape) ──────────


class TestExactMatchEscapedValues:
    """Producers that regex-escape the filter Value before ExactMatch wrapping.

    ExactMatch wraps the Value in ``^...$`` and compiles it as a regex, so a
    caller must escape regex metacharacters (e.g. Go ``regexp.QuoteMeta`` or
    Python ``re.escape``) for the match to be literal — otherwise a metric id
    like ``svc.request.count`` would also match ``svcXrequestYcount``. The
    ID-targeted state load then reuses the Value as a literal storage key, so it
    must recover the unescaped literal id, not the escaped pattern body.
    """

    def test_unwrap_recovers_literal_from_escaped_dots(self):
        from datadog_sync.utils.configuration import _unwrap_exact_match_pattern

        # What process_filters stores when the producer escaped dots.
        pattern = r"^svc\.request\.count$"
        assert _unwrap_exact_match_pattern(pattern) == "svc.request.count"

    def test_unwrap_plain_literal_unchanged(self):
        from datadog_sync.utils.configuration import _unwrap_exact_match_pattern

        # UUID-style id with no metacharacters: escaping is a no-op upstream.
        assert _unwrap_exact_match_pattern("^dash-1$") == "dash-1"

    def test_unwrap_recovers_literal_backslash(self):
        from datadog_sync.utils.configuration import _unwrap_exact_match_pattern

        assert _unwrap_exact_match_pattern(r"^metric\\dimension$") == r"metric\dimension"

    def test_unwrap_real_regex_raises(self):
        """An unescaped metacharacter means a genuine regex, not an escaped id."""
        from datadog_sync.utils.configuration import _unwrap_exact_match_pattern

        with pytest.raises(ValueError):
            _unwrap_exact_match_pattern("^svc.*count$")

    def test_semantic_regex_escape_falls_back(self):
        """A regex escape such as ``\\d`` must not become the literal id ``d``."""
        from datadog_sync.utils.configuration import extract_exact_id_filters
        from datadog_sync.utils.filter import process_filters

        filters = process_filters([r"Type=logs_metrics;Name=id;Value=\d;Operator=ExactMatch"])

        assert filters["logs_metrics"][0].attr_re.fullmatch("7")
        assert not filters["logs_metrics"][0].attr_re.fullmatch("d")
        assert extract_exact_id_filters(filters, "or", ["logs_metrics"]) is None

    def test_extract_exact_id_filters_escaped_metric_ids(self):
        """Regression: a producer regex-escapes dotted logs_metrics ids.

        Reproduces the drop where dotted ids vanished at ID-targeted state load
        because the escaped pattern body (``svc\\.request\\.count``) was used as
        a literal storage key and never matched the real blob key
        (``svc.request.count``). Only the dot-free id survived unfixed.
        """
        import re
        from datadog_sync.utils.configuration import extract_exact_id_filters
        from datadog_sync.utils.filter import Filter

        raw_ids = [
            "metric-no-dots",  # no metacharacters — survives even unfixed
            "svc.request.count",
            "app.errors.total.count",
        ]
        # Mirror the producer: regex-escape each id, then ExactMatch-wrap it.
        rt_filters = [
            Filter(
                resource_type="logs_metrics",
                attr_name="id",
                attr_re=re.compile(f"^{re.escape(_id)}$"),
                operator="exactmatch",
            )
            for _id in raw_ids
        ]
        result = extract_exact_id_filters({"logs_metrics": rt_filters}, "or", ["logs_metrics"])
        assert result == {"logs_metrics": raw_ids}

    def test_escaped_id_load_end_to_end(self, tmp_path):
        """State.get_by_ids resolves an escaped-value id against the real blob key."""
        import re
        from datadog_sync.utils.configuration import extract_exact_id_filters
        from datadog_sync.utils.filter import Filter
        from datadog_sync.utils.state import State

        _id = "svc.request.count"
        src = tmp_path / "source"
        src.mkdir()
        # Import writes one blob per resource, keyed by the literal (unescaped) id.
        (src / f"logs_metrics.{_id}.json").write_text(json.dumps({_id: {"id": _id, "attributes": {}}}))

        rt_filter = Filter(
            resource_type="logs_metrics",
            attr_name="id",
            attr_re=re.compile(f"^{re.escape(_id)}$"),
            operator="exactmatch",
        )
        exact_ids = extract_exact_id_filters({"logs_metrics": [rt_filter]}, "or", ["logs_metrics"])

        state = State(
            StorageType.LOCAL_FILE,
            source_resources_path=str(src),
            destination_resources_path=str(tmp_path / "destination"),
            resource_per_file=True,
            exact_ids=exact_ids,
        )
        assert _id in state.source["logs_metrics"]


# ─── State with exact_ids ───────────────────────────────────────────────────


class TestStateExactIdLoading:
    def test_state_loads_exact_ids_without_listing(self, tmp_path):
        """With exact_ids, only specific files are fetched — no directory listing."""
        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()

        backend = LocalFile(source_resources_path=src_path, destination_resources_path=dst_path, resource_per_file=True)
        data = StorageData()
        data.source["dashboards"]["dash-1"] = {"id": "dash-1"}
        data.source["dashboards"]["dash-2"] = {"id": "dash-2"}
        data.source["monitors"]["mon-1"] = {"id": "mon-1"}
        backend.put(Origin.SOURCE, data)

        # get_by_ids should only load dash-1, not dash-2 or mon-1
        result = backend.get_by_ids(Origin.SOURCE, {"dashboards": ["dash-1"]})
        assert "dash-1" in result.source["dashboards"]
        assert "dash-2" not in result.source["dashboards"]
        assert "mon-1" not in result.source["monitors"]

    def test_state_minimize_reads_true_with_exact_ids(self, tmp_path):
        """State._minimize_reads is True when exact_ids is set."""
        from datadog_sync.utils.state import State

        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()

        state = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            exact_ids={"dashboards": ["dash-1"]},
            resource_per_file=True,
        )
        assert state._minimize_reads is True

    def test_state_uses_get_by_ids_when_exact_ids_set(self, tmp_path):
        """State.load_state() calls get_by_ids (not get) when exact_ids is set."""
        from datadog_sync.utils.state import State

        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()

        # Write a file
        backend = LocalFile(source_resources_path=src_path, destination_resources_path=dst_path, resource_per_file=True)
        data = StorageData()
        data.source["dashboards"]["dash-1"] = {"id": "dash-1"}
        backend.put(Origin.SOURCE, data)

        state = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            exact_ids={"dashboards": ["dash-1"]},
            resource_per_file=True,
        )
        assert "dash-1" in state.source["dashboards"]


# ─── ensure_resource_loaded ─────────────────────────────────────────────────


class TestEnsureResourceLoaded:
    def _make_state_with_exact_ids(self, tmp_path):
        from datadog_sync.utils.state import State

        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()
        return State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            exact_ids={"dashboards": ["dash-1"]},
            resource_per_file=True,
        )

    def test_ensure_resource_loaded_fetches_both_src_and_dst(self, tmp_path):
        """ensure_resource_loaded loads both source and destination state."""
        from datadog_sync.utils.state import State

        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()

        # Write a monitor to both source and destination
        backend = LocalFile(source_resources_path=src_path, destination_resources_path=dst_path, resource_per_file=True)
        data = StorageData()
        data.source["monitors"]["mon-1"] = {"id": "mon-1", "name": "SrcMonitor"}
        data.destination["monitors"]["mon-1"] = {"id": "mon-1", "name": "DstMonitor"}
        backend.put(Origin.ALL, data)

        state = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            exact_ids={"dashboards": []},
            resource_per_file=True,
        )

        state.ensure_resource_loaded("monitors", "mon-1")
        assert state.source["monitors"]["mon-1"] == {"id": "mon-1", "name": "SrcMonitor"}
        assert state.destination["monitors"]["mon-1"] == {"id": "mon-1", "name": "DstMonitor"}

    def test_ensure_resource_loaded_skips_if_already_present(self, tmp_path):
        """ensure_resource_loaded is idempotent — skips if already in state."""
        state = self._make_state_with_exact_ids(tmp_path)
        sentinel = {"id": "mon-1", "already": "loaded"}
        state._data.source["monitors"]["mon-1"] = sentinel
        # Call ensure — should not overwrite
        state.ensure_resource_loaded("monitors", "mon-1")
        assert state._data.source["monitors"]["mon-1"] is sentinel

    def test_ensure_resource_loaded_noop_when_not_minimize_reads(self, tmp_path):
        """ensure_resource_loaded is a no-op when not in minimize-reads mode."""
        from datadog_sync.utils.state import State

        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()

        state = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=src_path,
            destination_resources_path=dst_path,
        )
        # No-op: minimize_reads is False
        state.ensure_resource_loaded("monitors", "nonexistent")
        assert "nonexistent" not in state._data.source["monitors"]

    def test_ensure_resource_loaded_handles_missing_gracefully(self, tmp_path):
        """Missing resource: state is unchanged for that ID."""
        state = self._make_state_with_exact_ids(tmp_path)
        state.ensure_resource_loaded("monitors", "nonexistent-id")
        assert "nonexistent-id" not in state._data.source["monitors"]

    def test_ensure_resource_loaded_partial_backend_failure(self, tmp_path):
        """Source loaded but destination missing: src populated, dst absent."""
        from datadog_sync.utils.state import State

        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()

        # Only write source, not destination
        backend = LocalFile(source_resources_path=src_path, destination_resources_path=dst_path, resource_per_file=True)
        data = StorageData()
        data.source["monitors"]["mon-1"] = {"id": "mon-1"}
        backend.put(Origin.SOURCE, data)  # only source

        state = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            exact_ids={"dashboards": []},
            resource_per_file=True,
        )
        state.ensure_resource_loaded("monitors", "mon-1")
        assert "mon-1" in state.source["monitors"]
        assert "mon-1" not in state.destination["monitors"]

    def test_ensure_resource_loaded_repeated_miss_does_not_refetch(self, tmp_path):
        """Missing dependency: get_single called only once despite repeated calls."""
        from datadog_sync.utils.state import State

        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()

        state = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=src_path,
            destination_resources_path=dst_path,
            exact_ids={"dashboards": []},
            resource_per_file=True,
        )
        with patch.object(state._storage, "get_single", return_value=(None, None)) as mock:
            state.ensure_resource_loaded("monitors", "never-exists")
            state.ensure_resource_loaded("monitors", "never-exists")
            state.ensure_resource_loaded("monitors", "never-exists")
        assert mock.call_count == 1


# ─── get_single NotFound handling ───────────────────────────────────────────


class TestGetSingleNotFound:
    def test_s3_get_single_returns_none_for_nosuchkey(self):
        """S3 NoSuchKey → (None, None), not an exception."""
        with patch("datadog_sync.utils.storage.aws_s3_bucket.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client
            mock_client.get_object.side_effect = ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "The key does not exist"}},
                "GetObject",
            )
            from datadog_sync.utils.storage.aws_s3_bucket import AWSS3Bucket

            backend = AWSS3Bucket(
                config={
                    "aws_bucket_name": "test-bucket",
                    "aws_region_name": "us-east-1",
                    "aws_access_key_id": "",
                    "aws_secret_access_key": "",
                    "aws_session_token": "",
                },
            )
            src, dst = backend.get_single("dashboards", "nonexistent")
            assert src is None
            assert dst is None

    def test_localfile_get_single_returns_none_for_missing_file(self, tmp_path):
        """LocalFile get_single returns (None, None) when file doesn't exist."""
        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()
        backend = LocalFile(source_resources_path=src_path, destination_resources_path=dst_path, resource_per_file=True)
        src, dst = backend.get_single("dashboards", "nonexistent-id")
        assert src is None
        assert dst is None


# ─── get_by_ids partial match ───────────────────────────────────────────────


class TestGetByIdsPartialMatch:
    def test_get_by_ids_partial_match_localfile(self, tmp_path):
        """get_by_ids() returns only found IDs — missing IDs are silently skipped."""
        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()

        backend = LocalFile(source_resources_path=src_path, destination_resources_path=dst_path, resource_per_file=True)
        data = StorageData()
        data.source["dashboards"]["dash-1"] = {"id": "dash-1"}
        backend.put(Origin.SOURCE, data)

        # dash-1 exists, dash-2 doesn't
        result = backend.get_by_ids(Origin.SOURCE, {"dashboards": ["dash-1", "dash-2"]})
        assert "dash-1" in result.source["dashboards"]
        assert "dash-2" not in result.source["dashboards"]

    def test_s3_get_by_ids_partial_match(self):
        """S3 get_by_ids() gracefully skips IDs that produce NoSuchKey."""
        with patch("datadog_sync.utils.storage.aws_s3_bucket.boto3") as mock_boto3:
            mock_client = MagicMock()
            mock_boto3.client.return_value = mock_client

            # dash-1 exists, dash-2 → NoSuchKey
            nosuchkey = ClientError({"Error": {"Code": "NoSuchKey", "Message": ""}}, "GetObject")

            def get_object_side_effect(**kwargs):
                key = kwargs["Key"]
                if "dash-1" in key:
                    body = MagicMock()
                    body.read.return_value = json.dumps({"dash-1": {"id": "dash-1"}}).encode()
                    import io

                    return {"Body": io.BytesIO(json.dumps({"dash-1": {"id": "dash-1"}}).encode())}
                raise nosuchkey

            mock_client.get_object.side_effect = get_object_side_effect

            from datadog_sync.utils.storage.aws_s3_bucket import AWSS3Bucket

            backend = AWSS3Bucket(
                config={
                    "aws_bucket_name": "test-bucket",
                    "aws_region_name": "us-east-1",
                    "aws_access_key_id": "",
                    "aws_secret_access_key": "",
                    "aws_session_token": "",
                },
                resource_per_file=True,
            )

            result = backend.get_by_ids(Origin.SOURCE, {"dashboards": ["dash-1", "dash-2"]})
            assert "dash-1" in result.source["dashboards"]
            assert "dash-2" not in result.source["dashboards"]


# ─── Backward compatibility ──────────────────────────────────────────────────


class TestBackwardCompatibility:
    def test_full_load_unchanged_without_minimize_reads(self, tmp_path):
        """State without exact_ids or resource_types loads everything."""
        from datadog_sync.utils.state import State

        src_path = str(tmp_path / "source")
        dst_path = str(tmp_path / "dest")
        Path(src_path).mkdir()
        Path(dst_path).mkdir()

        backend = LocalFile(source_resources_path=src_path, destination_resources_path=dst_path, resource_per_file=True)
        data = StorageData()
        data.source["dashboards"]["dash-1"] = {"id": "dash-1"}
        data.source["monitors"]["mon-1"] = {"id": "mon-1"}
        backend.put(Origin.SOURCE, data)

        state = State(
            type_=StorageType.LOCAL_FILE,
            source_resources_path=src_path,
            destination_resources_path=dst_path,
        )
        assert state._minimize_reads is False
        assert "dash-1" in state.source["dashboards"]
        assert "mon-1" in state.source["monitors"]
