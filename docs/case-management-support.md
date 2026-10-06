# Case Management Resource Support — Implementation Contract

> **Status:** Draft contract. This document is the implementation contract for adding
> Datadog Case Management resource support to `datadog-sync-cli`. A feasibility phase
> (§12) precedes model implementation: the hard gates marked **pending** below are
> entry gates — until they are empirically confirmed, the corresponding models ship
> **fail-closed by default** (see §8 and §12). The row-by-row operation matrix in §2
> and the per-model contracts in §3 are the source of truth for the implementation
> PRs; the machine-readable registry artifact in §11 is generated from the same
> contracts.
>
> **Verification basis:** Datadog Case Management public API documentation
> (`https://docs.datadoghq.com/api/latest/case-management/`), the
> `case-management-type` and `case-management-attribute` API sections, and the
> `datadog-sync-cli` codebase at the time of writing. All endpoint paths, pagination
> shapes, and schema claims below were verified against the published API surface.

## 1. Disposition classes

Every documented operation (75 total: 66 on the case-management page plus 9 on the
adjacent type/attribute pages) is classified as exactly one of:

| Class | Meaning |
|---|---|
| **[F]** | Full fidelity — synchronized with **zero intentional fidelity loss** |
| **[T]** | Documented transformation — synchronized with an explicitly stated transformation or fidelity loss |
| **[C]** | Command verb — an action endpoint of a modeled resource (folded into that model's convergence), not an independent resource |
| **[I]** | Non-convergeable — write-only relationship or no read path; intentionally unsupported, requires product approval |
| **[U]** | Per-principal state — scoped to an authenticated user; not org-level resource state |

`[F]` is reserved for zero intentional loss. Where a resource's fidelity depends on a
runtime condition (e.g., project team cardinality, rule reference mapping), the
resource is `[F]`/`[T]` **split per resource** and the split is recorded at import
and surfaced in fidelity reporting.

## 2. Row-by-row operation matrix

### 2.1 Automation rules (7 operations)

| # | Operation | Method & Path | Model / Disposition |
|---|---|---|---|
| 1 | List automation rules | `GET /api/v2/cases/projects/{project_id}/rules` | `case_management_automation_rules` [T] — discovery |
| 2 | Create an automation rule | `POST /api/v2/cases/projects/{project_id}/rules` | [T] — create (staged-disabled) |
| 3 | Get an automation rule | `GET /api/v2/cases/projects/{project_id}/rules/{rule_id}` | [T] — per-rule read |
| 4 | Update an automation rule | `PUT /api/v2/cases/projects/{project_id}/rules/{rule_id}` | [T] — update (staged-disabled) |
| 5 | Delete an automation rule | `DELETE /api/v2/cases/projects/{project_id}/rules/{rule_id}` | [T] — delete |
| 6 | Enable an automation rule | `POST …/rules/{rule_id}/enable` | **[C]** — never invoked; superseded by staged convergence (§8) |
| 7 | Disable an automation rule | `POST …/rules/{rule_id}/disable` | **[C]** — never invoked |

### 2.2 Cases (23 operations)

| # | Operation | Method & Path | Model / Disposition |
|---|---|---|---|
| 8 | Create a case | `POST /api/v2/cases` | `case_management_cases` [F] — create (gated by §8) |
| 9 | Search cases | `GET /api/v2/cases` (1-based `page[size]`/`page[number]`; total at `meta.page.total`) | [F] — discovery |
| 10 | Get the details of a case | `GET /api/v2/cases/{case_id}` | [F] — per-id read |
| 11 | Update case title | `POST /api/v2/cases/{case_id}/title` | **[C]** — convergence verb |
| 12 | Update case description | `POST /api/v2/cases/{case_id}/description` | **[C]** |
| 13 | Update case status | `POST /api/v2/cases/{case_id}/status` | **[C]** |
| 14 | Update case priority | `POST /api/v2/cases/{case_id}/priority` | **[C]** |
| 15 | Assign case | `POST /api/v2/cases/{case_id}/assign` | **[C]** |
| 16 | Unassign case | `POST /api/v2/cases/{case_id}/unassign` | **[C]** |
| 17 | Archive case | `POST /api/v2/cases/{case_id}/archive` | **[C]** |
| 18 | Unarchive case | `POST /api/v2/cases/{case_id}/unarchive` | **[C]** |
| 19 | Update case attributes | `POST /api/v2/cases/{case_id}/attributes` | **[C]** — generic metadata bag convergence |
| 20 | Update case custom attribute | `POST /api/v2/cases/{case_id}/custom_attributes/{custom_attribute_key}` | **[C]** |
| 21 | Delete case custom attribute | `DELETE /api/v2/cases/{case_id}/custom_attributes/{custom_attribute_key}` | **[C]** |
| 22 | Update case due date | `POST /api/v2/cases/{case_id}/due_date` | **[C]** — readability gated by S1 (§12) |
| 23 | Update case resolved reason | `POST /api/v2/cases/{case_id}/resolved_reason` | **[C]** — readability gated by S1 |
| 24 | Update case project (move) | `PATCH /api/v2/cases/{case_id}/relationships/project` | **[C]** |
| 25 | Comment case | `POST /api/v2/cases/{case_id}/comment` | `case_management_case_comments` [F] — create |
| 26 | Update case comment | `PUT /api/v2/cases/{case_id}/comment/{cell_id}` | [F] — update |
| 27 | Delete case comment | `DELETE /api/v2/cases/{case_id}/comment/{cell_id}` | [F] — delete |
| 28 | Aggregate cases | `POST /api/v2/cases/aggregate` | **[I]** — query endpoint, not state |
| 29 | Count cases | `POST /api/v2/cases/count` | **[I]** — query endpoint |
| 30 | Bulk update cases | `POST /api/v2/cases/bulk` | **[I]** — query/mutation endpoint, not per-resource convergence |

### 2.3 Case timeline (1 operation)

| # | Operation | Method & Path | Model / Disposition |
|---|---|---|---|
| 31 | Get case timeline | `GET /api/v2/cases/{case_id}/timelines` (zero-based `page[size]`/`page[number]`; **no pagination meta** — stop on short/empty page) | comment-cell discovery for `case_management_case_comments`; non-comment historical cells are **[T]** documented loss (§3.8) |

### 2.4 Case links (3 operations)

| # | Operation | Method & Path | Model / Disposition |
|---|---|---|---|
| 32 | List case links | `GET /api/v2/cases/link` (requires `entity_type` + `entity_id`) | `case_management_case_links` — discovery fans out over cases with `entity_type=CASE`, deduplicating links seen from both endpoints |
| 33 | Create a case link | `POST /api/v2/cases/link` | [F] for `CASE` targets; **[T]** for `INCIDENT`/`PAGE`/`AGENT_CONVERSATION` targets (skipped with reason + counted loss — no destination counterpart resource exists) |
| 34 | Delete a case link | `DELETE /api/v2/cases/link/{link_id}` | [F] — delete (drift = delete+recreate; no update endpoint exists) |

### 2.5 Case views (5 operations)

| # | Operation | Method & Path | Model / Disposition |
|---|---|---|---|
| 35 | List case views | `GET /api/v2/cases/views` (requires `project_id`) | `case_management_case_views` [F] — per-project fan-out |
| 36 | Create a case view | `POST /api/v2/cases/views` | [F] — create (attributes carry `project_id`; asymmetric with the response's `relationships.project`) |
| 37 | Get a case view | `GET /api/v2/cases/views/{view_id}` | [F] |
| 38 | Update a case view | `PUT /api/v2/cases/views/{view_id}` | [F] |
| 39 | Delete a case view | `DELETE /api/v2/cases/views/{view_id}` | [F] |

### 2.6 Watchers (3 operations)

| # | Operation | Method & Path | Model / Disposition |
|---|---|---|---|
| 40 | List case watchers | `GET /api/v2/cases/{case_id}/watchers` | `case_management_case_watchers` [F] (opt-in) — discovery |
| 41 | Watch a case | `POST /api/v2/cases/{case_id}/watchers` | [F] — create (subscribes the mapped user; opt-in flag, default off) |
| 42 | Unwatch a case | `DELETE /api/v2/cases/{case_id}/watchers/{user_uuid}` | [F] — delete |

### 2.7 Favorites (3 operations)

| # | Operation | Method & Path | Model / Disposition |
|---|---|---|---|
| 43 | List project favorites | `GET /api/v2/cases/projects/{project_id}/favorites` | **[U]** — per-principal preference state |
| 44 | Favorite a project | `POST /api/v2/cases/projects/{project_id}/favorites` | **[U]** |
| 45 | Unfavorite a project | `DELETE /api/v2/cases/projects/{project_id}/favorites` | **[U]** |

**[U] rationale (final):** favorites are scoped to the *authenticated* user. An
org-credential resource sync has no principal to attribute or enumerate — per-user
replay would be a separate on-behalf-of product capability, not resource
synchronization. Product approval recorded with this contract.

### 2.8 Insights (2 operations)

| # | Operation | Method & Path | Model / Disposition |
|---|---|---|---|
| 46 | Add insights to a case | `POST /api/v2/cases/{case_id}/insights` | **[I]** — no read path exists; state cannot be discovered, so it cannot converge |
| 47 | Remove insights from a case | `DELETE /api/v2/cases/{case_id}/insights` | **[I]** |

### 2.9 Maintenance windows (4 operations)

| # | Operation | Method & Path | Model / Disposition |
|---|---|---|---|
| 48 | List maintenance windows | `GET /api/v2/maintenance_windows` | `case_management_maintenance_windows` [F] — discovery (excludes the suppression-marker namespace, §9) |
| 49 | Create a maintenance window | `POST /api/v2/maintenance_windows` | [F] — create |
| 50 | Update a maintenance window | `PUT /api/v2/maintenance_windows/{maintenance_window_id}` | [F] — update |
| 51 | Delete a maintenance window | `DELETE /api/v2/maintenance_windows/{maintenance_window_id}` | [F] — delete |

### 2.10 Notification rules (4 operations)

| # | Operation | Method & Path | Model / Disposition |
|---|---|---|---|
| 52 | Get notification rules | `GET /api/v2/cases/projects/{project_id}/notification_rules` | `case_management_project_notification_rules` [T] — per-project fan-out |
| 53 | Create a notification rule | `POST /api/v2/cases/projects/{project_id}/notification_rules` | [T] — create (staged-disabled) |
| 54 | Update a notification rule | `PUT /api/v2/cases/projects/{project_id}/notification_rules/{notification_rule_id}` | [T] — update (staged-disabled) |
| 55 | Delete a notification rule | `DELETE /api/v2/cases/projects/{project_id}/notification_rules/{notification_rule_id}` | [T] — delete |

### 2.11 Projects (5 operations)

| # | Operation | Method & Path | Model / Disposition |
|---|---|---|---|
| 56 | Create a project | `POST /api/v2/cases/projects` | `case_management_projects` [F]/[T] — create (see §3.3) |
| 57 | Get all projects | `GET /api/v2/cases/projects` | [F]/[T] — discovery (single response) |
| 58 | Get the details of a project | `GET /api/v2/cases/projects/{project_id}` | [F]/[T] — per-id read |
| 59 | Remove a project | `DELETE /api/v2/cases/projects/{project_id}` | [F]/[T] — delete (retention closure may block, §5) |
| 60 | Update a project | `PATCH /api/v2/cases/projects/{project_id}` | [F]/[T] — update |

### 2.12 Case-related relationship actions (6 operations)

| # | Operation | Method & Path | Model / Disposition |
|---|---|---|---|
| 61 | Link incident to case | `POST /api/v2/cases/{case_id}/relationships/incidents` | **[I]** — write-only relationship; no discoverable case-side representation |
| 62 | Create Jira issue for case | `POST /api/v2/cases/{case_id}/relationships/jira_issues` | **[I]** — environment-bound (destination integration config required); read-side `jira_issue` field is [T] documented loss |
| 63 | Link existing Jira issue to case | `POST /api/v2/cases/{case_id}/relationships/jira_issues` | **[I]** — as above |
| 64 | Remove Jira issue link from case | `DELETE /api/v2/cases/{case_id}/relationships/jira_issues` | **[I]** |
| 65 | Create ServiceNow ticket for case | `POST /api/v2/cases/{case_id}/relationships/servicenow_tickets` | **[I]** — environment-bound; read-side `service_now_ticket` field is [T] documented loss |
| 66 | Create investigation notebook for case | `POST /api/v2/cases/{case_id}/relationships/notebook` | **[I]** — write-only; no discoverable case-side representation |

These operations mutate durable case-related state that is **not independently
discoverable/listable** for convergence; they are classified non-convergeable with
product approval, not as mere "commands."

### 2.13 Case types (4 operations — `case-management-type` section)

| # | Operation | Method & Path | Model / Disposition |
|---|---|---|---|
| 67 | Get all case types | `GET /api/v2/cases/types` | `case_management_types` [F] — discovery (single response; **no per-id GET** exists) |
| 68 | Create a case type | `POST /api/v2/cases/types` | [F] — create |
| 69 | Update a case type | `PUT /api/v2/cases/types/{case_type_id}` | [F] — update |
| 70 | Delete a case type | `DELETE /api/v2/cases/types/{case_type_id}` | [F] — delete |

### 2.14 Custom attribute configs (5 operations — `case-management-attribute` section)

| # | Operation | Method & Path | Model / Disposition |
|---|---|---|---|
| 71 | Get all custom attributes | `GET /api/v2/cases/types/custom_attributes` | read view of values — represented by case `custom_attributes` (case data) |
| 72 | Get all custom attributes config of case type | `GET /api/v2/cases/types/{case_type_id}/custom_attributes` | `case_management_custom_attribute_configs` [F] — per-type read |
| 73 | Create custom attribute config | `POST /api/v2/cases/types/{case_type_id}/custom_attributes` | [F] — create |
| 74 | Update custom attribute config | `PUT /api/v2/cases/types/{case_type_id}/custom_attributes/{custom_attribute_id}` | [F] — update |
| 75 | Delete custom attributes config | `DELETE /api/v2/cases/types/{case_type_id}/custom_attributes/{custom_attribute_id}` | [F] — delete |

## 3. Per-model contracts

### 3.1 `case_management_types`
- **Base path:** `/api/v2/cases/types`. List is a single response (no pagination). No per-id GET — targeted import is list-and-filter.
- **Identity/adoption:** `attributes.name` (ambiguity-refused).
- **Connections:** none. **Owner:** none. **Side effects:** none.
- **Deletion:** standard (`cleanup_policy: delete`). **Crash recovery:** name adoption.
- **id-file namespace:** `resource` semantics **not applicable** (no per-id GET; no id-file support).

### 3.2 `case_management_custom_attribute_configs`
- **Discovery:** org-wide `GET /api/v2/cases/types/custom_attributes` (single request; items carry `attributes.case_type_id`). CRUD path-scoped under the case type.
- **Identity/adoption:** (mapped destination case-type, `attributes.key`), ambiguity-refused.
- **Connections:** `case_management_types` (`attributes.case_type_id`). **Owner:** none. **Side effects:** none.
- **Deletion:** standard. **Crash recovery:** identity adoption. **id-file namespace:** `resource` (not applicable — no per-id GET).

### 3.3 `case_management_projects`
- **Discovery:** `GET /api/v2/cases/projects` (single response); per-id GET exists.
- **Identity/adoption:** `attributes.key` (immutable). **Connections:** teams (team reference synthesized from `relationships.member_team.data[]`; **cardinality policy:** 0 links → sync without team; exactly 1 → remap; >1 → **[T]** ambiguity: sync without team, counted fidelity loss, never a silent `data[0]` pick) and `case_management_types` (`enabled_custom_case_types`, list remap).
- **Create schema:** `{key, name, team_uuid, enabled_custom_case_types}` only — `settings` and `columns_config` are update-only (post-create convergence PATCH; the destination mapping is persisted before the post-create PATCH so a failed PATCH never orphans the project).
- **Settings policy:** `auto_close_inactive_cases` and `auto_transition_assigned_cases` are inert configuration → synced; `settings.notification` and `settings.integration_*` are behavior-changing/environment-bound → excluded from the default waves with per-subfield reason.
- **Fidelity:** `[F]` when team cardinality is 0 or exactly 1; `[T]` when ambiguous. **Owner:** none. **Side effects:** none.
- **Deletion:** standard, subject to retention closure (§5). **Crash recovery:** key adoption. **id-file:** per-id GET exists.

### 3.4 `case_management_project_notification_rules` — **[T]**
- **Discovery:** per-project fan-out (parent scope: projects). **Identity/adoption:** exact-content within the mapped project (deterministic; ambiguity-refused — never heuristic similarity).
- **Recipients:** `SLACK_CHANNEL | EMAIL | HTTP | PAGERDUTY_SERVICE | MS_TEAMS_CHANNEL`; `data.team_id` is a Microsoft Teams external team ID — **never remapped**; email semantics are decided by gate S2 (§12): arbitrary external address (verbatim, environment-bound) **or** mapped-user reference (remapped to the mapped destination user's address).
- **Enabled state:** synchronized via staged-disabled + cutover (§8). Enable/disable verbs never invoked.
- **Owner:** none (no creator field exists). **Side effects:** rules fire notifications when enabled — gated by §8.
- **Deletion:** standard. **Crash recovery:** deterministic enumerable recovery (per-project rule re-list, exact-content match among unmapped resources). **id-file namespace:** `parent` (project ids).

### 3.5 `case_management_automation_rules` — **[T]**
- **Discovery:** per-project fan-out. **Identity/adoption:** exact-content within the mapped project, ambiguity-refused.
- **Action fields:** `ASSIGN_AGENT` carries AI-agent identifiers; `EXECUTE_WORKFLOW` carries workflow handles. These are environment-bound: a rule whose references lack a validated operator-provided mapping is staged and content-synced, but **activation is blocked at cutover** (fail-closed, surfaced per rule — never activated against unvalidated references).
- **Enabled state:** staged-disabled + cutover (§8). **Owner:** `relationships.created_by` (user UUID). **Side effects:** gated by §8.
- **Deletion:** standard. **Crash recovery:** deterministic enumerable recovery. **id-file namespace:** `parent` (project ids).

### 3.6 `case_management_cases`
- **Discovery:** `GET /api/v2/cases`, 1-based pagination (`page[size]`/`page[number]`; total at `meta.page.total`).
- **Identity:** no natural key exists; the create API has no idempotency field. Identity = **provenance marker** (gate S9, §12): a writable, destination-searchable, unique, update-preserved source identifier stored on the case. **If S9 cannot confirm a compliant marker and no API idempotency mechanism exists, this resource fails closed** — manual reconciliation is not the shipped default.
- **Convergence:** no single PATCH endpoint exists; the §2.2 action endpoints are convergence verbs driven by a per-case minimal-action state machine. `attributes.archived_at` is normalized to a boolean for comparison (raw timestamps are excluded from diffs — two different non-null timestamps with equal archive state are no drift); the generic metadata bag (`attributes.attributes`) is compared and converged via `POST …/attributes`.
- **Connections:** projects (`relationships.project`), users (`relationships.assignee`), case types (`attributes.type_id`). **Owner:** `relationships.created_by` (user UUID, read-only). **Side effects:** gated by §8.
- **Deletion:** **none exists** (archive is not delete) — `cleanup_policy: retain` (§5). **Crash recovery:** provenance-marker search (S9). **id-file:** per-id GET exists.

### 3.7 `case_management_case_links`
- **Discovery:** fan out over cases (`entity_type=CASE` + per-case `entity_id`), deduplicating links seen from both endpoints.
- **Targets (final):** `CASE` → both endpoints remapped via case mappings; `INCIDENT`, `PAGE`, `AGENT_CONVERSATION` → **[T] skipped with reason + counted fidelity loss** (no destination counterpart resource exists; IDs are not remappable).
- **Identity:** (parent case, child case, relationship). No update endpoint → drift = delete-then-recreate. **Connections:** cases (both endpoints). **Owner:** none. **Side effects:** none.
- **Deletion:** standard. **Crash recovery:** re-list recovery for **all** unknown outcomes. **id-file namespace:** `parent` (case ids).

### 3.8 `case_management_case_comments`
- **Discovery:** `GET /api/v2/cases/{case_id}/timelines` (zero-based, no pagination meta — stop on short/empty page with a hard safety cap; a cap hit is an **incomplete scope**, never a successful end-of-list).
- **Identity/adoption:** exact-content (mapped case + comment text), ambiguity-refused. Cells with `deleted_at` set are filtered at discovery and never recreated.
- **Timeline fidelity:** non-comment historical cells are **[T] documented loss** — status/assignee/archive convergence verbs regenerate the corresponding entries; anything else is not reproduced.
- **Owner:** timeline-cell author (user UUID; a handle is also present) — preserved read-only for ownership attribution. **Side effects:** comments can notify — gated by §8.
- **Deletion:** standard. **Crash recovery:** deterministic per-case timeline re-list, exact-content match among unmapped comments. **id-file namespace:** `parent` (case ids).

### 3.9 `case_management_case_views`
- **Discovery:** per-project fan-out (list requires `project_id`). **Identity/adoption:** (mapped project, `attributes.name`), ambiguity-refused.
- **Asymmetry:** the response carries the project under `relationships.project`; create carries `attributes.project_id`.
- **Connections:** projects; `np_rule_id` → notification rules (null is not a missing dependency). **Owner:** `relationships.created_by` (UUID). **Side effects:** none.
- **Deletion:** standard. **Crash recovery:** identity adoption. **id-file namespace:** `parent` (project ids).

### 3.10 `case_management_case_watchers` — opt-in
- **Discovery:** per-case fan-out. **Identity:** (mapped case, mapped user). **Connections:** users; cases.
- **Side effects:** watching a case subscribes the mapped user to notifications — **opt-in flag, default off**; enablement gated by S13/S14 (§12).
- **Owner:** the watched user. **Deletion:** standard (unwatch). **Crash recovery:** identity adoption. **id-file namespace:** `parent` (case ids).

### 3.11 `case_management_maintenance_windows`
- **Discovery:** single response. **Identity/adoption:** `attributes.name`, ambiguity-refused. **Owner:** `attributes.created_by` (UUID-vs-handle classification pinned by S14).
- **Side effects:** an active window suppresses case notifications — that suppression is the mechanism §9 uses, and also a fidelity caveat (documented).
- **Discovery exclusion:** the suppression-marker namespace (§9) is excluded from source discovery and reconciliation by filter.
- **Deletion:** standard. **Crash recovery:** name adoption. **id-file:** per-id GET does not exist (no id-file).

## 4. Dependency DAG (sync order)

ID-remap edges are expressed via `resource_connections`. The
custom-attribute-configs → cases edge is semantic (custom-attribute keys, no ID
path) and is expressed as ordering metadata. **The DAG freezes only after the §12
writable-reference inventory closes.**

```
teams ─(team ref)→ projects ────────────→ cases ←─(assignee)── users
types ─(type_id)→ cases
types ─(type_id, enabled_custom_case_types)→ projects
types ─(case_type_id)→ custom_attribute_configs ─(semantic ordering)→ cases
projects ─(parent scope)→ notification rules ─(np_rule_id)→ views
projects ─(parent scope)→ automation rules
projects ─(parent scope)→ views
projects ─(parent scope)→ cases ─→ comments, links, watchers
users ─(user)→ watchers
maintenance windows: standalone (query strings reference project keys semantically)
```

## 5. Cleanup — partial order with retention closure

Cleanup order is the **reverse partial order** of §4 (dependents before
dependencies), **not** an exact-reverse linear list. Two closures apply:

- **Retention closure:** cases have `cleanup_policy: retain` (no delete API
  exists). Retained resources are never queued for deletion and never reported as
  deleted. Retained cases **permanently block** project and case-type deletion
  through the dependency scan — a documented, accepted consequence of the
  no-delete case API, surfaced as explicit accounting rather than an error.
- **Type-wide discovery authority:** for parent-scoped child types, any failed or
  incomplete parent scope makes the **entire child type non-authoritative** for
  that import, and **all deletion for that type is suppressed** (conservative). A
  finer-grained per-scope authority is deliberately out of scope.
- **Filtered resources are excluded from both desired and deletion scopes by
  construction.**
- Family cleanup ships **disabled by default** until final acceptance, and never
  takes the unordered fallback.

## 6. Parent-scoped discovery and the id-file dispatch contract

Child models enumerate parents themselves via the parent type's own list endpoint
(the existing concurrent per-type import flow is unchanged). The `--id-file` path
dispatches by **model metadata**:

- `ResourceConfig.id_file_namespace = "resource"` (default): the existing
  `get_resources_by_ids` semantics — the payload contains **resource ids**. Every
  existing type is unchanged.
- `ResourceConfig.id_file_namespace = "parent"`: the payload contains **parent
  ids**; dispatch routes to `get_resources_by_parent_ids(client, parent_ids,
  max_concurrent_reads=…)`, returning the same `(resources, missing, errored)`
  shape with per-scope terminal outcomes.

Parent ids supplied are **source** ids; the model maps them through state for
destination-path operations. The namespace is a model constant versioned in the
registry artifact (§11), so external orchestrator chunk fingerprints remain valid.
Types join the `--id-file` import allowlist **with their model PRs**, each with a
docstring note documenting the parent-id meaning (the established explicit-review
allowlist pattern).

## 7. Capacity and memory bounds

- Bounded per-endpoint concurrency via the existing `--max-concurrent-reads`
  mechanism; standard backoff for 429/5xx.
- **Managed (orchestrator-chunked) imports:** parent-group chunks bound total
  records per invocation — a max-records-per-chunk guard fails loudly when a chunk
  would exceed its bound.
- **Standalone:** a documented hard discovery cap with **loud failure** above it —
  an over-cap result is an error, never a silently short list.
- Load tests assert **peak memory** (not only request counts) at representative
  org sizes, per-endpoint request bounds, and per-parent failure isolation.

## 8. Case-family safety — staged-disabled rules, cutover, and the write-path gate

Rules (notification + automation) are **[T]**: their content is synchronized, but
the **enabled state is staged** — rules are created/updated in a disabled state by
default (staging), with the source's desired enabled state preserved in a
`_desired_*` state sidecar. Final convergence happens at **cutover**:

1. **Establish and verify server-enforced suppression** (gate S11): a
   case-management maintenance window must suppress **both** notification and
   automation side effects for matching cases. **If unproven, the case family
   fails closed** — no case/comment/watcher writes ship.
2. **Foundational sync** (types, projects, configs, maintenance windows) with
   rules staged disabled.
3. **Case and child sync under the write-path gate:** every case-family
   `create_resource`/`update_resource` verifies an active suppression window (in
   the reserved marker namespace, §9) covering the project with **remaining TTL >
   chunk deadline + margin**. A chunk that cannot finish before the margin
   aborts fail-closed. Unmanaged destination rules are **never mutated** and are
   not blocking under proven window coverage.
4. **Cutover:** managed rules converge to the **source's desired enabled state**
   (`--rule-convergence final`). Environment-bound references (AI agents,
   workflow handles, integration-bound recipients) must be covered by a validated
   operator-provided mapping or that rule's activation is **skipped fail-closed
   with an explicit per-rule error**. Pre-stage destination state is journaled in
   a `_pre_stage_snapshot` state sidecar on first staging (versioned, retained
   until post-cutover verification). `--restore-pre-stage` applies the snapshots
   back exactly (audited). A crashed cutover completes on retry; the default sync
   path can **never** converge rules to enabled without the explicit flag.
5. **Verify final state; expire/remove suppression.**

## 9. Suppression maintenance-window protocol (distinct from the MW resource)

The suppression window is an **orchestration control**, not the
`case_management_maintenance_windows` resource (which represents customer state
only):

- **Identity:** a reserved synthetic marker namespace (dedicated name prefix +
  run marker; project-scoped query) that cannot collide with source windows. The
  source-MW model's discovery **excludes the marker namespace by filter**, so
  suppression windows never enter source state or reconciliation.
- **Ownership:** created/renewed/deleted by the orchestrator (or operator
  runbook, standalone) via direct API calls with the marker protocol — never via
  the resource model.
- **Stale windows** (marker-pattern windows older than their TTL): detected,
  reported for operator removal, never silently left suppressing.
- **Conflicts:** a source window with the same query coexists harmlessly
  (overlapping suppression is acceptable); name collisions are impossible by the
  reserved namespace.

## 10. Rollback, preflight, and support matrices

- **Preflight:** a dedicated **dry-run plan surface** enumerates every planned
  irreversible write (case/comment/watcher convergence verbs, rule staging and
  cutover changes, suppression-window operations) **without executing any**; the
  operator approves the plan before the first irreversible write. Generic diffs
  are insufficient — they do not enumerate verbs or orchestration operations.
- **Canary and rollback:** canary orgs with maximum irreversible-write caps;
  audit checks with run context; abort criteria (any unexplained sink event);
  compensating actions where APIs permit (archive cases, unwatch, delete
  children/rules/configs/windows); exact-state restoration of managed rules from
  `_pre_stage_snapshot` journals via `--restore-pre-stage`.
- **Support matrices:** *standalone* — org-credential sync, full bounded
  discovery per run, operator-runbook suppression, in-invocation cleanup
  (default-off). *Orchestrated* — ownership attribution per the owner fields,
  chunked/deadlined discovery and apply, orchestrator-owned suppression renewal,
  registry-artifact-validated integration. Each matrix has its own acceptance
  suite.

## 11. Machine-readable registry artifact

`docs/case-management-registry.json` is the versioned registry artifact. It
records, for every model: `resource_type`, `id_file_namespace`, `cleanup_policy`,
`owner_field`, `parent_scope_type`, `identity`, and `status`. External consumers
validate against the artifact's `registry_version`. The reconciliation PR adds a
consistency test asserting that the README resource table and the registry
artifact match the registered models.

## 12. Feasibility gates (entry gates; results recorded before model implementation)

| Gate | Question | Status | Consequence if unresolved |
|---|---|---|---|
| S1 | Are `due_date` / `security_resolved_reason` readable on a case GET? Custom-attribute delete semantics? | pending | corresponding convergence verbs become documented fidelity loss |
| S2 | Recipient create body; email semantics: external address or mapped-user reference? | pending | email treated as environment-bound verbatim (no remap) |
| S3 | Automation action-type inventory (recursive) — which action fields carry environment-bound references? | pending | all AI-agent/workflow references require operator mapping or activation blocking |
| S4 | Comment decode + author shape (UUID/handle) | pending | — |
| S5 | Case-link target enumeration | **resolved** (docs): `CASE`, `INCIDENT`, `PAGE`, `AGENT_CONVERSATION` | — |
| S6 | View-update mutability; MW list shape | pending | — |
| S7 | Project member-team cardinality invariant | pending | ambiguity policy (§3.3) applies |
| S9 | **Hard:** provenance marker — writable, destination-searchable, unique, update-preserved, non-colliding | pending | **`case_management_cases` fails closed** |
| S11 | **Hard:** MW suppresses both notification and automation side effects | pending | **case family fails closed** |
| S12 | Integration-config contract for environment-bound write paths | pending | [I] dispositions stand |
| S13 | Side-effect evidence methodology (controlled sinks + audit assertions; cassettes never prove absence) | pending | — |
| S14 | **Hard:** permissions under the real on-behalf-of principal; MW `created_by` UUID-vs-handle | pending | case family fails closed |
| S15 | **Hard:** API availability by site | pending | affected sites excluded |

**Writable-reference inventory:** every writable ID/reference field across action,
recipient, query, custom-attribute, relationship, and integration variants is
inventoried and classified (remap / operator-mapping / stable-verbatim /
environment-bound-blocked / reject) **before the DAG and registry freeze**.

## 13. External-orchestrator integration requirements (generic)

- Orchestration stays in the existing `import`/`sync` commands plus external
  scheduling. The `--id-file` parent-namespace contract (§6) is the chunking
  surface: a chunk carries parent ids for parent-namespace types.
- The registry artifact (§11) carries `registry_version`; consumers validate
  against the expected version.
- Rule staging, cutover (`--rule-convergence final`), and restoration
  (`--restore-pre-stage`) are public CLI surfaces; external phases invoke them in
  the §8 order.
- Suppression-window lifecycle (§9) is owned by the orchestrator or operator
  runbook, using the documented marker protocol.
