# Incident Resource Support — Contract / RFC

> **Status:** Draft — Milestone 1 (Contract/RFC). This document is a prerequisite gate
> for any incident-resource implementation work. No implementation code may land until
> this RFC is reviewed and approved, and the implementation sequence is re-confirmed
> against the approved version.
>
> **Version:** 1.0-draft
>
> **Verification basis:** Datadog Incidents API documentation
> (`https://docs.datadoghq.com/api/latest/incidents/`), the `datadog-api-client` Python
> SDK **2.61.0** (the version present in the sync-cli tox environment), and the
> the `origin/main` branch of the public `datadog-sync-cli` repository. All schema
> claims below were checked against the installed 2.61.0 model modules and the live
> Incidents API documentation; items that require a live controlled-org API spike are
> explicitly marked **[SPIKE]**. Claims in this RFC are reproducible from public
> artifacts (the published API docs and the `datadog-api-client` package).

## 1. Goal

Add incident-resource synchronization to `datadog-sync-cli` so that an external
orchestrator can replicate incidents and their configuration across Datadog
organizations / datacenters, **without ever paging or notifying anyone** as a
side effect of sync-driven creation.

This RFC is scoped to `datadog-sync-cli`'s public behavior and contracts.
Internal rollout topology, process details, and orchestration-specific wiring
belong in a private companion document, not in this public repo.

The central safety constraint: a sync-cli-created incident must not trigger
notifications, integrations, workflows, or on-call pages. This RFC defines the
endpoint selection, the safety barrier, the identity model, the durability model, and
the per-resource contracts that make that guarantee provable rather than assumed.

## 2. Endpoint inventory and disposition

The Incidents API exposes the following endpoint groups. Each is classified as:

- **syncable** — has List + Get + Create + Update + Delete (or a sufficient subset for
  idempotent create/update convergence).
- **singleton** — one object per org; no per-id path.
- **action** — fire-and-forget; no read/delete/idempotency contract.
- **write-only config** — Create/Update exist but no List/Get; source state cannot be
  discovered through the documented API.
- **read-only view** — no write path.
- **unsupported-fidelity** — the response omits fields required to faithfully
  recreate the source object.

| Resource | Base path | List | Get | Create | Update | Delete | Disposition |
|---|---|:--:|:--:|:--:|:--:|:--:|---|
| `incidents` | `/api/v2/incidents` | yes | yes | `POST /import` | PATCH | DELETE | **syncable** (via import; see §3) |
| `incident_types` | `/api/v2/incidents/config/types` | yes | yes | POST | PATCH | DELETE | **syncable** |
| `incident_user_defined_fields` | `.../config/user-defined-fields` | yes | yes | POST | PATCH | DELETE | **syncable** |
| `incident_user_defined_roles` | `.../config/user-defined-roles` | yes | yes | POST | PATCH | DELETE | **syncable** |
| `incident_impact_fields` | `.../config/impact-fields` | yes | yes | POST | **PUT** | DELETE | **syncable** |
| `incident_notification_templates` | `.../config/notification-templates` | yes | yes | POST | PATCH | DELETE | **syncable** |
| `incident_postmortem_templates` | `.../config/postmortem-templates` | yes | yes | POST | PATCH | DELETE | **syncable** |
| `incident_global_handles` | `.../config/global/incident-handles` | yes | — | POST | **PUT** | DELETE | **syncable** (atypical; see §10) |
| `incident_global_settings` | `.../config/global/settings` | GET (singleton) | — | — | PATCH | — | **singleton** |
| `incident_notification_rules` | `.../config/notification-rules` | yes | yes | POST | **PUT** | DELETE | **syncable** (rules; see §12) |
| `incident_rules` | `.../config/rules` | yes | yes | POST | PATCH | DELETE | **conditional** (see §11) |
| `incident_impacts` | `/incidents/{id}/impacts` | yes | — | POST | PATCH | DELETE | **syncable** (nested) |
| `incident_integration_metadata` | `/incidents/{id}/relationships/integrations` | yes | yes | POST | PATCH | DELETE | **syncable** (nested) |
| `incident_todos` | `/incidents/{id}/relationships/todos` | yes | yes | POST | PATCH | DELETE | **syncable** (nested) |
| `incident_attachments` | `/incidents/{id}/attachments` | yes | — | POST | PATCH | DELETE | **partial** (see §13) |
| `incident_responders` | `/incidents/{id}/responders` | yes | yes | POST | — | DELETE | **syncable** (nested; no update) |
| `incident_timestamp_overrides` | `/incidents/{id}/timestamp-overrides` | yes | — | POST | PATCH | DELETE | **syncable** (nested) |
| `incident_configurations` | `/incidents/{id}/configurations` | **no** | **no** | POST | PATCH | — | **write-only config** — destination safety state only (see §4) |
| `incident_google_chat_config` | `.../config/google-chat-configurations` | — | — | POST | PATCH | — | **skipped** (no list/get/delete) |
| `incident_google_meet_config` | `.../config/google-meet-configurations` | — | — | POST | PATCH | — | **skipped** (no list/get/delete) |
| `incident_servicenow_record` | `/incidents/{id}/servicenow-records` | — | — | POST | — | — | **skipped** (action) |
| `incident_oncall_page` | `/incidents/{id}/page` | — | — | POST | — | — | **skipped** (action — pages someone!) |
| `incident_ai_postmortem` | `/incidents/{id}/ai/postmortem` | — | — | POST | — | — | **skipped** (action) |
| `incident_type_org_settings` | `.../config/types/{id}/org-settings` | yes | yes | — | — | — | **skipped** (read-only) |
| deprecated `page from incident` | `/incidents/{id}/cases/page` | — | — | POST | — | — | **skipped** (deprecated action) |

**Syncable count:** 17 resource types (incident_configurations is NOT syncable — it is
destination-side safety state; see §4).

## 3. Incident creation: the import endpoint and the no-notification guarantee

**Finding (verified):** `POST /api/v2/incidents` executes integrations and
notification rules; there is **no API parameter** that suppresses this.
`IncidentCreateAttributes` offers only `is_test` and `notification_handles`;
notification *rules* still fire.

**Decision:** sync-cli creates incidents via `POST /api/v2/incidents/import`. The SDK
docstring states: *"Import an incident from an external system. This endpoint allows
you to create incidents with historical data such as custom timestamps for detection,
declaration, and resolution. Imported incidents do not execute integrations or
notification rules."*

`IncidentImportRequestAttributes` (verified in 2.61.0) = `{title, declared, detected,
fields, incident_type_uuid, resolved, visibility}` — **no `notification_handles`**, and
only a subset of create attributes. Import relationships = `commander_user` +
`declared_by_user` only (no creator).

**Critical limitation:** the import guarantee covers **only the import operation**.
Later PATCHes, child-resource creation, and rule installation can still notify/page.
`notification_handles` controls direct recipients on update, but notification **rules**
react to field changes and incident **rules** execute jobs (Jira/ServiceNow/chat/meet/
workflows). Excluding `notification_handles` is therefore **not sufficient**. The
defense-in-depth barrier in §4 closes this gap.

## 4. The suppression barrier (incident configurations)

`incident_configurations` has **no GET/LIST** endpoint and is not included in incident
GET responses. It is therefore **not a syncable resource**. It is modeled as
**destination-side safety state**: an incident configuration with
`execute_integrations=false` and `execute_notification_rules=false` established
immediately after import, before any PATCH or child creation.

**Verified in 2.61.0:** `incident_configuration_data_attributes_request` and
`..._patch_data_attributes_request` both expose `execute_integrations`,
`execute_notification_rules`, `include_in_analytics`, `include_in_search`.

**[SPIKE] — configuration recovery contract (must be resolved before coding):**
- Is a configuration automatically present after import, or must it be POSTed?
- The upsert algorithm: PATCH-first → 404 → POST, or POST-first → 409 → PATCH.
- How is a pre-existing configuration ID recovered? PATCH requires the configuration
  ID in the body; if POST succeeded and its response was lost, or saga state was lost,
  a later 409 does not necessarily reveal the ID.
- Unknown POST/PATCH outcome → **fail-closed / manual reconciliation**.
- If the API cannot support safe recovery, the only safe initial behavior is
  **import-only incidents with no later PATCHes or children**.

**Barrier lifecycle policy (must be resolved before coding):**
- Explicit incident modes and transitions: `historical-import`, `quarantined`,
  `cutover-active`.
- Are imported incidents intentionally inert forever, or is source desired
  configuration captured and restored at cutover? If restoration is allowed, is setting
  the flags `true` retroactive?
- Never alter a pre-existing incident's flags without provenance and an explicit opt-in.
- Rollback for barrier changes (not just auditing): how barrier changes are reverted.

**Side-effect matrix (tri-state, fail-closed on `unknown`):** every mutating endpoint
gets `{verified_safe, known_side_effect, unknown}` + provenance. The controlled test
org deliberately contains active notification rules, workflows, and integration sinks,
and validates import, configuration POST/PATCH, incident PATCH, and every child
mutation **independently**. A VCR cassette does not prove the absence of out-of-band
pages; no-side-effect validation requires a controlled test org with synthetic sink
endpoints + audit/event assertions + sanitized exported evidence.

## 5. Ownership attribution (OBO)

An external orchestrator preserves creator attribution via On-Behalf-Of (OBO) JWTs:
the destination API is invoked as the mapped destination user. The owner field
determines which OBO identity is used.

**Verified in 2.61.0:**

| Resource | Owner field | Type | Notes |
|---|---|---|---|
| `incidents` | `relationships.created_by_user.data.id` | UUID | **optional** (`Union[..., UnsetType] = unset`), NOT non-nullable. Distinct from `commander_user` (nullable). Import request cannot submit a creator → OBO identity preserves attribution. Fallback: `created_by_user` → `declared_by_user` → service account. |
| `incident_types` | `attributes.created_by` | UUID (`str`) | NOT an email handle. Also `relationships.created_by_user`. |
| `incident_user_defined_fields` | `relationships.created_by_user.data.id` | UUID | |
| `incident_user_defined_roles` | `relationships.created_by_user.data.id` | UUID | |
| `incident_impact_fields` | `relationships.created_by_user.data.id` | UUID | |
| `incident_notification_templates` | `relationships.created_by_user.data.id` | UUID | |
| `incident_global_handles` | `relationships.created_by_user.data.id` | UUID | |
| `incident_notification_rules` | `relationships.created_by_user.data.id` | UUID | |
| `incident_rules` | `attributes.created_by_uuid` | UUID | |
| `incident_impacts` | `relationships.created_by_user.data.id` | UUID | nested |
| `incident_integration_metadata` | `relationships.created_by_user.data.id` | UUID | nested |
| `incident_todos` | `relationships.created_by_user.data.id` | UUID | nested |
| `incident_timestamp_overrides` | `relationships.created_by_user.data.id` | UUID | nested |
| `incident_responders` | `relationships.created_by.data.id` | UUID | **creator**, NOT `relationships.user` (the responder). |
| `incident_postmortem_templates` | **none** | — | Response has `last_modified_by_user`, NOT `created_by_user`. Owner policy: **[SPIKE]** — must resolve to a single deterministic choice (service-account ownership OR last-modifier approximation) before Wave 1; an OR is not carried into implementation. Fidelity loss documented. |
| `incident_attachments` | **none** | — | No creator in response. Inherit the parent incident's `created_by_user` owner (NOT commander). |
| `incident_global_settings` | **none** | — | Singleton. Service account. |

**API permissions vs ownership [SPIKE]:** creating org-wide types/templates/handles/
rules as the mapped source creator may fail if that destination user lacks
settings-write permissions, even if incident creation works. Each endpoint must be
tested under the exact OBO principal used in production and **fail/report rather than
silently switch principals** (a service-account retry would violate ownership).
403/404/preview-unavailable outcomes are included in wave acceptance and
continue-on-error metrics.

**OBO grouper redesign (external orchestrator):** a single `FieldPath` +
single `FieldType` cannot express ordered fallbacks, mixed UUID/handle, or
parent-owner lookup. The orchestrator's owner configuration must be redesigned as an
ordered strategy list with typed extractors and an explicit parent-reference
strategy. Attachment owner stamping must be resolved without extra incident GETs or
unavailable `ImportState` reads (`ImportState` is write-only — verified).

## 6. Identity and unknown-POST recovery

**Verified:** `creation_idempotency_key` appears in the import **response** attributes
only, NOT the import request. It does **not** close the crash-after-acceptance window
(crash after the server accepted import but before the response/UUID was received).
There is no API client idempotency key in 2.61.0.

Title/type/timestamps are **not** a safe cross-org identity; a unique heuristic match
can adopt an unrelated destination incident.

**[SPIKE] — one concrete strategy required before coding (priority order):**
1. An API-supported client idempotency/provenance parameter on the import request
   (verified 2.61.0 does NOT expose one — re-check the live Preview spec).
2. A **deterministic, destination-searchable provenance marker** approved by
   product/security (e.g., a marker stored in the incident's `fields` or a searchable
   attribute).
3. **Explicit operator-supplied source→destination adoption mappings** with **no
   automatic heuristic adoption**.

If none of (1)–(3) is available, the RFC records that no-natural-key incidents have
no robust DR recovery, and the project either (a) limits incident sync to a
manual-reconciliation terminal state, or (b) defers incident sync until a marker is
approved.

**Per-type identity table (to be finalized in the RFC):** for each of the 17 types —
exact mapping key, rename semantics, duplicate-key behavior (never collapse legitimate
duplicates; never silently overwrite `_existing_resources_map`), lost-state recovery,
provenance for no-natural-key types. Composite natural keys containing an
incident-type ID differ across orgs until remapped; destination discovery builds
mapping keys by **reverse-mapping destination type IDs to source IDs** with
ambiguity detection.

**Conflict/rollback policy:** default `conflict → report/skip` for natural-key matches
without provenance. Only update a resource previously linked by durable
source→destination state, an approved marker, or an operator-approved adoption map.
Snapshot + restore every mutable config class (not only global/type settings).
Optimistic concurrency only where the API supplies a usable version/ETag; otherwise
refuse rollback overwrite and require manual reconciliation. **Never silently overwrite
an unrelated destination resource.**

## 7. Durability / saga backend

**Verified:** `BaseStorage` (sync-cli) exposes only `get` / `get_single` / `put`;
Azure uses `overwrite=True`; there is no compare-and-swap, create-if-absent,
generation/ETag, lease, or transactional API. The promised atomic intent, replay
fencing, and conflict resolution **cannot** be built on the current abstraction.

**[SPIKE] — choose one concrete design before model work:**
- (a) A generic conditional-object/lease API across every backend (local, S3, GCS,
  Azure) with fencing + stale-lease recovery; or
- (b) A proven single-writer orchestration invariant enforced outside sync-cli; or
- (c) Append-only attempt records with a deterministic winner protocol.

Specify exact object keys, atomicity boundary, retry behavior, lock expiry, downgrade
behavior, GC, privacy treatment, and handling of concurrent/replayed runs. This must
coexist with state pruning and source-ID filename sanitization. A unit test named
`test_concurrent_replayed_runs_resolved` is not a design.

The saga states: `intent → imported → barrier_confirmed → patched → complete`,
separate from normal destination resource state. A pre-call intent is written before
POST `/import`; the child gate reads a persisted `barrier_confirmed` saga state, not
an ordinary destination resource blob. The saga schema remains readable after binary
downgrade.

**No automatic compensating delete:** deletion may be disallowed by type
configuration, may itself trigger side effects, and is unsafe if barrier establishment
failed. On failure, leave the imported incident **quarantined**, persist the state,
alert, and require **audited manual reconciliation**.

## 8. Nested-resource discovery

One mechanism: child resource types accept parent incident IDs through
`get_resources_by_ids` (analogous to the existing `team_memberships` pattern), and
the external orchestrator chunks those IDs using its existing discovery infrastructure.

**Verified:** `ImportState` is write-only (no `.source` accessor) — parent IDs load
from durable bucket state, not in-memory state.

**[SPIKE]:** confirm `get_resources_by_ids` parent-ID fan-out suffices, or specify a
new checkpoint protocol (CLI input, checkpoint key/version, authoritative-snapshot
transition, retry semantics, concurrent-run fencing).

**CLI allowlists (command-scoped dual meaning):** nested child types are added to the
**import** id-file allowlist (IDs = parent incident IDs) and the **sync state-load**
allowlist (IDs = child source IDs). This dual meaning is intentional and must be
tested in implementation.

## 9. Dependency graph and tier table

The external orchestrator's resource-tier table (`[][]string`) is indexed
by integers. The final table must be **set-preserving**: no existing type is dropped.
Existing tiers 0–6 (verified):

- Tier 0: roles, teams, notebooks, rum_applications, logs_indexes, logs_archives,
  logs_pipelines, logs_custom_pipelines, logs_metrics, metric_percentiles,
  metric_tag_configurations, metrics_metadata, security_monitoring_rules,
  sensitive_data_scanner_groups, spans_metrics, host_tags,
  synthetics_mobile_applications, api_keys
- Tier 1: users, synthetics_private_locations, logs_restriction_queries,
  authn_mappings, synthetics_mobile_applications_versions, logs_indexes_order,
  logs_archives_order, logs_pipelines_order, sensitive_data_scanner_groups_order,
  sensitive_data_scanner_rules
- Tier 2: team_memberships, monitors, synthetics_tests
- Tier 3: synthetics_global_variables, service_level_objectives
- Tier 4: powerpacks, slo_corrections, downtimes, downtime_schedules,
  synthetics_test_suites
- Tier 5: dashboards
- Tier 6: restriction_policies, dashboard_lists

**Proposed insertion (set-preserving, to be finalized by the RFC):**

- Tier 0: + `incident_types`
- Tier 2: + `incident_user_defined_fields`, `incident_user_defined_roles`,
  `incident_impact_fields`, `incident_notification_templates`,
  `incident_postmortem_templates`, `incident_global_handles`
- Tier 3: + `incidents`
- Tier 4: + `incident_impacts`, `incident_integration_metadata`,
  `incident_todos`, `incident_attachments`, `incident_responders`,
  `incident_timestamp_overrides`
- Tier 6: + `incident_global_settings` (depends on dashboards)
- Tier 7 (NEW): `incident_notification_rules`, `incident_rules`

Edges are classified as **ID-remap** (sync-cli `resource_connections`), **semantic
ordering** (enforced in the external orchestrator's dependency-direction test
but not in `resource_connections`), or **safety** (children after parent barrier;
rules last). The `TestResourceTypeSyncTiers_SetEqualsExistingOrder` fixture must be
updated in the same commit (it asserts set-equality both directions + count).

## 10. Per-resource contracts

### 10.1 `incident_types` (Tier 0)
- Base: `/api/v2/incidents/config/types`; List/Get/Create/PATCH/DELETE.
- Mapping key: `attributes.name`.
- Owner: `attributes.created_by` (UUID `str`) / `relationships.created_by_user`.
- Default types (`is_default=true`) are system-managed: map by name (no POST), skip on
  update (`SkipResource`).
- **Behavior-changing config** (`attributes.configuration`: `allow_workflows`,
  `allow_incident_deletion`, `editable_timestamps`, `private_incidents`,
  `test_incidents`) is omitted in initial waves and preserved at destination unless a
  separate configuration-sync opt-in is enabled. Never overwrite an existing
  destination setting.
- Readonly strip: `created_at`, `modified_at`, `created_by`, `last_modified_by`.

### 10.2 `incident_user_defined_fields` (Tier 2)
- Base: `.../config/user-defined-fields`; List/Get/Create/PATCH/DELETE.
- Required `relationships.incident_type` on create.
- Mapping key: incident_type + name (type-scoped).
- Owner: `relationships.created_by_user`.
- **Behavior-changing fields** (`required`, defaults) omitted in the safe wave.

### 10.3 `incident_user_defined_roles` (Tier 2)
- Base: `.../config/user-defined-roles`; same shape as UDF; required `incident_type`.

### 10.4 `incident_impact_fields` (Tier 2)
- Base: `.../config/impact-fields`; **PUT** (not PATCH) for update.
- Required `incident_type`.

### 10.5 `incident_notification_templates` (Tier 2)
- Base: `.../config/notification-templates`; optional `incident_type`.
- Readonly: `created`, `modified`.

### 10.6 `incident_postmortem_templates` (Tier 2)
- Base: `.../config/postmortem-templates`; `incident_type` is **immutable** after
  create (update must not send it).
- **No `created_by_user`** in response (only `last_modified_by_user`). Owner policy:
  **[SPIKE]** — must resolve to a single deterministic choice (service-account
  ownership OR last-modifier approximation) before Wave 1; an OR is not carried into
  implementation. Fidelity loss documented.
- **Environment-specific settings** (Confluence/Google-Docs): remap / destination
  allowlist / strip-fail-closed / separate opt-in. Do not create externally backed
  templates until the side-effect matrix covers them.

### 10.7 `incident_global_handles` (Tier 2)
- Base: `.../config/global/incident-handles`; atypical collection CRUD.
- Mapping key: `attributes.name` (verified).
- `relationships.commander_user` (optional), `relationships.incident_type`
  (required), `relationships.created_by_user`.
- **[SPIKE] update/delete contract:** `incident_handle_data_request` requires `id`
  (update is NOT "PUT without id"); delete has no ID/body selector — spike the
  singleton/scoped semantics before treating handles as ordinary collection CRUD.

### 10.8 `incident_global_settings` (Tier 6, singleton)
- Base: `.../config/global/settings`; GET (singleton), PATCH; no create/delete.
- `attributes.analytics_dashboard_id` → depends on `dashboards`.
- Never overwrites an existing destination setting; separate opt-in; snapshot/restore
  with optimistic concurrency where the API supplies a version.

### 10.9 `incidents` (Tier 3, core)
- Base: `/api/v2/incidents`; create via `POST /import`; List/Get/PATCH/DELETE.
- `resource_connections`: `incident_types` (`attributes.incident_type_uuid`),
  `users` (`relationships.commander_user.data.id`,
  `relationships.declared_by_user.data.id`).
- Import-allowlist ONLY: `{title, declared, detected, fields, incident_type_uuid,
  resolved, visibility}`. `resolved` only valid when state field = `resolved`.
- Owner: `created_by_user` (optional) → `declared_by_user` → service account.
- Excluded: `id`, `created`, `modified`, `declared_by`, `declared_by_uuid`,
  `case_id`, `public_id`, `notification_handles`, child relationships
  (`attachments`, `impacts`, `integrations`, `responders`, `user_defined_fields`).
- **Fidelity losses (documented):** source `created`/`modified` timestamps are
  regenerated, not preserved; `archived` state, `public_id`, `case_id`,
  `notification_handles` (intentional), non-Datadog creator/declaring identity,
  `is_test`, `initial_cells` are not reproducible.

### 10.10 `incident_impacts` (Tier 4, nested)
- Base: `/incidents/{id}/impacts`; List/Create/PATCH/DELETE.
- `attributes.fields` is an object mapping impact-field **names** to values (verified)
  — there is **no ID remap** to `incident_impact_fields`; only a semantic ordering
  dependency (enforced in the external orchestrator's tier ordering, not in `resource_connections`).
- Owner: `relationships.created_by_user`.

### 10.11 `incident_integration_metadata` (Tier 4, nested)
- Base: `/incidents/{id}/relationships/integrations`.
- Contains Slack/Jira/Microsoft Teams identifiers and links → **environment policy**
  (remap / destination allowlist / strip-fail-closed). Not copied blindly.
- Owner: `relationships.created_by_user`.

### 10.12 `incident_todos` (Tier 4, nested)
- Base: `/incidents/{id}/relationships/todos`.
- `attributes.assignees` is `oneOf[str, IncidentTodoAnonymousAssignee]` (verified):
  only `str` items are user IDs (remapped against `users`); anonymous-assignee objects
  are passed through, NOT treated as users.
- `attributes.completed` is **writable** (verified) — preserved in create/update,
  NOT stripped.
- Owner: `relationships.created_by_user`.

### 10.13 `incident_attachments` (Tier 4, nested, partial)
- Base: `/incidents/{id}/attachments`; separate `POST .../attachments/postmortems`
  for postmortem attachments.
- **LIST response exposes only** `document_url`, `title`, `modified`,
  `attachment_type` (verified) — no postmortem template ID, cells, or full content.
- **Postmortem attachments are SKIPPED** (no read fidelity for cells/content/template
  id). Link attachments synced with `document_url` fidelity only (cross-org
  accessibility caveat documented).
- **No `incident_attachments → incident_postmortem_templates` remap** (not derivable
  from discovered records).
- Owner: parent incident's `created_by_user` (attachments expose no creator).

### 10.14 `incident_responders` (Tier 4, nested)
- Base: `/incidents/{id}/responders`; List/Get/Create/DELETE (no update).
- `resource_connections`: `incidents`, `users` (`relationships.user.data.id` — the
  responder user, for remapping).
- **Owner = `relationships.created_by.data.id`** (creator), NOT the responder.
- Create accepts only `relationships.user` (verified) — role assignments/metadata are
  not writable; **fidelity loss documented**.

### 10.15 `incident_timestamp_overrides` (Tier 4, nested)
- Base: `/incidents/{id}/timestamp-overrides`; List/Create/PATCH/DELETE.
- Owner: `relationships.created_by_user`.

## 11. Incident-rule round-trip (conditional support)

**Verified (finding 3):** `IncidentRuleDataAttributesRequest` accepts
`incident_type_uuid` (UUID), but `IncidentRuleDataAttributesResponse` does **not**
return it — it exposes `incident_settings_association_uuid` instead, and
`IncidentRuleDataResponse` has no relationships.

The proposed `incident_rules → incident_types` connection via
`attributes.incident_type_uuid` **cannot round-trip** from a discovered response.

**[SPIKE] — incident-rule disposition:** determine whether a stable
association-to-incident-type mapping exists through org settings, an include, or
another endpoint. If yes, document and test that mapping. If no, type-scoped incident
rules are **unsupported** (revises the resource count + graph).

## 12. Rules (installed disabled; activation is a distinct product workflow)

`incident_notification_rules` and `incident_rules` are installed with
`enabled=false` on **both create and update** (otherwise a second normal sync copies
source `enabled=true` and activates rules). Rules are canonicalized to `enabled=false`
**before mapping/diff** on a **copy** (source state is not mutated); the desired
source `enabled` value is read from source state for a separate post-cutover
activation workflow.

**Activation is NOT implemented in this project.** A separate follow-up project may
add an opt-in activation implementation with operator approval, portability
validation for handles/`task_payload`, post-batch activation, audit, and
immediate-disable rollback. Notification rules and automation incident rules have
**distinct risks and activation policies** — separate tests and gates per rule type.

**Environment dependencies:** notification-rule handles and incident-rule
`task_payload` can reference chat/ServiceNow/Jira/workflow/paging configs not
portable across orgs. Explicit validators per field: remap, destination allowlist,
strip-fail-closed, or separate opt-in.

## 13. Complete field/relationship fidelity + environment matrix

**[SPIKE] — produce the full matrix in the RFC (not deferred to implementation):**
for each response field, identify the create/update counterpart, transformation,
side-effect class, and destination validation.

Known asymmetries (verified in 2.61.0):
- UDF responses contain autocomplete `metadata` (`search_url`, parameters); the create
  request does not accept it → cannot be faithfully copied.
- Integration metadata contains Slack/Jira/Microsoft Teams identifiers and links →
  destination mapping/allowlist policy.
- Incident-type responses contain Google Meet, Microsoft Teams, and Zoom
  configuration relationships; create data has no relationships → strip/disposition.
- Notification-rule and incident-rule conditions/queries can reference
  destination-specific fields/values.
- Notification-template content and attachment URLs can contain environment-specific
  destinations.
- `created_by_user` on incidents is **optional** (verified), not non-nullable.

## 14. Behavior-changing-config classification

Every config model is classified as **inert data**, **behavior-changing config**, or
**action**:

- **Inert** (safe default wave): basic name/description fields.
- **Behavior-changing** (separate opt-in): UDF `required`/defaults, postmortem
  `is_default`/external storage, global handles, user-defined roles,
  notification/automation conditions, incident-type `configuration` flags
  (`allow_workflows`, `allow_incident_deletion`, `editable_timestamps`,
  `private_incidents`, `test_incidents`).
- **Action** (skipped): on-call page, ServiceNow, AI postmortem, page-link.

Behavior-changing config is behind separate opt-ins; destination values are
preserved by default; merge/conflict/rollback policy per field.

## 15. Replication vs. sync (product mode)

With cleanup disabled for the entire incident family, source deletions of todos,
attachments, responders, rules, templates, and configuration are **not converged**.
This is **replication, not full sync**.

This is an explicit product mode and acceptance criterion. UI/docs/metrics say
"replicated, deletion not converged" until deletion qualification (a separate wave)
passes. The evidence needed to enable deletion per type is defined, especially where
delete itself can produce side effects.

## 16. Rollback (per wave)

- Which writes are intentionally left in place.
- How copied rules are re-disabled if an external actor enabled them.
- How barrier status is audited for all created incidents.
- How global/type settings are restored from a pre-change snapshot (durable snapshot
  location, optimistic-concurrency policy, protection against overwriting
  administrator changes made after the snapshot).
- How the saga/state schema remains readable after binary downgrade.
- When manual deletion is permitted and how it avoids notifications.
- **No automatic compensating delete.**

## 17. Implementation sequence (conditioned on RFC approval)

1. **Wave 0 — Generic durability prep:** conditional-object/lease (or append-only
   winner) API across every storage backend. No incident code.
2. **Wave 1 — True vertical slice:** feature gate + shared infrastructure (manifest,
   saga, barrier, nested helper) + `incident_types` + minimum safe UDF + `incidents`
   (import + barrier + saga) + **one dependency-light child** (e.g.
   `incident_todos` or `incident_timestamp_overrides`, NOT `incident_impacts` which
   depends on `incident_impact_fields`) + nested parent-ID CLI allowlists +
   matching external-orchestrator companion (tiers, owners, OBO chunking, both-DC flag
   wiring). The controlled-org canary (no-side-effect + ownership) runs through the
   real cross-org OBO path before any fan-out.
3. **Wave 2 — Behavior-inert config:** remaining type-scoped config without
   environment/behavior-changing fields (`incident_user_defined_roles`,
   `incident_impact_fields`, `incident_notification_templates`,
   `incident_global_handles`). `incident_impact_fields` lands here so `incident_impacts`
   (Wave 3) has its semantic dependency satisfied.
4. **Wave 3 — Incident children:** `incident_impacts`, `incident_integration_metadata`,
   `incident_responders`, `incident_attachments`, `incident_timestamp_overrides` (if
   not in Wave 1).
5. **Wave 4 — Environment-backed / behavior-changing config (separate opt-in):**
   `incident_postmortem_templates`, `incident_global_settings`, behavior-changing
   fields on `incident_types`/UDF.
6. **Wave 5 — Rules (installed disabled):** `incident_notification_rules`,
   `incident_rules` (if §11 resolves as supported). Activation is a distinct product
   workflow, not in this project.
7. **Wave 6 — Deletion qualification:** separate from initial incident support.

Each wave is independently releasable; a narrow external-orchestrator companion
change lands **with** each OSS wave (not at the end); sync-cli release publication +
orchestrator binary pinning at each boundary.

## 18. Open questions requiring the [SPIKE]

1. Configuration recovery contract (§4): auto-present after import? upsert algorithm?
   pre-existing ID recovery?
2. Identity/idempotency strategy (§6): provenance marker? operator mappings?
   manual-reconciliation?
3. Saga/durability backend (§7): conditional API vs single-writer invariant vs
   append-only winner?
4. Parent-discovery checkpoint protocol (§8): does `get_resources_by_ids` suffice?
5. Incident-rule type-scoping (§11): stable association-to-type mapping, or
   unsupported?
6. Global-handle update/delete contract (§10.7): singleton/scoped semantics?
7. API permissions per endpoint under OBO identities (§5): settings-write requirements?
8. Side-effect experiments in a controlled org (§4): tri-state classification with
   provenance.

These must be resolved and signed off before Wave 1 coding begins.
