# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from __future__ import annotations
import json
import logging
import re
from copy import deepcopy
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple
from datetime import datetime, timedelta, timezone

from deepdiff import DeepDiff
from dateutil.parser import parse
from dateutil.rrule import rrulestr
from dateutil.tz import gettz

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


class DowntimeSchedules(BaseResource):
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
    def _rebase_count(rrule: str, recurrence_rule, next_start: datetime) -> str:
        """Reduce COUNT by occurrences consumed before the rebased start."""
        count_match = _RRULE_COUNT_RE.search(rrule)
        if count_match is None:
            return rrule

        remaining = int(count_match.group("count"))
        for occurrence in recurrence_rule:
            if occurrence >= next_start:
                break
            remaining -= 1

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
            if start > cutoff_local:
                active_recurrences.append(recurrence)
                continue

            recurrence_rule = rrulestr(rule_raw, dtstart=start)
            next_start = recurrence_rule.after(cutoff_local, inc=False)
            if next_start is None:
                continue

            recurrence["start"] = cls._iso_local(next_start)
            recurrence["rrule"] = cls._rebase_count(rule_raw, recurrence_rule, next_start)
            active_recurrences.append(recurrence)

        return active_recurrences

    def _normalize_recurrence_schedule(self, _id: str, schedule: Dict, cutoff: datetime) -> None:
        """Rebase recurrences for an API write and skip schedules with none left."""
        if not schedule.get("recurrences"):
            return

        active_recurrences = self._normalized_recurrences(schedule, cutoff)
        schedule["recurrences"] = active_recurrences
        if not active_recurrences:
            raise SkipResource(
                str(_id),
                self.resource_type,
                "Downtime recurrence has no future occurrences.",
            )

    @classmethod
    def _reconcile_update_recurrences(
        cls,
        source_schedule: Dict,
        destination_schedule: Dict,
        cutoff: datetime,
    ) -> None:
        """Hide representation-only rebasing while preserving semantic changes."""
        if "recurrences" not in source_schedule or "recurrences" not in destination_schedule:
            return

        source_recurrences = cls._normalized_recurrences(source_schedule, cutoff)
        destination_recurrences = cls._normalized_recurrences(destination_schedule, cutoff)
        if not DeepDiff(source_recurrences, destination_recurrences, ignore_order=True):
            source_schedule["recurrences"] = deepcopy(destination_schedule["recurrences"])

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

        # Recurring schedules store offset-free starts interpreted in the
        # schedule's timezone. Advance each start that is past or too close
        # for a create request to the next RRULE occurrence so its weekday
        # and wall-clock cadence are preserved.
        self._normalize_recurrence_schedule(_id, schedule, now + timedelta(seconds=60))

    async def pre_resource_action_hook(self, _id, resource: Dict) -> None:
        if _id not in self.config.state.destination[self.resource_type]:
            self._normalize_create_schedule(_id, resource)
        else:
            # If start or end times of the resource are in the past, we set to the current destination `start` and `end`
            # this is to avoid unnecessary diff outputs
            if resource["attributes"].get("schedule"):
                one_time_source = resource["attributes"].get("schedule")
                one_time_created = self.config.state.destination[self.resource_type][_id]["attributes"].get("schedule")
                if one_time_created.get("start") and one_time_source.get("start"):
                    start_source = parse(one_time_source["start"])
                    start_created = parse(one_time_created["start"])
                    if start_source.timestamp() < start_created.timestamp():
                        one_time_source["start"] = one_time_created["start"]
                if one_time_created.get("end") and one_time_source.get("end"):
                    start_source = parse(one_time_source["end"])
                    start_created = parse(one_time_created["end"])
                    if start_source.timestamp() < start_created.timestamp():
                        one_time_source["end"] = one_time_created["end"]
                self._reconcile_update_recurrences(
                    one_time_source,
                    one_time_created,
                    datetime.now(timezone.utc) + timedelta(seconds=60),
                )

    async def pre_apply_hook(self) -> None:
        pass

    async def create_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
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

    async def update_resource(self, _id: str, resource: Dict) -> Tuple[str, Dict]:
        destination_client = self.config.destination_client
        resource["id"] = self.config.state.destination[self.resource_type][_id]["id"]
        schedule = resource["attributes"].get("schedule")
        if schedule:
            self._normalize_recurrence_schedule(
                _id,
                schedule,
                datetime.now(timezone.utc) + timedelta(seconds=60),
            )
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
