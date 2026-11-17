# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
"""Migration assessment report built by ``import --create-assessment-report-app``.

The report lists every resource type in the source org, split into types that
datadog-sync-cli migrates (counts come from the import run) and types that need
manual migration (counted with extra read-only calls to the source org). It is
written to ``REPORT_FILE_NAME`` and rendered as an App Builder app in the
destination org. The app is named after the source and destination orgs, and
an existing app with that name is overwritten with a new version.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from datadog_sync.utils.resource_utils import CustomClientHTTPError

if TYPE_CHECKING:
    from datadog_sync.utils.configuration import Configuration
    from datadog_sync.utils.custom_client import CustomClient


APP_TITLE = "Datadog Migration Assessment"
APPS_PATH = "/api/v2/app-builder/apps"
REPORT_FILE_NAME = "migration_assessment_report.json"


_PAGE_SIZE = 100
_MAX_PAGES = 1000
_MAX_CONCURRENT_COUNTS = 8


@dataclass
class ImportTypeSummary:
    """What one import run saw for a single resource type."""

    listed: int = 0
    error: str = ""
    outcomes: Counter = field(default_factory=Counter)


# resource_type -> (display name, category, notes)
COVERED_RESOURCES: Dict[str, Tuple[str, str, str]] = {
    "dashboards": ("Dashboards", "Dashboards & Notebooks", ""),
    "dashboard_lists": ("Dashboard Lists", "Dashboards & Notebooks", ""),
    "notebooks": ("Notebooks", "Dashboards & Notebooks", ""),
    "powerpacks": ("Powerpacks", "Dashboards & Notebooks", ""),
    "monitors": ("Monitors", "Monitors & Alerting", ""),
    "downtimes": ("Downtimes (legacy v1)", "Monitors & Alerting", ""),
    "downtime_schedules": ("Downtime Schedules (v2)", "Monitors & Alerting", ""),
    "service_level_objectives": ("Service Level Objectives", "SLOs", ""),
    "slo_corrections": ("SLO Corrections", "SLOs", ""),
    "logs_archives": ("Logs Archives", "Logs", ""),
    "logs_archives_order": ("Logs Archives Order", "Logs", "Ordered list"),
    "logs_custom_pipelines": ("Logs Custom Pipelines", "Logs", "Deprecated, use logs_pipelines"),
    "logs_indexes": ("Logs Indexes", "Logs", ""),
    "logs_indexes_order": ("Logs Indexes Order", "Logs", "Ordered list"),
    "logs_pipelines": ("Logs Pipelines (all)", "Logs", "Includes integration pipelines"),
    "logs_pipelines_order": ("Logs Pipelines Order", "Logs", "Ordered list"),
    "logs_metrics": ("Logs Metrics", "Logs", ""),
    "logs_restriction_queries": ("Logs Restriction Queries", "Logs", ""),
    "metric_tag_configurations": ("Metric Tag Configurations", "Metrics", ""),
    "metric_percentiles": ("Metric Percentiles", "Metrics", ""),
    "metrics_metadata": ("Metrics Metadata", "Metrics", ""),
    "spans_metrics": ("Spans Metrics", "Metrics", ""),
    "synthetics_tests": ("Synthetics Tests", "Synthetics", ""),
    "synthetics_test_suites": ("Synthetics Test Suites", "Synthetics", ""),
    "synthetics_global_variables": ("Synthetics Global Variables", "Synthetics", ""),
    "synthetics_private_locations": ("Synthetics Private Locations", "Synthetics", "Custom only"),
    "synthetics_mobile_applications": ("Synthetics Mobile Apps", "Synthetics", ""),
    "synthetics_mobile_applications_versions": ("Synthetics Mobile App Versions", "Synthetics", ""),
    "security_monitoring_rules": ("Security Monitoring Rules", "Security", "Custom rules only"),
    "sensitive_data_scanner_groups": ("Sensitive Data Scanner Groups", "Security", ""),
    "sensitive_data_scanner_groups_order": ("Sensitive Data Scanner Groups Order", "Security", "Ordered list"),
    "sensitive_data_scanner_rules": ("Sensitive Data Scanner Rules", "Security", ""),
    "restriction_policies": ("Restriction Policies", "Security", ""),
    "rum_applications": ("RUM Applications", "RUM", ""),
    "authn_mappings": ("AuthN Mappings", "Users, Teams & Access", ""),
    "roles": ("Roles", "Users, Teams & Access", ""),
    "teams": ("Teams", "Users, Teams & Access", ""),
    "team_memberships": ("Team Memberships", "Users, Teams & Access", ""),
    "users": ("Users", "Users, Teams & Access", ""),
    "host_tags": ("Host Tags", "Infrastructure", ""),
}


# ---------------------------------------------------------------------------
# Counting resources that datadog-sync-cli does not migrate
# ---------------------------------------------------------------------------


def _items(resp, key: str) -> List:
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        value = resp.get(key, [])
        return value if isinstance(value, list) else []
    return []


async def _count_list(client: CustomClient, path: str, key: str = "data") -> int:
    return len(_items(await client.get(path), key))


async def _count_cursor(client: CustomClient, path: str, key: str = "data") -> int:
    total = 0
    cursor = None
    for _ in range(_MAX_PAGES):
        params = {"page[size]": _PAGE_SIZE}
        if cursor:
            params["page[cursor]"] = cursor
        resp = await client.get(path, params=params)
        batch = _items(resp, key)
        total += len(batch)
        cursor = resp.get("meta", {}).get("pagination", {}).get("next_cursor") if isinstance(resp, dict) else None
        if not cursor or len(batch) < _PAGE_SIZE:
            break
    return total


async def _count_offset(client: CustomClient, path: str, key: str = "data") -> int:
    total = 0
    for page in range(_MAX_PAGES):
        resp = await client.get(path, params={"page[limit]": _PAGE_SIZE, "page[offset]": page * _PAGE_SIZE})
        batch = _items(resp, key)
        total += len(batch)
        if len(batch) < _PAGE_SIZE:
            break
    return total


async def _count_page_number(client: CustomClient, path: str, key: str = "data") -> int:
    total = 0
    for page in range(_MAX_PAGES):
        resp = await client.get(path, params={"page[size]": _PAGE_SIZE, "page[number]": page})
        batch = _items(resp, key)
        total += len(batch)
        if len(batch) < _PAGE_SIZE:
            break
    return total


async def _count_service_accounts(client: CustomClient) -> int:
    total = 0
    for page in range(_MAX_PAGES):
        resp = await client.get("/api/v2/users", params={"page[size]": _PAGE_SIZE, "page[number]": page})
        batch = _items(resp, "data")
        total += sum(1 for user in batch if user.get("attributes", {}).get("service_account"))
        if len(batch) < _PAGE_SIZE:
            break
    return total


async def _count_ip_allowlist(client: CustomClient) -> int:
    resp = await client.get("/api/v2/ip_allowlist")
    return len(resp.get("data", {}).get("attributes", {}).get("entries", []))


async def _count_aws_accounts(client: CustomClient) -> int:
    return len(_items(await client.get("/api/v1/integration/aws"), "accounts"))


async def _count_rum_retention_filters(client: CustomClient) -> int:
    apps = _items(await client.get("/api/v2/rum/applications"), "data")
    total = 0
    for app in apps:
        resp = await client.get(f"/api/v2/rum/applications/{app['id']}/retention_filters")
        total += len(_items(resp, "data"))
    return total


# (display name, category, notes, counter)
MANUAL_RESOURCES: List[Tuple[str, str, str, Callable[[CustomClient], Awaitable[int]]]] = [
    ("Workflows", "Workflow Automation & Apps", "", partial(_count_cursor, path="/api/v2/workflows")),
    (
        "Apps (App Builder)",
        "Workflow Automation & Apps",
        "",
        partial(_count_page_number, path="/api/v2/app-builder/apps"),
    ),
    (
        "Action Connections",
        "Workflow Automation & Apps",
        "",
        partial(_count_cursor, path="/api/v2/actions/connections"),
    ),
    (
        "Software Catalog Entities",
        "Software / Service Catalog",
        "",
        partial(_count_cursor, path="/api/v2/catalog/entity"),
    ),
    (
        "Service Definitions",
        "Software / Service Catalog",
        "",
        partial(_count_cursor, path="/api/v2/services/definitions"),
    ),
    ("Scorecards", "Software / Service Catalog", "", partial(_count_offset, path="/api/v2/scorecard/rules")),
    ("Incidents", "Incident & Case Management", "", partial(_count_cursor, path="/api/v2/incidents")),
    (
        "Incident Services",
        "Incident & Case Management",
        "",
        partial(_count_cursor, path="/api/v2/incidents/config/services"),
    ),
    ("Cases", "Incident & Case Management", "", partial(_count_cursor, path="/api/v2/cases")),
    ("On-Call Schedules", "On-Call", "", partial(_count_cursor, path="/api/v2/on-call/schedules")),
    ("On-Call Escalation Policies", "On-Call", "", partial(_count_cursor, path="/api/v2/on-call/escalation-policies")),
    (
        "APM Retention Filters",
        "APM & Tracing",
        "Custom rules only",
        partial(_count_list, path="/api/v2/apm/config/retention-filters"),
    ),
    (
        "RUM Retention Filters",
        "RUM (beyond rum_applications)",
        "Summed across RUM applications",
        _count_rum_retention_filters,
    ),
    ("RUM Metrics", "RUM (beyond rum_applications)", "", partial(_count_list, path="/api/v2/rum/config/metrics")),
    (
        "Logs Custom Destinations",
        "Logs (beyond covered)",
        "",
        partial(_count_list, path="/api/v2/logs/config/custom-destinations"),
    ),
    (
        "Observability Pipelines",
        "Observability Pipelines",
        "",
        partial(_count_page_number, path="/api/v2/remote_config/products/obs_pipelines/pipelines"),
    ),
    ("Reference Tables", "Reference / Data", "", partial(_count_list, path="/api/v2/reference-tables/tables")),
    ("Service Accounts", "Org, Account & Access", "Filtered from users", _count_service_accounts),
    ("API Keys", "Org, Account & Access", "Values not exportable", partial(_count_cursor, path="/api/v2/api_keys")),
    (
        "Application Keys",
        "Org, Account & Access",
        "Values not exportable",
        partial(_count_cursor, path="/api/v2/application_keys"),
    ),
    ("IP Allowlist", "Org, Account & Access", "", _count_ip_allowlist),
    ("AWS Integration Accounts", "Cloud & Third-Party Integrations", "", _count_aws_accounts),
    (
        "Azure Integration",
        "Cloud & Third-Party Integrations",
        "",
        partial(_count_list, path="/api/v1/integration/azure"),
    ),
    ("GCP Integration", "Cloud & Third-Party Integrations", "", partial(_count_list, path="/api/v1/integration/gcp")),
    (
        "PagerDuty Integration",
        "Cloud & Third-Party Integrations",
        "",
        partial(_count_list, path="/api/v1/integration/pagerduty/configuration/services"),
    ),
    (
        "Slack Integration Channels",
        "Cloud & Third-Party Integrations",
        "",
        partial(_count_list, path="/api/v1/integration/slack/configuration/channels"),
    ),
    (
        "Webhooks",
        "Cloud & Third-Party Integrations",
        "",
        partial(_count_list, path="/api/v1/integration/webhooks/configuration/webhooks", key="webhooks"),
    ),
    (
        "Error Tracking Inclusion Filters",
        "Other / Misc",
        "",
        partial(_count_cursor, path="/api/v2/error-tracking/settings/inclusion-filters"),
    ),
]


def _error_reason(err: Exception) -> str:
    if isinstance(err, CustomClientHTTPError):
        return f"HTTP {err.status_code}"
    return type(err).__name__


async def collect_manual_resources(client: CustomClient) -> List[Dict]:
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_COUNTS)

    async def run(name: str, category: str, notes: str, counter) -> Dict:
        async with semaphore:
            try:
                count, error = await counter(client), None
            except Exception as err:
                count, error = -1, _error_reason(err)
        return _row(name, category, False, count, error, notes)

    return list(await asyncio.gather(*(run(*entry) for entry in MANUAL_RESOURCES)))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _row(name: str, category: str, covered: bool, count: int, error: Optional[str], notes: str) -> Dict:
    return {
        "name": name,
        "category": category,
        "covered": covered,
        "count": count,
        "error": error,
        "status": "ERROR" if error else "OK",
        "notes": notes,
    }


def _import_notes(static_notes: str, outcomes: Counter) -> str:
    parts = [static_notes] if static_notes else []
    for status, label in (
        ("success", "imported"),
        ("failure", "failed"),
        ("skipped", "skipped"),
        ("filtered", "filtered"),
    ):
        if outcomes.get(status):
            parts.append(f"{outcomes[status]:,} {label}")
    return ", ".join(parts)


def covered_rows(resource_types: List[str], summaries: Dict[str, ImportTypeSummary]) -> List[Dict]:
    ordered = [rt for rt in COVERED_RESOURCES if rt in resource_types]
    ordered += sorted(rt for rt in resource_types if rt not in COVERED_RESOURCES)
    rows = []
    for resource_type in ordered:
        name, category, notes = COVERED_RESOURCES.get(
            resource_type, (resource_type.replace("_", " ").title(), "Other", "")
        )
        summary = summaries.get(resource_type, ImportTypeSummary())
        error = summary.error or None
        count = -1 if error and not summary.listed else summary.listed
        rows.append(_row(name, category, True, count, error, _import_notes(notes, summary.outcomes)))
    return rows


def site_from_api_url(api_url: str) -> str:
    host = urlparse(api_url).netloc or api_url
    return host[len("api.") :] if host.startswith("api.") else host


def app_url_from_api_url(api_url: str) -> str:
    site = site_from_api_url(api_url)
    if site.count(".") == 1:
        site = f"app.{site}"
    return f"https://{site}"


async def org_label(client: CustomClient) -> str:
    try:
        resp = await client.get("/api/v1/org")
        return resp["orgs"][0]["name"]
    except Exception:
        pass
    try:
        resp = await client.get("/api/v2/current_user")
        return next(i["attributes"]["name"] for i in resp.get("included", []) if i.get("type") == "orgs")
    except Exception:
        return site_from_api_url(client.url_object._default)


def build_report(
    source_label: str,
    destination_label: str,
    site: str,
    resources: List[Dict],
    generated_at: Optional[datetime] = None,
) -> Dict:
    generated_at = generated_at or datetime.now(timezone.utc)
    return {
        "source": source_label,
        "destination": destination_label,
        "site": site,
        "generated_at": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "resources": resources,
    }


# ---------------------------------------------------------------------------
# App Builder app
# ---------------------------------------------------------------------------


def _stable_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"datadog-sync-cli/assessment-report/{name}"))


def app_name(report: Dict) -> str:
    return f"Migration Assessment: {report['source']} → {report['destination']}"


def _rows_query(name: str, rows: List[Dict]) -> Dict:
    return {
        "id": _stable_id(f"query/{name}"),
        "name": name,
        "type": "dataTransform",
        "properties": {"outputs": "${" + json.dumps(rows) + "}"},
    }


def _text(name: str, content: str) -> Dict:
    return {
        "type": "text",
        "name": name,
        "events": [],
        "properties": {
            "content": content,
            "contentType": "markdown",
            "isVisible": True,
            "textAlign": "left",
            "verticalAlign": "top",
        },
    }


def _table(name: str, query_name: str, columns: List[Tuple[str, str]], page_size: int) -> Dict:
    return {
        "type": "table",
        "name": name,
        "events": [],
        "properties": {
            "columns": [
                {
                    "dataPath": path,
                    "disableSortBy": False,
                    "header": header,
                    "id": _stable_id(f"{name}/{path}"),
                    "isHidden": False,
                }
                for path, header in columns
            ],
            "data": f"${{{query_name}?.outputs}}",
            "globalFilter": True,
            "isScrollable": "vertical",
            "isSubRowsEnabled": False,
            "isVisible": True,
            "isWrappable": True,
            "pageSize": page_size,
            "paginationType": "client_side",
            "rowButtons": [],
            "summary": True,
        },
    }


def _table_height(row_count: int) -> int:
    # Each row takes about 4 grid units; keep short tables at a readable minimum.
    return max(80, 4 * row_count + 4)


def build_app(report: Dict) -> Dict:
    """Return the App Builder app definition (the ``attributes`` of a create or update request)."""
    resources = report["resources"]
    covered = [r for r in resources if r["covered"] and not r["error"]]
    manual = [r for r in resources if not r["covered"] and not r["error"]]
    errors = [r for r in resources if r["error"]]
    empty = [r for r in resources if not r["error"] and r["count"] == 0]

    sync_types = len([r for r in covered if r["count"] > 0])
    sync_objects = sum(r["count"] for r in covered if r["count"] > 0)
    manual_types = len([r for r in manual if r["count"] > 0])
    manual_objects = sum(r["count"] for r in manual if r["count"] > 0)

    def count_rows(rows: List[Dict]) -> List[Dict]:
        return [
            {"resource": r["name"], "category": r["category"], "count": r["count"], "notes": r["notes"]}
            for r in sorted(rows, key=lambda r: -r["count"])
        ]

    error_rows = [
        {
            "resource": r["name"],
            "category": r["category"],
            "tool": "sync-cli" if r["covered"] else "manual",
            "error": r["error"],
        }
        for r in errors
    ]
    count_columns = [("resource", "Resource"), ("category", "Category"), ("count", "Count"), ("notes", "Notes")]
    error_columns = [("resource", "Resource"), ("category", "Category"), ("tool", "Tool"), ("error", "Error")]
    sections = [
        ("covered", "coveredRows", "## ✅ Covered by datadog-sync-cli", count_rows(covered), count_columns),
        ("manual", "manualRows", "## ⚠️ Manual migration required", count_rows(manual), count_columns),
        ("errors", "errorRows", "## ⛔ API errors / missing scopes", error_rows, error_columns),
    ]

    generated = report["generated_at"].replace("T", " ")[:16] + " UTC"
    header = (
        f"# {APP_TITLE}\n"
        f"**Source:** {report['source']} → **Destination:** {report['destination']}  |  "
        f"**Site:** {report['site']}  |  **Generated:** {generated}"
    )
    cards = [
        f"##### ✅ sync-cli resources\n# {sync_types}\n{sync_objects:,} total objects",
        f"##### ⚠️ Manual migration\n# {manual_types}\n{manual_objects:,} total objects",
        f"##### ◻️ Empty resources\n# {len(empty)}\nresource types with 0 objects",
        f"##### ⛔ API errors\n# {len(errors)}\ncheck API/APP key scopes",
    ]
    footer = (
        "_Generated by datadog-sync-cli `import --create-assessment-report-app` · "
        "covered resources per [datadog-sync-cli](https://github.com/DataDog/datadog-sync-cli)_"
    )

    placed: List[Tuple[Dict, int, int, int, int]] = [(_text("header", header), 0, 0, 12, 7)]
    placed += [(_text(f"card{i}", content), i * 3, 7, 3, 9) for i, content in enumerate(cards)]
    y = 16
    for key, query_name, title, rows, columns in sections:
        placed.append((_text(f"title_{key}", title), 0, y, 12, 4))
        y += 4
        height = _table_height(len(rows))
        placed.append((_table(f"table_{key}", query_name, columns, max(1, len(rows))), 0, y, 12, height))
        y += height
    placed.append((_text("footer", footer), 0, y, 12, 4))

    cells = [
        {
            "type": "gridCell",
            "name": f"gridCell{i}",
            "events": [],
            "properties": {
                "children": [component],
                "isVisible": "true",
                "layout": {"default": {"height": height, "width": width, "x": x, "y": cell_y}},
            },
        }
        for i, (component, x, cell_y, width, height) in enumerate(placed)
    ]

    return {
        "name": app_name(report),
        "description": "Datadog org migration assessment generated by datadog-sync-cli",
        "rootInstanceName": "grid0",
        "components": [
            {
                "type": "grid",
                "name": "grid0",
                "events": [],
                "properties": {"backgroundColor": "default", "children": cells},
            }
        ],
        "queries": [_rows_query(query_name, rows) for _, query_name, _, rows, _ in sections],
    }


async def upsert_app(client: CustomClient, app: Dict) -> Tuple[str, bool]:
    """Create the app, or overwrite the existing app with the same name as a new version.

    Returns the app ID and whether a new app was created.
    """
    resp = await client.get(APPS_PATH, params={"filter[name]": app["name"], "limit": 100})
    existing = next((a for a in _items(resp, "data") if a.get("attributes", {}).get("name") == app["name"]), None)
    if existing:
        app_id = existing["id"]
        await client.patch(
            f"{APPS_PATH}/{app_id}", {"data": {"type": "appDefinitions", "id": app_id, "attributes": app}}
        )
        return app_id, False
    created = await client.post(APPS_PATH, {"data": {"type": "appDefinitions", "attributes": app}})
    return created["data"]["id"], True


async def publish_app(client: CustomClient, app_id: str) -> None:
    await client.post(f"{APPS_PATH}/{app_id}/deployment", None)


async def create_assessment_report(config: Configuration, summaries: Dict[str, ImportTypeSummary]) -> None:
    config.logger.info("building migration assessment report")
    source_url = config.source_client.url_object._default
    destination_url = config.destination_client.url_object._default

    manual = await collect_manual_resources(config.source_client)
    report = build_report(
        source_label=config.source_org_label or await org_label(config.source_client),
        destination_label=config.destination_org_label or await org_label(config.destination_client),
        site=site_from_api_url(source_url),
        resources=covered_rows(config.resources_arg, summaries) + manual,
    )

    with open(REPORT_FILE_NAME, "w") as f:
        json.dump(report, f, indent=2)
    config.logger.info(f"assessment report written to {os.path.abspath(REPORT_FILE_NAME)}")

    try:
        app_id, created = await upsert_app(config.destination_client, build_app(report))
    except Exception as err:
        config.logger.error(f"failed to create the assessment report app in the destination org: {err}")
        return
    app_url = f"{app_url_from_api_url(destination_url)}/app-builder/apps/{app_id}"
    config.logger.info(f"{'created' if created else 'overwrote'} assessment report app: {app_url}")

    try:
        await publish_app(config.destination_client, app_id)
    except Exception as err:
        config.logger.warning(
            f"assessment report app saved but not published: {err}. Publish it from App Builder to share it."
        )
        return
    config.logger.info("published the assessment report app")
