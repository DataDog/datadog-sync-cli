# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from __future__ import annotations
import json
import logging
import re
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple
from datetime import datetime, timedelta, timezone

from deepdiff import DeepDiff
from dateutil.parser import parse
from dateutil.rrule import rrulestr
from dateutil.tz import datetime_exists, gettz

from datadog_sync.constants import LOGGER_NAME
from datadog_sync.utils.base_resource import BaseResource, ResourceConfig
from datadog_sync.utils.custom_client import PaginationConfig
from datadog_sync.utils.resource_utils import (
    CustomClientHTTPError,
    DowntimeSchedulesDateOperator,
    SkipResource,
)

if TYPE_CHECKING:
    from datadog_sync.utils.custom_client import CustomClient

log = logging.getLogger(LOGGER_NAME)

# Substring of the destination API's 400 body when a create collides with an
# equivalent existing downtime, e.g.
#   {"errors":["The downtime being created is a duplicate of one or more
#    existing downtimes: ['<id>']"]}
_DUPLICATE_DOWNTIME_MARKER = "duplicate of one or more existing downtimes"
_DOWNTIME_NOT_FOUND_MARKER = "Downtime not found"
_SINGLE_DUPLICATE_ID_RE = re.compile(r"existing downtimes:\s*\[\s*(['\"])(?P<id>[^'\"]+)\1\s*\]\s*$")
_RRULE_COUNT_RE = re.compile(r"(?:^|;)COUNT=(?P<count>\d+)(?=;|$)", re.IGNORECASE)
_RRULE_UNTIL_RE = re.compile(r"(?:^|;)UNTIL=(?P<until>[^;]+)(?=;|$)", re.IGNORECASE)


class _CancelDestinationDowntime(Exception):
    """Signal that an exhausted source must cancel an active destination."""


class _RecurrenceExpansionLimit(Exception):
    """Raised when an RRULE requires unsafe amounts of synchronous work."""


@dataclass(frozen=True)
class _ActiveWindow:
    start: datetime
    end: Optional[datetime]


@dataclass(frozen=True)
class _RecurrencePlan:
    timezone_name: str
    active_window: Optional[_ActiveWindow]
    future_recurrences: Tuple[Dict, ...]


class _RecurrenceUpdateAction(Enum):
    PATCH = "patch"
    NORMALIZE_PATCH = "normalize_patch"
    OMIT_SCHEDULE = "omit_schedule"
    CANCEL = "cancel"
    RECREATE = "recreate"


@dataclass(frozen=True)
class _PreparedRecurrenceUpdate:
    action: _RecurrenceUpdateAction
    create_schedule: Dict


class DowntimeSchedules(BaseResource):
    _MAX_RRULE_OCCURRENCES = 100_000
    _MAX_API_RECURRENCES = 5
    _BRIDGE_RRULE = "FREQ=DAILY;COUNT=1"
    resource_type = "downtime_schedules"
    resource_config = ResourceConfig(
        resource_connections={"monitors": ["attributes.monitor_identifier.monitor_id"]},
        non_nullable_attr=[],
        base_path="/api/v2/downtime",
        excluded_attributes=[
            "id",
            "attributes.modified",
            "attributes.created",
            "attributes.status",
            "attributes.canceled",
            "relationships",
            "attributes.schedule.current_downtime",
        ],
        deep_diff_config={
            "ignore_order": True,
            "custom_operators": [DowntimeSchedulesDateOperator()],
        },
        skip_resource_mapping=True,
    )
    pagination_config = PaginationConfig(
        page_size=100,
        page_size_param="page[limit]",
        page_number_param="page[offset]",
        page_number_func=lambda idx, page_size, page_number: page_number + page_size,
        remaining_func=lambda *args: 1,
    )
    # Additional DowntimeSchedules specific attributes

    def __init__(self, config) -> None:
        super().__init__(config)
        self._prepared_recurrence_updates: Dict[str, _PreparedRecurrenceUpdate] = {}

    async def get_resources(self, client: CustomClient) -> List[Dict]:
        # `include=created_by` populates `relationships.created_by.data.id` on
        # each downtime. Downstream consumers (e.g. HAMR managed-sync's OBO
        # grouper) key on that field to route the resource under its creator's
        # identity; without the include, the LIST response omits `relationships`
        # entirely and downstream code falls back to a service-account identity.
        resp = await client.paginated_request(client.get)(
            self.resource_config.base_path,
            pagination_config=self.pagination_config,
            params={"include": "created_by"},
        )

        return resp

    async def import_resource(self, _id: Optional[str] = None, resource: Optional[Dict] = None) -> Tuple[str, Dict]:
        if _id:
            source_client = self.config.source_client
            resource = (
                await source_client.get(
                    self.resource_config.base_path + f"/{_id}",
                    params={"include": "created_by"},
                )
            )["data"]

        if resource["attributes"].get("canceled"):
            raise SkipResource(resource["id"], self.resource_type, "Downtime is canceled.")

        return str(resource["id"]), resource

    @staticmethod
    def _parse_utc(value):
        """Parse an ISO timestamp and return a UTC-aware datetime. Naive input
        is assumed UTC (the destination stores schedules in UTC)."""
        parsed = parse(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _iso_utc(dt) -> str:
        return dt.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _schedule_timezone(timezone_name: str):
        """Resolve an IANA timezone using python-dateutil's bundled data."""
        schedule_timezone = gettz(timezone_name)
        if schedule_timezone is None:
            raise ValueError(f"Unknown schedule timezone: {timezone_name}")
        return schedule_timezone

    @classmethod
    def _parse_recurrence_start(cls, value: str, timezone_name: str) -> datetime:
        """Parse a recurring start in the schedule's local timezone."""
        schedule_timezone = cls._schedule_timezone(timezone_name)
        parsed = parse(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=schedule_timezone)
        return parsed.astimezone(schedule_timezone)

    @staticmethod
    def _iso_local(dt: datetime) -> str:
        """Recurring starts are local datetimes; the timezone is separate."""
        return dt.replace(tzinfo=None).isoformat()

    @staticmethod
    def _rrule_without_count(rrule: str, count_match) -> str:
        """Remove COUNT while preserving the remaining semicolon-delimited rule."""
        count_start, count_end = count_match.span()
        if count_start == 0 and count_end < len(rrule) and rrule[count_end] == ";":
            count_end += 1
        return rrule[:count_start] + rrule[count_end:]

    @staticmethod
    def _rrule_with_utc_until(rrule: str, start: datetime) -> str:
        """Convert UNTIL to UTC for dateutil without changing the API RRULE."""
        until_match = _RRULE_UNTIL_RE.search(rrule)
        if until_match is None:
            return rrule

        until = parse(until_match.group("until"))
        if until.tzinfo is None:
            until = until.replace(tzinfo=start.tzinfo)
        until_utc = until.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        until_start, until_end = until_match.span("until")
        return rrule[:until_start] + until_utc + rrule[until_end:]

    @classmethod
    def _next_valid_occurrence(
        cls,
        rrule: str,
        start: datetime,
        cutoff: datetime,
    ) -> Tuple[Optional[datetime], Optional[int]]:
        """Return the first real local occurrence after cutoff and remaining COUNT."""
        count_match = _RRULE_COUNT_RE.search(rrule)
        count = int(count_match.group("count")) if count_match else None
        if count is not None and count <= 0:
            return None, 0

        generation_rule = cls._rrule_without_count(rrule, count_match) if count_match else rrule
        generation_rule = cls._rrule_with_utc_until(generation_rule, start)
        recurrence_rule = rrulestr(generation_rule, dtstart=start)
        valid_occurrences = 0
        for generated_occurrences, occurrence in enumerate(recurrence_rule, start=1):
            if generated_occurrences > cls._MAX_RRULE_OCCURRENCES:
                raise _RecurrenceExpansionLimit(
                    f"Recurrence requires more than {cls._MAX_RRULE_OCCURRENCES} occurrences to normalize."
                )
            if not datetime_exists(occurrence):
                continue

            valid_occurrences += 1
            if count is not None and valid_occurrences > count:
                return None, 0
            if occurrence > cutoff:
                remaining = count - valid_occurrences + 1 if count is not None else None
                return occurrence, remaining
            if count is not None and valid_occurrences == count:
                return None, 0

        return None, 0 if count is not None else None

    @staticmethod
    def _replace_count(rrule: str, remaining: Optional[int]) -> str:
        if remaining is None:
            return rrule
        count_match = _RRULE_COUNT_RE.search(rrule)
        if count_match is None:
            return rrule
        count_start, count_end = count_match.span("count")
        return rrule[:count_start] + str(remaining) + rrule[count_end:]

    @classmethod
    def _normalized_recurrences(cls, schedule: Dict, cutoff: datetime) -> List[Dict]:
        """Return recurrence copies rebased to the first occurrence after cutoff."""
        recurrences = schedule.get("recurrences")
        if not recurrences:
            return []

        timezone_name = schedule.get("timezone") or "UTC"
        cutoff_local = cutoff.astimezone(cls._schedule_timezone(timezone_name))
        active_recurrences = []
        for recurrence in deepcopy(recurrences):
            start_raw = recurrence.get("start")
            rule_raw = recurrence.get("rrule")
            if not start_raw or not rule_raw:
                active_recurrences.append(recurrence)
                continue

            start = cls._parse_recurrence_start(start_raw, timezone_name)
            count_match = _RRULE_COUNT_RE.search(rule_raw)
            count = int(count_match.group("count")) if count_match else None
            if start > cutoff_local and datetime_exists(start) and (count is None or count > 0):
                active_recurrences.append(recurrence)
                continue

            next_start, remaining = cls._next_valid_occurrence(rule_raw, start, cutoff_local)
            if next_start is None:
                continue

            recurrence["start"] = cls._iso_local(next_start)
            recurrence["rrule"] = cls._replace_count(rule_raw, remaining)
            active_recurrences.append(recurrence)

        return active_recurrences

    @classmethod
    def _active_window(cls, schedule: Dict, now: datetime) -> Optional[_ActiveWindow]:
        """Return the API-derived current window when it is active now."""
        current = schedule.get("current_downtime")
        if not isinstance(current, dict) or not current.get("start"):
            return None

        start = cls._parse_utc(current["start"])
        end = cls._parse_utc(current["end"]) if current.get("end") else None
        if start <= now and (end is None or now < end):
            return _ActiveWindow(start=start, end=end)
        return None

    @classmethod
    def _analyze_recurrence_schedule(
        cls,
        schedule: Dict,
        now: datetime,
        cutoff: datetime,
    ) -> _RecurrencePlan:
        """Separate an active API window from normalized future cadence."""
        return _RecurrencePlan(
            timezone_name=schedule.get("timezone") or "UTC",
            active_window=cls._active_window(schedule, now),
            future_recurrences=tuple(cls._normalized_recurrences(schedule, cutoff)),
        )

    @staticmethod
    def _effective_active_window(plan: _RecurrencePlan, now: datetime) -> Optional[_ActiveWindow]:
        """Ignore a residual window too short for the API's one-minute unit."""
        window = plan.active_window
        if window is None or window.end is None:
            return window
        if (window.end - now).total_seconds() < 60:
            return None
        return window

    @classmethod
    def _bridge_recurrence(cls, _id: str, plan: _RecurrencePlan, now: datetime) -> Optional[Dict]:
        window = cls._effective_active_window(plan, now)
        if window is None:
            return None
        if window.end is None:
            raise SkipResource(
                str(_id),
                cls.resource_type,
                "Active recurrence has no finite end and cannot be bridged.",
            )

        remaining_minutes = int((window.end - now).total_seconds() // 60)
        if remaining_minutes < 1:
            return None
        start = now.astimezone(cls._schedule_timezone(plan.timezone_name))
        return {
            "start": cls._iso_local(start),
            "duration": f"{remaining_minutes}m",
            "rrule": cls._BRIDGE_RRULE,
        }

    @classmethod
    def _materialize_recurrence_plan(
        cls,
        _id: str,
        plan: _RecurrencePlan,
        now: datetime,
        include_bridge: bool,
    ) -> List[Dict]:
        recurrences = [deepcopy(recurrence) for recurrence in plan.future_recurrences]
        bridge = cls._bridge_recurrence(_id, plan, now) if include_bridge else None
        if bridge is not None:
            if len(recurrences) >= cls._MAX_API_RECURRENCES:
                raise SkipResource(
                    str(_id),
                    cls.resource_type,
                    f"Active bridge would exceed the API maximum of {cls._MAX_API_RECURRENCES} recurrences.",
                )
            recurrences.insert(0, bridge)
        return recurrences

    @classmethod
    def _active_windows_equivalent(
        cls,
        source_plan: _RecurrencePlan,
        destination_plan: _RecurrencePlan,
        now: datetime,
    ) -> bool:
        source = source_plan.active_window
        destination = destination_plan.active_window
        if source is not None and destination is not None:
            if source.end is None or destination.end is None:
                return source.end is destination.end

            # A bridge starts during sync, not when the source occurrence
            # began. Its duration is rounded down to whole minutes, so an end
            # up to one minute early is representation-only, including the
            # bridge's final minute.
            end_delta = (source.end - destination.end).total_seconds()
            return -1 <= end_delta < 60

        # The API cannot represent a bridge shorter than one minute. Once the
        # destination bridge has ended, treat only that sub-minute source tail
        # as converged rather than repeatedly trying to PATCH an active child.
        if source is not None and cls._effective_active_window(source_plan, now) is None:
            return destination is None
        return source is destination

    @classmethod
    def _recurrence_plans_equivalent(
        cls,
        source_plan: _RecurrencePlan,
        destination_plan: _RecurrencePlan,
        now: datetime,
    ) -> bool:
        futures_differ = DeepDiff(
            list(source_plan.future_recurrences),
            list(destination_plan.future_recurrences),
            ignore_order=True,
        )
        return not futures_differ and cls._active_windows_equivalent(source_plan, destination_plan, now)

    def _prepare_update_recurrences(
        self,
        _id: str,
        source_schedule: Dict,
        destination_schedule: Dict,
        now: datetime,
        cutoff: datetime,
    ) -> None:
        """Prepare a PATCH-safe schedule and remember apply-time behavior."""
        source_plan = self._analyze_recurrence_schedule(source_schedule, now, cutoff)
        destination_plan = self._analyze_recurrence_schedule(destination_schedule, now, cutoff)
        create_schedule = deepcopy(source_schedule)
        action = _RecurrenceUpdateAction.PATCH

        if self._recurrence_plans_equivalent(source_plan, destination_plan, now):
            source_schedule["recurrences"] = deepcopy(destination_schedule["recurrences"])
            if source_plan.active_window is not None or destination_plan.active_window is not None:
                action = _RecurrenceUpdateAction.OMIT_SCHEDULE
            elif source_plan.future_recurrences:
                action = _RecurrenceUpdateAction.NORMALIZE_PATCH
            else:
                action = _RecurrenceUpdateAction.OMIT_SCHEDULE
        else:
            source_active = self._effective_active_window(source_plan, now)
            destination_active = destination_plan.active_window
            if source_plan.active_window is not None and source_active is None and destination_active is not None:
                if self._active_windows_equivalent(source_plan, destination_plan, now):
                    # The source has less than one representable minute left.
                    # Preserve an equivalent active destination and defer any
                    # cadence change until both windows have ended.
                    source_schedule["recurrences"] = deepcopy(destination_schedule["recurrences"])
                    action = _RecurrenceUpdateAction.OMIT_SCHEDULE
                elif source_plan.future_recurrences:
                    source_schedule["recurrences"] = self._materialize_recurrence_plan(
                        _id,
                        source_plan,
                        now,
                        include_bridge=True,
                    )
                    action = _RecurrenceUpdateAction.RECREATE
                else:
                    source_schedule["recurrences"] = []
                    action = _RecurrenceUpdateAction.CANCEL
            elif source_active is not None:
                if destination_active is None:
                    source_schedule["recurrences"] = self._materialize_recurrence_plan(
                        _id,
                        source_plan,
                        now,
                        include_bridge=True,
                    )
                elif abs((source_active.start - destination_active.start).total_seconds()) > 60:
                    if self._active_windows_equivalent(source_plan, destination_plan, now):
                        # The API rejects changing an active child's start. A
                        # bridge created during an earlier sync naturally has a
                        # different start from the source occurrence, so defer
                        # recurrence-only changes until that equivalent active
                        # window ends. Unrelated fields may still be patched.
                        source_schedule["recurrences"] = deepcopy(destination_schedule["recurrences"])
                        action = _RecurrenceUpdateAction.OMIT_SCHEDULE
                        log.info(
                            f"[downtime_schedules - {_id}] deferred recurrence change "
                            "until active destination window ends"
                        )
                    else:
                        # The destination is actively muting the wrong window
                        # and the API will not PATCH its start. Replace it with a
                        # bridge plus the source's future cadence. _delete_resource
                        # clears the stale mapping before POST so a failed create
                        # converges through the normal create path on the next run.
                        source_schedule["recurrences"] = self._materialize_recurrence_plan(
                            _id,
                            source_plan,
                            now,
                            include_bridge=True,
                        )
                        action = _RecurrenceUpdateAction.RECREATE
                # When active starts align, retain the source's original
                # recurrence anchor. The API derives the existing active child
                # from it and accepts future-cadence changes without shifting
                # the active start.
            elif source_plan.future_recurrences:
                source_schedule["recurrences"] = [deepcopy(recurrence) for recurrence in source_plan.future_recurrences]
            elif destination_active is not None or destination_plan.future_recurrences:
                # An empty recurrence list is intentionally only an internal
                # diff marker. update_resource consumes CANCEL before writing.
                source_schedule["recurrences"] = []
                action = _RecurrenceUpdateAction.CANCEL
            else:
                source_schedule["recurrences"] = deepcopy(destination_schedule["recurrences"])
                action = _RecurrenceUpdateAction.OMIT_SCHEDULE

        self._prepared_recurrence_updates[str(_id)] = _PreparedRecurrenceUpdate(
            action=action,
            create_schedule=create_schedule,
        )

    def _normalize_recurrence_schedule(
        self,
        _id: str,
        schedule: Dict,
        cutoff: datetime,
        skip_if_empty: bool = True,
    ) -> bool:
        """Rebase recurrences for a fallback API write."""
        if not schedule.get("recurrences"):
            return True

        try:
            active_recurrences = self._normalized_recurrences(schedule, cutoff)
        except _RecurrenceExpansionLimit as error:
            raise SkipResource(str(_id), self.resource_type, str(error)) from error
        schedule["recurrences"] = active_recurrences
        if not active_recurrences:
            if skip_if_empty:
                raise SkipResource(
                    str(_id),
                    self.resource_type,
                    "Downtime recurrence has no future occurrences.",
                )
            return False
        return True

    @staticmethod
    def _http_error_messages(error: CustomClientHTTPError) -> List[str]:
        """Return string messages from a JSON API error response."""
        body = error.response_body
        if not isinstance(body, str):
            return []
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return []
        errors = parsed.get("errors", []) if isinstance(parsed, dict) else []
        return [message for message in errors if isinstance(message, str)]

    @classmethod
    def _single_duplicate_id(cls, error: CustomClientHTTPError) -> Optional[str]:
        for message in cls._http_error_messages(error):
            if _DUPLICATE_DOWNTIME_MARKER not in message:
                continue
            match = _SINGLE_DUPLICATE_ID_RE.search(message)
            if match:
                return match.group("id")
        return None

    @classmethod
    def _is_downtime_not_found(cls, error: CustomClientHTTPError) -> bool:
        return any(_DOWNTIME_NOT_FOUND_MARKER in message for message in cls._http_error_messages(error))

    def _normalize_create_schedule(self, _id: str, resource: Dict) -> None:
        schedule = resource["attributes"].get("schedule")
        if not schedule:
            return
        now = datetime.now(timezone.utc)

        # Past `end` means the maintenance window has already closed on the
        # source. Replicating it to the destination would either invent a
        # new customer-visible maintenance (if we shifted `end` forward) or
        # 400 with "Downtime cannot be scheduled in the past". Skip: an
        # ended downtime has nothing left to silence.
        end_raw = schedule.get("end")
        if end_raw:
            end_dt = self._parse_utc(end_raw)
            if end_dt <= now:
                raise SkipResource(
                    str(_id),
                    self.resource_type,
                    "Downtime end is in the past.",
                )

        # Rewrite past `start` forward to now+60s. `end` (if present) is
        # left as-is per customer intent — the window may shrink but its
        # original end time is preserved.
        start_raw = schedule.get("start")
        if start_raw:
            start_dt = self._parse_utc(start_raw)
            if start_dt <= now:
                schedule["start"] = self._iso_utc(now + timedelta(seconds=60))

        # Recurring schedules need two independent representations: a
        # one-shot bridge for any window active now, and RRULEs rebased to
        # future cadence. Pin the bridge start to the current local time so
        # request latency cannot shift its fixed duration beyond the source end.
        if schedule.get("recurrences"):
            cutoff = now + timedelta(seconds=60)
            try:
                plan = self._analyze_recurrence_schedule(schedule, now, cutoff)
                recurrences = self._materialize_recurrence_plan(
                    _id,
                    plan,
                    now,
                    include_bridge=True,
                )
            except _RecurrenceExpansionLimit as error:
                raise SkipResource(str(_id), self.resource_type, str(error)) from error
            if not recurrences:
                raise SkipResource(
                    str(_id),
                    self.resource_type,
                    "Downtime recurrence has no future occurrences or active window.",
                )
            schedule["recurrences"] = recurrences

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        self._prepared_recurrence_updates.pop(str(_id), None)
        if _id not in self.config.state.destination[self.resource_type]:
            self._normalize_create_schedule(_id, resource)
        else:
            # If start or end times of the resource are in the past, we set to the current destination `start` and `end`
            # this is to avoid unnecessary diff outputs
            if resource["attributes"].get("schedule"):
                source_schedule = resource["attributes"]["schedule"]
                destination_schedule = self.config.state.destination[self.resource_type][_id]["attributes"].get(
                    "schedule", {}
                )
                if destination_schedule.get("start") and source_schedule.get("start"):
                    start_source = parse(source_schedule["start"])
                    start_created = parse(destination_schedule["start"])
                    if start_source.timestamp() < start_created.timestamp():
                        source_schedule["start"] = destination_schedule["start"]
                if destination_schedule.get("end") and source_schedule.get("end"):
                    end_source = parse(source_schedule["end"])
                    end_created = parse(destination_schedule["end"])
                    if end_source.timestamp() < end_created.timestamp():
                        source_schedule["end"] = destination_schedule["end"]
                if "recurrences" in source_schedule and "recurrences" in destination_schedule:
                    now = datetime.now(timezone.utc)
                    try:
                        self._prepare_update_recurrences(
                            _id,
                            source_schedule,
                            destination_schedule,
                            now,
                            now + timedelta(seconds=60),
                        )
                    except _RecurrenceExpansionLimit as error:
                        raise SkipResource(str(_id), self.resource_type, str(error)) from error

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        schedule = resource.get("attributes", {}).get("schedule")
        if schedule:
            schedule.pop("current_downtime", None)
        payload = {"data": resource}
        try:
            resp = await destination_client.post(self.resource_config.base_path, payload)
        except CustomClientHTTPError as e:
            duplicate_id = self._single_duplicate_id(e) if e.status_code == 400 else None
            if duplicate_id is not None:
                # The API identified exactly one equivalent destination
                # downtime. Fetch and return it so BaseResource persists the
                # recovered source-to-destination mapping. Ambiguous or
                # malformed duplicate responses still propagate as failures.
                existing = await destination_client.get(self.resource_config.base_path + f"/{duplicate_id}")
                log.info(f"[downtime_schedules - {_id}] reconciled duplicate with existing destination downtime")
                return _id, existing["data"]
            raise

        return _id, resp["data"]

    async def _update_resource(self, _id: str, resource: Dict) -> None:
        try:
            await super()._update_resource(_id, resource)
        except _CancelDestinationDowntime:
            await self._delete_resource(_id)
            log.info(f"[downtime_schedules - {_id}] canceled active destination after source recurrence expired")

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        prepared = self._prepared_recurrence_updates.pop(str(_id), None)
        resource["id"] = self.config.state.destination[self.resource_type][_id]["id"]
        schedule = resource["attributes"].get("schedule")
        recreate_schedule = deepcopy(prepared.create_schedule) if prepared is not None else None

        if prepared is not None and prepared.action == _RecurrenceUpdateAction.CANCEL:
            raise _CancelDestinationDowntime

        if prepared is not None and prepared.action == _RecurrenceUpdateAction.RECREATE:
            await self._delete_resource(_id)
            resource.pop("id", None)
            resource["attributes"]["schedule"] = deepcopy(prepared.create_schedule)
            self._normalize_create_schedule(_id, resource)
            log.info(f"[downtime_schedules - {_id}] replacing mismatched active destination downtime")
            return await self.create_resource(_id, resource)

        if prepared is not None and prepared.action == _RecurrenceUpdateAction.OMIT_SCHEDULE:
            resource["attributes"].pop("schedule", None)
        elif prepared is not None and prepared.action == _RecurrenceUpdateAction.NORMALIZE_PATCH:
            self._normalize_recurrence_schedule(
                _id,
                schedule,
                datetime.now(timezone.utc) + timedelta(seconds=60),
            )
            schedule.pop("current_downtime", None)
        elif prepared is not None:
            schedule.pop("current_downtime", None)
        elif schedule:
            # Compatibility for direct callers that do not run the pre-action
            # hook (including the 404 recreate fallback tests).
            recreate_schedule = deepcopy(schedule)
            cutoff = datetime.now(timezone.utc) + timedelta(seconds=60)
            has_future_recurrence = self._normalize_recurrence_schedule(
                _id,
                schedule,
                cutoff,
                skip_if_empty=False,
            )
            if not has_future_recurrence:
                destination_schedule = (
                    self.config.state.destination[self.resource_type][_id].get("attributes", {}).get("schedule", {})
                )
                try:
                    destination_recurrences = self._normalized_recurrences(destination_schedule, cutoff)
                except _RecurrenceExpansionLimit as error:
                    raise SkipResource(str(_id), self.resource_type, str(error)) from error
                if destination_recurrences:
                    raise _CancelDestinationDowntime
                resource["attributes"].pop("schedule")
            else:
                schedule.pop("current_downtime", None)

        payload = {"data": resource}
        try:
            resp = await destination_client.patch(
                self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}",
                payload,
            )
        except CustomClientHTTPError as e:
            if e.status_code == 404 and self._is_downtime_not_found(e):
                # The mapped destination downtime was removed out-of-band, so
                # the PATCH target no longer exists ("Downtime not found").
                # Recreate it now and return the new destination object so the
                # BaseResource wrapper replaces the stale persisted mapping.
                # Re-run create-only schedule normalization because the first
                # pre-action hook took the update branch.
                resource.pop("id", None)
                if recreate_schedule is not None:
                    resource["attributes"]["schedule"] = recreate_schedule
                self._normalize_create_schedule(_id, resource)
                log.info(f"[downtime_schedules - {_id}] recreating missing mapped downtime on destination")
                return await self.create_resource(_id, resource)
            raise

        return _id, resp["data"]

    async def delete_resource(self, _id: str) -> None:
        destination_client = self.config.destination_client
        try:
            await destination_client.delete(
                self.resource_config.base_path + f"/{self.config.state.destination[self.resource_type][_id]['id']}"
            )
        except CustomClientHTTPError as e:
            if e.status_code == 404:
                # Already gone on the destination: deleting a non-existent
                # downtime is a successful no-op, not a failure.
                log.info(f"[downtime_schedules - {_id}] already deleted on destination")
                return
            raise

    def connect_id(self, key: str, r_obj: Dict, resource_to_connect: str) -> Optional[List[str]]:
        return super(DowntimeSchedules, self).connect_id(key, r_obj, resource_to_connect)
