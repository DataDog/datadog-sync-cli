# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from asyncio import Future, Queue, QueueEmpty, Task, gather, get_event_loop, sleep

from typing import Awaitable, Callable, List, Optional

from datadog_sync.utils.configuration import Configuration


class Workers:
    def __init__(self, config: Configuration) -> None:
        self.config: Configuration = config
        self.workers: List[Task] = []
        self.work_queue: Queue = Queue()
        self._shutdown: bool = False
        self._cb: Optional[Awaitable] = None
        self._cancel_cb: Callable = self.work_queue.empty

    async def init_workers(
        self, cb: Awaitable, cancel_cb: Optional[Callable], worker_count: Optional[int], *args, **kwargs
    ) -> None:
        # reset the worker
        self.workers = []
        self.work_queue = Queue()
        self._shutdown = False

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
        await sleep(0)

    async def _worker(self, *args, **kwargs) -> None:
        while not self._shutdown or (self._shutdown and not self.work_queue.empty()):
            try:
                t = self.work_queue.get_nowait()
                try:
                    await self._cb(t, *args, **kwargs)
                except Exception as e:
                    self.config.logger.error(f"Error processing task: {e}")
                finally:
                    self.work_queue.task_done()
            except QueueEmpty:
                pass
            except Exception as e:
                self.config.logger.error(f"Error processing task: {e}")
            await sleep(0)

    async def _cancel_worker(self) -> None:
        loop = get_event_loop()
        while True:
            if await loop.run_in_executor(None, self._cancel_cb):
                self._shutdown = True
                break
            await sleep(0)

    async def schedule_workers(self, additional_coros: List = []) -> Future:
        self._shutdown = False
        return await gather(*self.workers, *additional_coros, return_exceptions=True)
