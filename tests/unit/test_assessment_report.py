# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for import --create-assessment-report-app."""

import asyncio
import json
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from datadog_sync.cli import cli
from datadog_sync.utils import assessment_report
from datadog_sync.utils.assessment_report import (
    ImportTypeSummary,
    app_url_from_api_url,
    build_app,
    build_report,
    collect_manual_resources,
    covered_rows,
    create_assessment_report,
    org_label,
    site_from_api_url,
    upsert_app,
)
from datadog_sync.utils.custom_client import CustomClient, PaginationConfig, paginated_truncations
from datadog_sync.utils.resource_utils import CustomClientHTTPError
from datadog_sync.utils.resources_handler import ResourcesHandler


def _http_err(status: int) -> CustomClientHTTPError:
    resp = MagicMock()
    resp.status = status
    resp.message = "error"
    return CustomClientHTTPError(resp, message="body")


def _report(resources):
    return build_report("src-org", "dest-org", "datadoghq.com", resources, datetime(2026, 11, 11, tzinfo=timezone.utc))


class TestCoveredRows:
    def test_uses_import_counts_and_outcomes(self):
        summaries = {
            "monitors": ImportTypeSummary(listed=35, outcomes=Counter(success=33, failure=2)),
            "dashboards": ImportTypeSummary(listed=14, outcomes=Counter(success=14)),
        }
        rows = covered_rows(["monitors", "dashboards"], summaries)

        assert [r["name"] for r in rows] == ["Dashboards", "Monitors"]
        monitors = rows[1]
        assert monitors["covered"] is True
        assert monitors["count"] == 35
        assert monitors["status"] == "OK"
        assert monitors["notes"] == "33 imported, 2 failed"

    def test_list_error_marks_row_as_error(self):
        rows = covered_rows(["roles"], {"roles": ImportTypeSummary(error="HTTP 401")})
        assert rows[0]["count"] == -1
        assert rows[0]["error"] == "HTTP 401"
        assert rows[0]["status"] == "ERROR"

    def test_unknown_resource_type_gets_fallback_name(self):
        rows = covered_rows(["brand_new_type"], {})
        assert rows[0]["name"] == "Brand New Type"
        assert rows[0]["category"] == "Other"
        assert rows[0]["count"] == 0


class TestManualResources:
    def test_counts_and_sanitized_errors(self):
        client = MagicMock()

        async def get(path, params=None):
            if path == "/api/v2/workflows":
                raise _http_err(403)
            if path == "/api/v1/integration/aws":
                return {"accounts": [{"id": 1}, {"id": 2}]}
            return {"data": []}

        client.get = AsyncMock(side_effect=get)
        rows = {r["name"]: r for r in asyncio.run(collect_manual_resources(client))}

        assert len(rows) == len(assessment_report.MANUAL_RESOURCES)
        assert rows["Workflows"]["error"] == "HTTP 403"
        assert rows["Workflows"]["count"] == -1
        assert rows["AWS Integration Accounts"]["count"] == 2
        assert all(r["covered"] is False for r in rows.values())

    def test_cursor_pagination_follows_next_cursor(self):
        client = MagicMock()
        pages = [
            {"data": [{}] * 100, "meta": {"pagination": {"next_cursor": "c2"}}},
            {"data": [{}] * 5, "meta": {"pagination": {}}},
        ]
        client.get = AsyncMock(side_effect=pages)
        assert asyncio.run(assessment_report._count_cursor(client, "/api/v2/incidents")) == 105
        assert client.get.call_args_list[1].kwargs["params"]["page[cursor]"] == "c2"


def _cells(app):
    return app["components"][0]["properties"]["children"]


def _components(app):
    return {c["properties"]["children"][0]["name"]: c for c in _cells(app)}


def _query_rows(app, name):
    query = next(q for q in app["queries"] if q["name"] == name)
    return json.loads(query["properties"]["outputs"][2:-1])


class TestApp:
    ROWS = [
        {"name": "Monitors", "category": "M", "covered": True, "count": 35, "error": None, "notes": "35 imported"},
        {"name": "Powerpacks", "category": "D", "covered": True, "count": 0, "error": None, "notes": ""},
        {"name": "Notebooks", "category": "D", "covered": True, "count": 4, "error": None, "notes": ""},
        {"name": "Cases", "category": "I", "covered": False, "count": 6, "error": None, "notes": ""},
        {"name": "Roles", "category": "U", "covered": True, "count": -1, "error": "HTTP 401", "notes": ""},
        {"name": "Workflows", "category": "W", "covered": False, "count": -1, "error": "HTTP 403", "notes": ""},
    ]

    def test_matches_reference_app_structure(self):
        app = build_app(_report(self.ROWS))

        assert app["name"] == "Migration Assessment: src-org → dest-org"
        assert app["rootInstanceName"] == "grid0"
        assert app["components"][0]["type"] == "grid"
        assert [c["name"] for c in _cells(app)] == [f"gridCell{i}" for i in range(12)]
        assert list(_components(app)) == [
            "header",
            "card0",
            "card1",
            "card2",
            "card3",
            "title_covered",
            "table_covered",
            "title_manual",
            "table_manual",
            "title_errors",
            "table_errors",
            "footer",
        ]
        assert [(q["name"], q["type"]) for q in app["queries"]] == [
            ("coveredRows", "dataTransform"),
            ("manualRows", "dataTransform"),
            ("errorRows", "dataTransform"),
        ]
        assert all(uuid.UUID(q["id"]) for q in app["queries"])

    def test_summary_cards(self):
        cells = _components(build_app(_report(self.ROWS)))
        contents = [cells[f"card{i}"]["properties"]["children"][0]["properties"]["content"] for i in range(4)]
        assert contents[0] == "##### ✅ sync-cli resources\n# 2\n39 total objects"
        assert contents[1] == "##### ⚠️ Manual migration\n# 1\n6 total objects"
        assert contents[2] == "##### ◻️ Empty resources\n# 1\nresource types with 0 objects"
        assert contents[3].startswith("##### ⛔ API errors\n# 2\n")

    def test_tables_bind_to_their_queries(self):
        app = build_app(_report(self.ROWS))
        cells = _components(app)
        covered = cells["table_covered"]["properties"]["children"][0]["properties"]
        errors = cells["table_errors"]["properties"]["children"][0]["properties"]

        assert covered["data"] == "${coveredRows?.outputs}"
        assert [c["header"] for c in covered["columns"]] == ["Resource", "Category", "Count", "Notes"]
        assert covered["pageSize"] == 3
        assert errors["data"] == "${errorRows?.outputs}"
        assert [c["header"] for c in errors["columns"]] == ["Resource", "Category", "Tool", "Error"]

        assert _query_rows(app, "coveredRows") == [
            {"resource": "Monitors", "category": "M", "count": 35, "notes": "35 imported"},
            {"resource": "Notebooks", "category": "D", "count": 4, "notes": ""},
            {"resource": "Powerpacks", "category": "D", "count": 0, "notes": ""},
        ]
        assert _query_rows(app, "errorRows") == [
            {"resource": "Roles", "category": "U", "tool": "sync-cli", "error": "HTTP 401"},
            {"resource": "Workflows", "category": "W", "tool": "manual", "error": "HTTP 403"},
        ]

    def test_layout_stacks_without_overlap(self):
        rows = [
            {"name": f"R{i}", "category": "C", "covered": i % 2 == 0, "count": i, "error": None, "notes": ""}
            for i in range(60)
        ]
        layouts = [c["properties"]["layout"]["default"] for c in _cells(build_app(_report(rows)))]
        assert layouts[0] == {"height": 7, "width": 12, "x": 0, "y": 0}
        assert [lay["x"] for lay in layouts[1:5]] == [0, 3, 6, 9]
        assert all(lay["y"] == 7 and lay["height"] == 9 for lay in layouts[1:5])
        sections = layouts[5:]
        assert sections[0]["y"] == 16
        for above, below in zip(sections, sections[1:]):
            assert above["y"] + above["height"] == below["y"]
        assert layouts[6]["height"] == 4 * 30 + 4

    def test_app_is_stable_across_runs(self):
        assert build_app(_report(self.ROWS)) == build_app(_report(self.ROWS))


class TestUpsertApp:
    NAME = "Migration Assessment: src-org → dest-org"

    def test_creates_when_missing(self):
        client = MagicMock()
        client.get = AsyncMock(return_value={"data": [{"id": "x", "attributes": {"name": f"{self.NAME} (old)"}}]})
        client.post = AsyncMock(return_value={"data": {"id": "new-id", "type": "appDefinitions"}})
        client.patch = AsyncMock()

        app_id, created = asyncio.run(upsert_app(client, {"name": self.NAME}))

        assert (app_id, created) == ("new-id", True)
        assert client.get.call_args.kwargs["params"]["filter[name]"] == self.NAME
        client.post.assert_awaited_once_with(
            "/api/v2/app-builder/apps", {"data": {"type": "appDefinitions", "attributes": {"name": self.NAME}}}
        )
        client.patch.assert_not_called()

    def test_overwrites_existing_app_with_same_name(self):
        client = MagicMock()
        client.get = AsyncMock(return_value={"data": [{"id": "app-1", "attributes": {"name": self.NAME}}]})
        client.post = AsyncMock()
        client.patch = AsyncMock(return_value={"data": {"id": "app-1"}})

        app_id, created = asyncio.run(upsert_app(client, {"name": self.NAME}))

        assert (app_id, created) == ("app-1", False)
        client.patch.assert_awaited_once_with(
            "/api/v2/app-builder/apps/app-1",
            {"data": {"type": "appDefinitions", "id": "app-1", "attributes": {"name": self.NAME}}},
        )
        client.post.assert_not_called()


class TestCreateAssessmentReport:
    def _config(self):
        config = MagicMock()
        config.resources_arg = ["monitors"]
        config.source_client.url_object._default = "https://api.datadoghq.com"
        config.destination_client.url_object._default = "https://api.us5.datadoghq.com"
        config.source_org_label = None
        config.destination_org_label = None
        return config

    def _run(self, config, summaries, upsert, publish=None):
        with patch.object(assessment_report, "collect_manual_resources", AsyncMock(return_value=[])), patch.object(
            assessment_report, "org_label", AsyncMock(side_effect=["src-org", "dest-org"])
        ), patch.object(assessment_report, "upsert_app", upsert), patch.object(
            assessment_report, "publish_app", publish or AsyncMock()
        ) as publish_mock:
            asyncio.run(create_assessment_report(config, summaries))
        return publish_mock

    def test_writes_json_even_when_app_write_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = self._config()
        publish = self._run(config, {"monitors": ImportTypeSummary(listed=3)}, AsyncMock(side_effect=_http_err(403)))

        report = json.loads((tmp_path / assessment_report.REPORT_FILE_NAME).read_text())
        assert report["source"] == "src-org"
        assert report["destination"] == "dest-org"
        assert report["site"] == "datadoghq.com"
        assert report["resources"][0]["count"] == 3
        config.logger.error.assert_called_once()
        publish.assert_not_called()

    def test_logs_app_url_and_publishes(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = self._config()
        publish = self._run(config, {}, AsyncMock(return_value=("app-1", True)))

        messages = [c.args[0] for c in config.logger.info.call_args_list]
        assert "created assessment report app: https://us5.datadoghq.com/app-builder/apps/app-1" in messages
        assert "published the assessment report app" in messages
        publish.assert_awaited_once_with(config.destination_client, "app-1")

    def test_publish_failure_is_a_warning(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = self._config()
        self._run(config, {}, AsyncMock(return_value=("app-1", False)), publish=AsyncMock(side_effect=_http_err(403)))

        config.logger.error.assert_not_called()
        config.logger.warning.assert_called_once()
        messages = [c.args[0] for c in config.logger.info.call_args_list]
        assert "overwrote assessment report app: https://us5.datadoghq.com/app-builder/apps/app-1" in messages

    def test_labels_override_org_lookup(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = self._config()
        config.source_org_label = "plutofood Production"
        config.destination_org_label = "galaxy2404 Production"
        upsert = AsyncMock(return_value=("app-1", False))
        self._run(config, {}, upsert)

        assert upsert.call_args.args[1]["name"] == "Migration Assessment: plutofood Production → galaxy2404 Production"


class TestOrgLabel:
    def _client(self, responses):
        client = MagicMock()
        client.url_object._default = "https://api.datadoghq.com"
        client.get = AsyncMock(side_effect=responses)
        return client

    def test_uses_org_endpoint(self):
        client = self._client([{"orgs": [{"name": "galaxy2404"}]}])
        assert asyncio.run(org_label(client)) == "galaxy2404"

    def test_falls_back_to_current_user_org(self):
        current_user = {
            "data": {},
            "included": [{"type": "roles"}, {"type": "orgs", "attributes": {"name": "plutofood"}}],
        }
        client = self._client([_http_err(403), current_user])
        assert asyncio.run(org_label(client)) == "plutofood"
        assert client.get.call_args.args[0] == "/api/v2/current_user"

    def test_falls_back_to_site(self):
        client = self._client([_http_err(403), _http_err(403)])
        assert asyncio.run(org_label(client)) == "datadoghq.com"

    def test_cli_reads_label_env_vars(self, monkeypatch):
        captured = {}
        import_module = sys.modules["datadog_sync.commands._import"]
        monkeypatch.setattr(import_module, "run_cmd", lambda cmd, **kw: captured.update(kw))
        result = CliRunner().invoke(
            cli,
            ["import"],
            env={"DD_SOURCE_LABEL": "plutofood Production", "DD_DEST_LABEL": "galaxy2404 Production"},
        )
        assert result.exit_code == 0, result.output
        assert captured["source_org_label"] == "plutofood Production"
        assert captured["destination_org_label"] == "galaxy2404 Production"


class TestUrls:
    @pytest.mark.parametrize(
        "api_url,site,app_url",
        [
            ("https://api.datadoghq.com", "datadoghq.com", "https://app.datadoghq.com"),
            ("https://api.datadoghq.eu", "datadoghq.eu", "https://app.datadoghq.eu"),
            ("https://api.us5.datadoghq.com", "us5.datadoghq.com", "https://us5.datadoghq.com"),
            ("https://api.ap1.datadoghq.com", "ap1.datadoghq.com", "https://ap1.datadoghq.com"),
        ],
    )
    def test_site_and_app_url(self, api_url, site, app_url):
        assert site_from_api_url(api_url) == site
        assert app_url_from_api_url(api_url) == app_url


class TestImportTracking:
    def test_paginated_truncation_is_reported_to_context(self):
        client = CustomClient("https://api.datadoghq.com", {}, 60, 60, False)
        fetch = AsyncMock(side_effect=_http_err(401))

        async def run():
            sink = []
            token = paginated_truncations.set(sink)
            try:
                resources = await client.paginated_request(fetch)("/api/v2/roles", pagination_config=PaginationConfig())
            finally:
                paginated_truncations.reset(token)
            return resources, sink

        resources, sink = asyncio.run(run())
        assert resources == []
        assert sink == ["HTTP 401"]

    def test_handler_records_listed_count_and_truncation_error(self):
        config = MagicMock()
        config.id_payload = None
        r_class = MagicMock()

        async def get_resources(_client):
            paginated_truncations.get().append("HTTP 401")
            return []

        r_class._get_resources = AsyncMock(side_effect=get_resources)
        config.resources = {"roles": r_class, "monitors": MagicMock()}
        config.resources["monitors"]._get_resources = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
        handler = ResourcesHandler(config)
        handler.worker = MagicMock()

        tmp_storage = defaultdict(list)
        asyncio.run(handler._import_get_resources_cb("roles", tmp_storage))
        asyncio.run(handler._import_get_resources_cb("monitors", tmp_storage))

        assert handler.import_summaries["roles"].error == "HTTP 401"
        assert handler.import_summaries["monitors"].listed == 2
        assert handler.import_summaries["monitors"].error == ""
        assert paginated_truncations.get() is None

    def test_emit_counts_per_item_import_outcomes(self):
        config = MagicMock()
        config.emit_json = False
        handler = ResourcesHandler(config)
        handler._emit("monitors", "1", "import", "success")
        handler._emit("monitors", "2", "import", "failure")
        handler._emit("monitors", "", "import", "failure")
        handler._emit("monitors", "3", "sync", "success")

        assert handler.import_summaries["monitors"].outcomes == Counter(success=1, failure=1)


def test_read_only_rejects_assessment_report():
    result = CliRunner(mix_stderr=False).invoke(cli, ["--read-only", "import", "--create-assessment-report-app"])
    assert result.exit_code == 2
    assert "--create-assessment-report-app" in result.stderr
