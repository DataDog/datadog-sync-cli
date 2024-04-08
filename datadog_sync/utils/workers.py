# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from asyncio import Future, Queue, QueueEmpty, Task, gather, get_event_loop, sleep

from dataclasses import dataclass
from traceback import format_exc
from typing import Awaitable, Callable, List, Optional

from tqdm.asyncio import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

from datadog_sync.utils.configuration import Configuration


class Workers:
    def __init__(self, config: Configuration) -> None:
        self.config: Configuration = config
        self.workers: List[Task] = []
        self.work_queue: Queue = Queue()
        self.counter: Counter = Counter()
        self._shutdown: bool = False
        self._cb: Optional[Awaitable] = None
        self._cancel_cb: Callable = self.work_queue.empty
        self.pbar: Optional[tqdm] = None

    async def init_workers(
        self, cb: Awaitable, cancel_cb: Optional[Callable], worker_count: Optional[int], *args, **kwargs
    ) -> None:
        await self._reset()

        max_workers = self.config.max_workers
        if worker_count:
            max_workers = min(worker_count, max_workers)

        self._cb = cb
        if cancel_cb:
            self._cancel_cb = cancel_cb
        await self._create_workers(max_workers, *args, **kwargs)

    async def _create_workers(self, max_workers: Optional[int], *args, **kwargs):
        for _ in range(max_workers):
            self.workers.append(self._worker(*args, **kwargs))
        self.workers.append(self._cancel_worker())

    async def _worker(self, *args, **kwargs) -> None:
        while not self._shutdown or (self._shutdown and not self.work_queue.empty()):
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
                        self.pbar.update()
            except QueueEmpty:
                pass
            except Exception as e:
                self.config.logger.debug(format_exc())
                self.config.logger.error(f"Error processing task: {e}")
            await sleep(0)

    async def _cancel_worker(self) -> None:
        loop = get_event_loop()
        while True:
            if await loop.run_in_executor(None, self._cancel_cb):
                self._shutdown = True
                break
            if self.pbar:
                await loop.run_in_executor(None, self.pbar.refresh)
            await sleep(0)

    async def _reset(self):
        self.workers.clear()
        self.work_queue = Queue()
        self.counter.reset_counter()
        self._shutdown = False
        self.pbar = None

    async def schedule_workers(self, additional_coros: List = []) -> Future:
        self._shutdown = False
        return await gather(*self.workers, *additional_coros, return_exceptions=True)

    async def schedule_workers_with_pbar(self, total, additional_coros: List = []) -> Future:
        self.pbar = tqdm(total=total)

        self._shutdown = False
        with logging_redirect_tqdm():
            await self.schedule_workers(additional_coros)

        self.pbar.close()
        self.pbar = None


@dataclass
class Counter:
    successes: int = 0
    failure: int = 0
    skipped: int = 0
    filtered: int = 0

    def __str__(self):
        return (
            f"Successes: {self.successes}, Failures: {self.failure}, Skipped: {self.skipped}, Filtered: {self.filtered}"
        )

    def reset_counter(self) -> None:
        self.successes = self.failure = self.skipped = 0

    def increment_success(self) -> None:
        self.successes += 1

    def increment_failure(self) -> None:
        self.failure += 1

    def increment_skipped(self) -> None:
        self.skipped += 1

    def increment_filtered(self) -> None:
        self.filtered += 1
