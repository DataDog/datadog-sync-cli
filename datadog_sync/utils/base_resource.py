# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import abc
import asyncio
from asyncio import Lock, Semaphore
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, ClassVar, Optional, Dict, List, Tuple, Union

from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.resource_utils import (
    DEFAULT_TAGS,
    CustomClientHTTPError,
    FilteredResource,
    SkipResource,
    find_attr,
    ResourceConnectionError,
)
from datadog_sync.constants import Metrics

if TYPE_CHECKING:
    from datadog_sync.utils.configuration import Configuration


@dataclass
class TaggingConfig:
    path: str
    path_list: List[str] = field(init=False, default_factory=list)
    default_tags: ClassVar[List[str]] = DEFAULT_TAGS

    def __post_init__(self) -> None:
        self.path_list = self.path.split(".")

    def add_default_tags(self, resource: Dict) -> None:
        tmp = resource
        for p in self.path_list:
            if tmp is None:
                break

            val = tmp.get(p, None)
            if p == self.path_list[-1]:
                if val is None:
                    # Copy so callers don't share the class-level default_tags list.
                    tmp[p] = list(self.default_tags)
                    return
                else:
                    # Idempotent append: only add tags not already present, preserving order.
                    for t in self.default_tags:
                        if t not in tmp[p]:
                            tmp[p].append(t)
                    return
            else:
                tmp = val


@dataclass
class ResourceConfig:
    base_path: str
    resource_connections: Optional[Dict[str, List[str]]] = None
    non_nullable_attr: Optional[List[str]] = None
    null_values: Optional[Dict[str]] = None
    excluded_attributes: Optional[List[str]] = None
    concurrent: bool = True
    deep_diff_config: dict = field(default_factory=lambda: {"ignore_order": True})
    tagging_config: Optional[TaggingConfig] = None
    async_lock: Optional[Lock] = None
    non_nullable_list_vals: Optional[List[Tuple[str, Dict[str, str]]]] = None
    resource_mapping_key: Optional[Union[str, Callable[[Dict], str]]] = None
    skip_resource_mapping: bool = False
    # Attr-name prefixes that the LIST endpoint omits from response items.
    # When non-empty, two changes to --filter semantics:
    #   1) resources_handler._import_resource partitions the user's --filter
    #      list at LIST-time. Filters whose attr_name starts with any of
    #      these prefixes are "list-unsafe" — the LIST item cannot decide
    #      them — and are DEFERRED to the post-GET pass. Filters that don't
    #      reference these prefixes ("list-safe") still run at LIST-time and
    #      short-circuit normally, preserving the per-page rejection
    #      optimization for the common case (filter by name, tags, status).
    #   2) base_resource._import_resource evaluates the full --filter set
    #      against the per-id GET payload and raises FilteredResource on
    #      rejection. The handler buckets FilteredResource as `filtered`
    #      (matching the LIST-time pre-filter accounting).
    # Example: notebooks set this to ["attributes.cells"] — the LIST drops
    # include_cells, so a filter on attributes.cells.X cannot be decided at
    # LIST-time. A filter on attributes.name still short-circuits at
    # LIST-time without forcing a per-id GET for every rejected notebook.
    # The previous boolean form (skip LIST-time filter entirely when set)
    # made metadata-only filters force a per-id GET per item, a wall-clock
    # / rate-limit regression for the common filter-by-name use case.
    list_omitted_attr_prefixes: List[str] = field(default_factory=list)
    # Optional per-resource-type concurrency cap. When set to a positive int N,
    # limits _create_resource / _update_resource to at most N in-flight for this
    # resource type, regardless of the global --max-workers value. Motivating
    # case: the destination monitors API's edge/proxy layer times out
    # (HTTP 512) when sync-cli's 32-way worker pool queues too many concurrent
    # POST/PUT /api/v1/monitor requests. Setting max_concurrent=8 on the
    # Monitors resource caps the pressure on that specific endpoint without
    # slowing the other resource types.
    #
    # `concurrent=False` continues to serialize a resource type completely (via
    # `async_lock`). `max_concurrent` is the semaphore-based, N>1 middle ground.
    # When both are set, `concurrent=False` wins (serial behavior).
    max_concurrent: Optional[int] = None
    async_semaphore: Optional[Semaphore] = None

    async def init_async(self) -> None:
        # Both Lock and Semaphore bind to the current running event loop on
        # construction (Python 3.9). Instantiating them at dataclass
        # __post_init__ time can pin them to a loop that's about to be
        # replaced (e.g. tests that call asyncio.run per test-case get a
        # fresh loop each time). Always construct inside init_async() which
        # runs under the loop we're going to use.
        self.async_lock = Lock()
        # Always clear async_semaphore first so a re-init with
        # max_concurrent=None/0 disables the cap. Long-lived wrappers that
        # reuse the same Configuration across orgs (setting a cap for one org
        # then unsetting it for the next) would otherwise keep honoring a
        # stale semaphore from the previous org.
        self.async_semaphore = None
        if self.max_concurrent and self.max_concurrent > 0:
            self.async_semaphore = Semaphore(self.max_concurrent)

    def __post_init__(self) -> None:
        self.build_excluded_attributes()
        # Note: async primitives (async_lock, async_semaphore) are constructed
        # by init_async() so they bind to the correct running loop. Prior
        # code constructed async_lock here when concurrent=False; keep that
        # for backward compatibility with callers that acquire the lock
        # without first calling init_async(). Semaphore has no such legacy
        # path, so it's created only in init_async().
        if not self.concurrent:
            self.async_lock = Lock()

    def build_excluded_attributes(self) -> None:
        if self.excluded_attributes:
            for i, attr in enumerate(self.excluded_attributes):
                self.excluded_attributes[i] = "root" + "".join(["['{}']".format(v) for v in attr.split(".")])


class BaseResource(abc.ABC):
    resource_type: str
    resource_config: ResourceConfig

    def __init__(self, config: Configuration) -> None:
        self.config = config
        self._existing_resources_map: Dict[str, Dict] = {}
        if not self.resource_config.skip_resource_mapping and self.resource_config.resource_mapping_key is None:
            raise ValueError(
                f"Resource {self.resource_type} has skip_resource_mapping=False "
                f"but resource_mapping_key is not defined"
            )

    async def init_async(self):
        await self.resource_config.init_async()

    def get_resource_mapping_key(self, resource: Dict) -> Optional[str]:
        """Extract the mapping key from a resource using resource_mapping_key config.

        Supports dot-path strings (e.g. "attributes.email") and callables.
        Returns None if resource_mapping_key is not configured, if the dot-path
        traversal encounters a missing key, if the terminal value is None, or if
        a callable raises an exception.
        """
        key_config = self.resource_config.resource_mapping_key
        if key_config is None:
            return None
        if callable(key_config):
            try:
                return key_config(resource)
            except (KeyError, TypeError, AttributeError):
                return None
        # Dot-path traversal
        tmp = resource
        for part in key_config.split("."):
            if not isinstance(tmp, dict) or part not in tmp:
                return None
            tmp = tmp[part]
        if tmp is None:
            return None
        return str(tmp)

    async def map_existing_resources(self) -> None:
        """Fetch destination resources and populate _existing_resources_map.

        Default implementation fetches all destination resources via
        self.get_resources(destination_client) and builds _existing_resources_map
        keyed by resource_mapping_key.

        Tier 2 resources override this for custom fetch/mapping logic.
        """
        dest_resources = await self.get_resources(self.config.destination_client)
        self._existing_resources_map = {}
        for resource in dest_resources:
            key = self.get_resource_mapping_key(resource)
            if key is not None:
                self._existing_resources_map[key] = resource

    @abc.abstractmethod
    async def get_resources(self, client: CustomClient) -> List[Dict]:
        pass

    async def _get_resources(self, client: CustomClient) -> List[Dict]:
        r = self.get_resources(client)
        return await r

    async def get_resources_by_ids(
        self,
        client: CustomClient,
        ids: List[str],
        max_concurrent_reads: int = 10,
    ) -> Tuple[List[Dict], List[str], List[Tuple[str, str, str]]]:
        """Fetch specific resources by ID instead of listing all.

        Returns: (resources, missing_ids, errored_ids)
          - resources: successfully-fetched resource dicts
          - missing_ids: IDs returning HTTP 404 (resource deleted between enum and fetch)
          - errored_ids: list of (id, class, reason) where class in {"transient","permanent","skipped"}

        Reuses the model's existing import_resource(_id=...) primitive, which inherits
        per-model semantics (envelope unwrap, SkipResource, etc.). Concurrency bounded by
        asyncio.Semaphore(max_concurrent_reads), separate from --max-workers.

        Models whose import_resource has the `if _id:` guard pattern (monitors, SLOs,
        downtimes, synthetics) work with this default impl. Dashboards and notebooks
        always GET to fetch per-item bodies (widgets / cells) that the LIST endpoint
        omits — their import_resource also short-circuits when the caller already
        supplied a full body (detected by widgets/cells presence), so the --id-file
        path here does exactly one GET per ID: the get_resources_by_ids call below
        fetches each body, the queue handler later passes the same body back into
        import_resource which returns it as-is rather than re-fetching.
        """
        sem = asyncio.Semaphore(max_concurrent_reads)
        resources: List[Dict] = []
        missing: List[str] = []
        errored: List[Tuple[str, str, str]] = []

        # Lazy import: aiohttp is already a sync-cli dependency (custom_client.py)
        # but importing at module load creates a heavier import graph for tests.
        import aiohttp

        async def fetch_one(id_: str):
            async with sem:
                try:
                    _, resource = await self.import_resource(_id=id_)
                    return ("ok", resource)
                except SkipResource as e:
                    return ("skipped", id_, str(e))
                except CustomClientHTTPError as e:
                    if e.status_code == 404:
                        return ("missing", id_)
                    if e.status_code == 429 or e.status_code >= 500:
                        return ("transient", id_, f"HTTP {e.status_code}")
                    return ("permanent", id_, f"HTTP {e.status_code}")
                except asyncio.TimeoutError:
                    return ("transient", id_, "timeout")
                except aiohttp.ClientError as e:
                    # Includes ClientConnectionError (DNS, connection refused, TCP reset),
                    # ServerDisconnectedError, etc. These are transport-shaped failures
                    # that downstream consumers should treat the same as 5xx/429.
                    return ("transient", id_, f"connection error: {type(e).__name__}")
                except Exception as e:
                    # request_with_retry() in custom_client.py raises a plain Exception
                    # with the literal prefix "retry limit exceeded" when its retry budget
                    # is exhausted without an inner ClientResponseError firing first.
                    # Treat that case as transient so sustained-throttling surfaces as
                    # rate-limit-shaped exit, not silent partial success.
                    # NOTE: substring match couples to a log-message format. The companion
                    # test test_request_with_retry_message_contract pins the message shape
                    # so a refactor in custom_client.py will fail the test rather than
                    # silently demote retry-budget exhaustion back to permanent.
                    msg = str(e)
                    if "retry limit exceeded" in msg:
                        return ("transient", id_, msg[:200])
                    return ("permanent", id_, msg[:200])

        results = await asyncio.gather(*(fetch_one(i) for i in ids))
        for r in results:
            tag = r[0]
            if tag == "ok":
                resources.append(r[1])
            elif tag == "missing":
                missing.append(r[1])
            elif tag == "skipped":
                errored.append((r[1], "skipped", r[2]))
            elif tag == "transient":
                errored.append((r[1], "transient", r[2]))
            else:
                errored.append((r[1], "permanent", r[2]))
        return resources, missing, errored

    @abc.abstractmethod
    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        pass

    async def _import_resource(
        self,
        _id: Optional[str] = None,
        resource: Optional[Dict] = None,
        skip_filter: bool = False,
    ) -> str:
        _id, r = await self.import_resource(_id, resource)

        # Post-GET filter re-evaluation for models whose LIST omits fields that
        # filters may reference (notebooks' cells, dashboards' widgets). The
        # LIST-time filter in resources_handler defers list-unsafe filters
        # (those referencing list_omitted_attr_prefixes) to this pass; the
        # post-GET payload has the full body so all filters can be evaluated
        # authoritatively here. Resources rejected here raise FilteredResource
        # — the handler buckets them as `filtered`, not `skipped`. State is
        # not written. skip_filter=True bypasses this check for the
        # --force-missing-dependencies path: a filtered-out dep that another
        # kept resource depends on must be imported anyway, or downstream
        # ID remapping in run_sorter() will fail with unresolved references.
        if not skip_filter and self.resource_config.list_omitted_attr_prefixes and not self.filter(r):
            raise FilteredResource(str(_id), self.resource_type)

        if self.resource_config.tagging_config is not None:
            try:
                self.resource_config.tagging_config.add_default_tags(r)
            except Exception as e:
                self.config.logger.warning(
                    f"Error while adding default tags to resource {self.resource_type}. {str(e)}"
                )

        # Use the SourceStateWriter protocol method so this works against both
        # State (which loaded prior data on construction) and ImportState
        # (write-only; constructed via --skip-state-load). Direct
        # `state.source[type][id] = r` would only work on State and would
        # AttributeError on ImportState, which has no .source accessor.
        self.config.state.set_source(self.resource_type, str(_id), r)
        return str(_id)

    @abc.abstractmethod
    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        pass

    async def _pre_resource_action_hook(self, _id, resource: Dict) -> None:
        return await self.pre_resource_action_hook(_id, resource)

    @abc.abstractmethod
    async def pre_apply_hook(self) -> None:
        pass

    async def _pre_apply_hook(self) -> None:
        return await self.pre_apply_hook()

    async def _fetch_destination_org_principal(
        self,
        has_policy: Callable[[Dict], bool],
        current_user_path: str = "/api/v2/current_user",
    ) -> Optional[str]:
        """Fetch the destination org UUID as `org:{uuid}` — but only if at least
        one source-side resource passes `has_policy(resource)`.

        Used by resources that remap `org:` principals in restriction_policy
        bindings (monitors, synthetics_tests, restriction_policies). Skipping
        the /api/v2/current_user call for policy-free syncs removes an
        unnecessary API call and a failure dependency for callers whose
        credentials lack current_user access.

        Returns None when no source resource carries a policy (nothing to
        remap). Otherwise returns "org:{destination_org_uuid}". Callers
        should assign the return value to self.org_principal.

        Re-raises exceptions from the current_user GET after logging, so the
        caller matches the surrounding framework's warn-and-continue behavior.
        """
        source_state = self.config.state.source.get(self.resource_type, {})
        if not any(has_policy(r) for r in source_state.values()):
            return None
        destination_client = self.config.destination_client
        try:
            resp = await destination_client.get(current_user_path)
            org_id = resp["data"]["relationships"]["org"]["data"]["id"]
            return f"org:{org_id}"
        except Exception as e:
            self.config.logger.error(f"Failed to get org details: {e}")
            raise

    @abc.abstractmethod
    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        pass

    async def _create_resource(self, _id: str, resource: Dict) -> None:
        _id, r = await self.create_resource(_id, resource)
        self.config.state.destination[self.resource_type][_id] = r

    @abc.abstractmethod
    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        pass

    async def _update_resource(self, _id: str, resource: Dict) -> None:
        _id, r = await self.update_resource(_id, resource)
        self.config.state.destination[self.resource_type][_id] = r

    @abc.abstractmethod
    async def delete_resource(self, _id: str) -> None:
        pass

    async def _delete_resource(self, _id: str) -> None:
        try:
            await self.delete_resource(_id)
        except CustomClientHTTPError as e:
            if e.status_code == 404:
                self.config.state.destination[self.resource_type].pop(_id, None)
                return None

            raise e

        self.config.state.destination[self.resource_type].pop(_id, None)

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        resources = self.config.state.destination[resource_to_connect]

        failed_connections = []
        if isinstance(r_obj[key], list):
            for i, v in enumerate(r_obj[key]):
                _id = str(v)
                if _id in resources:
                    # Cast resource id to str or int based on source type
                    type_attr = type(v)
                    r_obj[key][i] = type_attr(resources[_id]["id"])
                else:
                    failed_connections.append(_id)
        else:
            _id = str(r_obj[key])
            if _id in resources:
                # Cast resource id to str on int based on source type
                type_attr = type(r_obj[key])
                r_obj[key] = type_attr(resources[_id]["id"])
            else:
                failed_connections.append(_id)

        return failed_connections

    def _resolve_or_drop(self, plain_id: str, resource_to_connect: str) -> Tuple[Optional[str], bool]:
        """Resolve plain_id (no "type:" prefix) against destination state.

        Returns (resolved_destination_id, is_permanently_stale):
          - Present in state.destination[resource_to_connect]: returns
            (destination_id, False) -- success path, caller keeps/remaps this entry.
          - Absent from destination: calls ensure_resource_loaded() (for
            --minimize-reads correctness), then rechecks destination because the lazy load
            may have populated its mapping, and only then checks source.
              - Present in source ("not yet synced"): returns (None, False) -- caller
                must treat this as today's hard-fail (add plain_id to failed_connections);
                NOT a drop. This is the legitimate "retry on a later sync" case.
              - Absent from source (permanently gone -- deleted before this org's
                first-ever import, or never existed): returns (None, True) -- caller
                drops this entry ONLY if self.config.drop_unresolvable_principals is True;
                if the flag is False, caller must treat this identically to the "not yet
                synced" case (today's unchanged hard-fail).

        Callers own: parsing "type:id" composites to plain_id before calling, reassembling
        "type:id" on success, deciding what "the list" means for their shape
        (binding.principals vs. flat restricted_roles), and the "list is now empty"
        access-elevation check.

        state.destination/state.source are defaultdict(dict), so indexing an unknown
        resource_to_connect returns {} rather than raising -- plain `in` membership checks
        are safe and no KeyError guard is needed. An exception raised by
        ensure_resource_loaded (e.g. a storage error) propagates uncaught, matching every
        other ensure_resource_loaded call site.
        """
        destination = self.config.state.destination[resource_to_connect]
        if plain_id in destination:
            return destination[plain_id]["id"], False

        self.config.state.ensure_resource_loaded(resource_to_connect, plain_id)
        destination = self.config.state.destination[resource_to_connect]
        if plain_id in destination:
            return destination[plain_id]["id"], False

        if plain_id in self.config.state.source[resource_to_connect]:
            return None, False
        return None, True

    # Composite "type:id" principal type prefixes -> the resource_to_connect they map to.
    _PRINCIPAL_TYPE_MAP: ClassVar[Dict[str, str]] = {"user": "users", "role": "roles", "team": "teams"}

    def _filter_stale_binding_principals(
        self, _id: str, bindings: Optional[List[Dict]]
    ) -> Tuple[Dict[str, List[str]], bool]:
        """Resolve/drop/hard-fail composite "type:id" principals across restriction-policy
        bindings (shared by restriction_policies / monitors / synthetics_tests).

        For each principal in each binding: resolve against destination (remap in place),
        else consult source. Source-present ("not yet synced") or flag-off -> hard-fail
        (add to failed_connections). Source-absent ("permanently gone") AND
        --drop-unresolvable-principals -> drop it, WARN, and count it.

        Rebuilds each binding's "principals" as a NEW list (never del/pop/index-assign
        during iteration) to avoid the enumerate/index-shift skip bug. Returns
        (failed_connections_dict, empty_binding_risk) where empty_binding_risk is True if
        any binding whose source list was non-empty became empty after dropping.
        """
        failed_connections_dict: Dict[str, List[str]] = defaultdict(list)
        empty_binding_risk = False
        for binding in bindings or []:
            principals = binding.get("principals")
            if not principals:
                continue
            had_principals = len(principals) > 0
            kept: List[str] = []
            for policy_id in principals:
                parts = policy_id.split(":", 1)
                if len(parts) != 2:
                    kept.append(policy_id)
                    continue
                _type, plain_id = parts
                resource_to_connect = self._PRINCIPAL_TYPE_MAP.get(_type)
                if resource_to_connect is None:
                    # org: (already remapped by pre_resource_action_hook) or any other
                    # non-user/role/team principal -> pass through untouched.
                    kept.append(policy_id)
                    continue
                resolved, stale = self._resolve_or_drop(plain_id, resource_to_connect)
                if resolved is not None:
                    kept.append(f"{_type}:{resolved}")
                elif stale and self.config.drop_unresolvable_principals:
                    self.config.logger.warning(
                        f"dropping stale principal '{policy_id}': absent from source and "
                        "destination (likely deleted before the org's first sync)",
                        resource_type=self.resource_type,
                        _id=_id,
                    )
                    if self.config.counter is not None:
                        self.config.counter.record_stale_principal_dropped(resource_type=self.resource_type, _id=_id)
                else:
                    # Source-present ("not yet synced", retry later) or flag off:
                    # unchanged hard-fail semantics -- keep the original id and record it.
                    kept.append(policy_id)
                    failed_connections_dict[resource_to_connect].append(plain_id)
            binding["principals"] = kept
            if had_principals and not kept:
                empty_binding_risk = True
        return failed_connections_dict, empty_binding_risk

    def _filter_stale_flat_roles(self, _id: str, container: Optional[Dict], key: str) -> Tuple[List[str], bool]:
        """Resolve/drop/hard-fail a flat list of role ids at container[key]
        (restricted_roles / options.restricted_roles / metadata.restricted_roles).

        Same three-way logic as _filter_stale_binding_principals but for plain role ids
        (no "type:" prefix). Rebuilds the list as a NEW list. Returns
        (failed_role_ids, empty_list_risk) where empty_list_risk is True if a non-empty
        source list became empty after dropping.
        """
        failed: List[str] = []
        if not container:
            return failed, False
        roles = container.get(key)
        if not roles:
            return failed, False
        had_roles = len(roles) > 0
        kept: List[str] = []
        for role in roles:
            plain_id = str(role)
            resolved, stale = self._resolve_or_drop(plain_id, "roles")
            if resolved is not None:
                kept.append(type(role)(resolved))
            elif stale and self.config.drop_unresolvable_principals:
                self.config.logger.warning(
                    f"dropping stale role '{plain_id}' from {key}: absent from source and "
                    "destination (likely deleted before the org's first sync)",
                    resource_type=self.resource_type,
                    _id=_id,
                )
                if self.config.counter is not None:
                    self.config.counter.record_stale_principal_dropped(resource_type=self.resource_type, _id=_id)
            else:
                kept.append(role)
                failed.append(plain_id)
        container[key] = kept
        return failed, (had_roles and not kept)

    def _raise_connection_error_if_any(
        self, _id: str, failed_connections_dict: Dict[str, List[str]], empty_binding_risk: bool = False
    ) -> bool:
        """Shared terminal step for the drop-aware connect_resources overrides.

        Mirrors the base connect_resources raise/skip behavior: raise unless
        --skip-failed-resource-connections is set. empty_binding_risk is threaded onto the
        exception so _apply_resource_cb can tag skipped-resource metrics. When
        --skip-failed-resource-connections suppresses the exception, returns True for an
        empty-binding risk so the apply path can tag the successful action and include it in
        the end-of-run escalation summary. Otherwise returns False.
        """
        if not failed_connections_dict and not empty_binding_risk:
            return False
        e = ResourceConnectionError(
            failed_connections_dict=failed_connections_dict, empty_binding_risk=empty_binding_risk
        )
        if empty_binding_risk:
            if self.config.skip_failed_resource_connections:
                self.config.logger.error(
                    "access-elevation risk: a binding/list whose source had principals became "
                    "empty after dropping unresolvable references; "
                    "--skip-failed-resource-connections is enabled, continuing sync. "
                    "DESTINATION RESOURCE MAY BE UNRESTRICTED",
                    resource_type=self.resource_type,
                    _id=_id,
                )
            else:
                self.config.logger.error(
                    "access-elevation risk: a binding/list whose source had principals became "
                    "empty after dropping unresolvable references; refusing to sync",
                    resource_type=self.resource_type,
                    _id=_id,
                )
        if not self.config.skip_failed_resource_connections:
            self.config.logger.info(
                f"skipping resource: {str(e)}",
                _id=_id,
                resource_type=self.resource_type,
            )
            raise e
        else:
            self.config.logger.debug(f"{str(e)}", _id=_id, resource_type=self.resource_type)
            return empty_binding_risk

    def extract_source_ids(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        """Extract dependency IDs referenced at r_obj[key] for resource_to_connect.

        Source-only: does NOT check destination state, does NOT mutate r_obj.
        Override in subclasses with custom connect_id logic (regex, prefix
        parsing, type dispatch, etc.).
        Mirror of connect_id -- keep in sync when connect_id changes.
        """
        if not r_obj.get(key):
            return None
        if isinstance(r_obj[key], list):
            return [str(v) for v in r_obj[key]]
        return [str(r_obj[key])]

    def connect_resources(self, _id: str, resource: Dict) -> None:
        if not self.resource_config.resource_connections:
            return

        failed_connections_dict = defaultdict(list)
        for resource_to_connect, v in self.resource_config.resource_connections.items():
            for attr_connection in v:
                c = find_attr(attr_connection, resource_to_connect, resource, self.connect_id)
                if c:
                    failed_connections_dict[resource_to_connect].extend(c)

        if failed_connections_dict:
            e = ResourceConnectionError(failed_connections_dict=failed_connections_dict)
            if not self.config.skip_failed_resource_connections:
                e = ResourceConnectionError(failed_connections_dict=failed_connections_dict)
                self.config.logger.info(
                    f"skipping resource: {str(e)}",
                    _id=_id,
                    resource_type=self.resource_type,
                )
                raise e
            else:
                self.config.logger.debug(f"{str(e)}", _id=_id, resource_type=self.resource_type)

    def filter(self, resource: Dict) -> bool:
        if not self.config.filters or self.resource_type not in self.config.filters:
            return True

        if self.config.filter_operator.lower() == "and":
            for _filter in self.config.filters[self.resource_type]:
                if not _filter.is_match(resource):
                    return False
            # All resources successfully matched
            return True
        else:
            # Fallback to 'OR' logic. Filter in if any filter applies to resource
            for _filter in self.config.filters[self.resource_type]:
                if _filter.is_match(resource):
                    return True
            # Filter was specified for resource type but resource did not match any
            return False

    async def _send_action_metrics(self, action: str, _id: str, status: str, tags: Optional[List[str]] = None) -> None:
        if not tags:
            tags = []
        if _id:
            tags.append(f"id:{_id}")
        tags.append(f"action_type:{action}")
        tags.append(f"status:{status}")
        tags.append(f"resource_type:{self.resource_type}")
        try:
            await self.config.destination_client.send_metric(Metrics.ACTION.value, tags + ["client_type:destination"])
            self.config.logger.debug(f"Sent metrics to destination for {self.resource_type}")
        except Exception as e:
            self.config.logger.debug(f"Failed to send metrics to destination for {self.resource_type}: {str(e)}")

        try:
            await self.config.source_client.send_metric(Metrics.ACTION.value, tags + ["client_type:source"])
            self.config.logger.debug(f"Sent metrics to source for {self.resource_type}")
        except Exception as e:
            self.config.logger.debug(f"Failed to send metrics to source for {self.resource_type}: {str(e)}")
