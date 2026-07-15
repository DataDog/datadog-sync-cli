# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from asyncio import AbstractEventLoop, Future, Queue, QueueEmpty, Task, gather, get_event_loop, sleep

from collections import defaultdict
from dataclasses import dataclass, field
from traceback import format_exc
from typing import Awaitable, Callable, Dict, List, Optional

from tqdm.asyncio import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

from datadog_sync.utils.configuration import Configuration


class Workers:
    def __init__(self, config: Configuration) -> None:
        self.config: Configuration = config
        self.workers: List[Task] = []
        self.work_queue: Queue = Queue()
        self.counter: Counter = Counter()
        # Back-reference so resource models (which only hold self.config) can record
        # dropped stale principals into the counter from inside connect_id/connect_resources.
        # Last-writer-wins is fine: only one Workers drives a given command's apply pass.
        config.counter = self.counter
        self.pbar: Optional[tqdm] = None
        self._running_workers_count: int = 0
        self._loop: AbstractEventLoop = get_event_loop()
        self._shutdown_workers: bool = False
        self._cb: Optional[Awaitable] = None
        self._cancel_cb: Callable = self.work_queue.empty

    async def init_workers(
        self, cb: Awaitable, cancel_cb: Optional[Callable], worker_count: Optional[int], *args, **kwargs
    ) -> Awaitable[None]:
        await self._reset()

        max_workers = self.config.max_workers
        if worker_count:
            max_workers = min(worker_count, max_workers)

        self._cb = cb
        if cancel_cb:
            self._cancel_cb = cancel_cb
        await self._create_workers(max_workers, *args, **kwargs)

    async def _create_workers(self, max_workers: int, *args, **kwargs) -> Awaitable[None]:
        for _ in range(max_workers):
            self.workers.append(self._worker(*args, **kwargs))
        self._running_workers_count = max_workers
        self.workers.append(self._cancel_worker())

    async def _worker(self, *args, **kwargs) -> Awaitable[None]:
        while not self._shutdown_workers or (self._shutdown_workers and not self.work_queue.empty()):
            try:
                t = self.work_queue.get_nowait()
                try:
                    await self._cb(t, *args, **kwargs)
                except Exception as e:
                    self.config.logger.debug(format_exc())
                    self.config.logger.error(f"Error processing task: {e}")
                finally:
                    self.work_queue.task_done()
                    if self.pbar:
                        await self._loop.run_in_executor(None, self.pbar.update)
            except QueueEmpty:
                pass
            except Exception as e:
                self.config.logger.debug(format_exc())
                self.config.logger.error(f"Error processing task: {e}")
            await sleep(0)
        self._running_workers_count -= 1

    async def _cancel_worker(self) -> None:
        while True:
            if await self._loop.run_in_executor(None, self._cancel_cb):
                self._shutdown_workers = True
                break

    async def _reset(self) -> Awaitable[None]:
        self.workers.clear()
        self.work_queue = Queue()
        self.counter.reset_counter()
        self._shutdown_workers = False
        self.pbar = None
        self._running_workers_count = 0

    async def _refresh_pbar(self) -> Awaitable[None]:
        while self._running_workers_count > 0 and self.pbar:
            await self._loop.run_in_executor(None, self.pbar.display)

    async def schedule_workers(self, additional_coros: List = []) -> Future:
        self._shutdown_workers = False
        return await gather(*self.workers, *additional_coros, return_exceptions=True)

    async def schedule_workers_with_pbar(self, total, additional_coros: List = []) -> Future:
        self.pbar = tqdm(total=total)

        self._shutdown_workers = False
        with logging_redirect_tqdm():
            additional_coros.append(self._refresh_pbar())
            await self.schedule_workers(additional_coros)

        self.pbar.close()
        self.pbar = None


@dataclass
class Counter:
    successes: int = 0
    failure: int = 0
    skipped: int = 0
    filtered: int = 0
    # Per-resource-type source IDs that failed or were skipped. Populated by
    # _apply_resource_cb. Used to emit a targeted end-of-apply summary so
    # subsequent sync-cli invocations (a separate process for a different
    # resource type) can correlate cascade failures back to specific source
    # IDs — e.g. "monitors sync couldn't remap roles X, Y, Z" is only
    # actionable if the roles-sync run logged that X, Y, Z failed.
    failed_ids_by_type: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))
    skipped_missing_deps_by_type: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))
    # Per-resource-type source IDs whose stale (source-absent) principal/role
    # references were dropped from an access-control list, and resource IDs skipped
    # because dropping left an empty binding/list (access-elevation risk). Populated
    # only when --drop-unresolvable-principals is set. Surfaced by _emit_apply_summary.
    stale_principals_dropped_by_type: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))
    empty_binding_risk_by_type: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))
    empty_binding_escalation_by_type: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))

    def __str__(self):
        return (
            f"Successes: {self.successes}, Failures: {self.failure}, Skipped: {self.skipped}, Filtered: {self.filtered}"
        )

    def reset_counter(self) -> None:
        self.successes = self.failure = self.skipped = self.filtered = 0
        self.failed_ids_by_type = defaultdict(list)
        self.skipped_missing_deps_by_type = defaultdict(list)
        self.stale_principals_dropped_by_type = defaultdict(list)
        self.empty_binding_risk_by_type = defaultdict(list)
        self.empty_binding_escalation_by_type = defaultdict(list)

    def increment_success(self) -> None:
        self.successes += 1

    def increment_failure(self, resource_type: Optional[str] = None, _id: Optional[str] = None) -> None:
        self.failure += 1
        # Explicit `is not None` (not truthy) so numeric-0 ids and empty-string
        # ids still get tracked — future resource types may use them, and
        # dropping them silently would under-report the summary.
        if resource_type is not None and _id is not None:
            self.failed_ids_by_type[resource_type].append(str(_id))

    def increment_skipped(
        self, resource_type: Optional[str] = None, _id: Optional[str] = None, missing_deps: bool = False
    ) -> None:
        self.skipped += 1
        if missing_deps and resource_type is not None and _id is not None:
            self.skipped_missing_deps_by_type[resource_type].append(str(_id))

    def increment_filtered(self) -> None:
        self.filtered += 1

    # Two dedicated methods rather than the single-multi-purpose-method convention used by
    # increment_skipped/increment_failure: the "dropped a stale principal but kept syncing"
    # and "skipped for empty-binding access-elevation risk" cases are semantically distinct
    # enough (one is a non-fatal drop, the other a hard-fail security signal) to warrant
    # separate names. This deliberate deviation is called out here so it isn't flagged as
    # an inconsistency.
    def record_stale_principal_dropped(self, resource_type: Optional[str] = None, _id: Optional[str] = None) -> None:
        if resource_type is not None and _id is not None:
            self.stale_principals_dropped_by_type[resource_type].append(str(_id))

    def record_empty_binding_risk(self, resource_type: Optional[str] = None, _id: Optional[str] = None) -> None:
        if resource_type is not None and _id is not None:
            self.empty_binding_risk_by_type[resource_type].append(str(_id))

    def record_empty_binding_escalation(
        self, resource_type: Optional[str] = None, _id: Optional[str] = None
    ) -> None:
        if resource_type is not None and _id is not None:
            self.empty_binding_escalation_by_type[resource_type].append(str(_id))
