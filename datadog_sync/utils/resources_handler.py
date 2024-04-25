# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import asyncio
from collections import defaultdict

from click import confirm
from pprint import pformat

from datadog_sync.constants import (
    DESTINATION_ORIGIN,
    SOURCE_ORIGIN,
)
from datadog_sync.utils.resources_manager import ResourcesManager
from datadog_sync.constants import TRUE, FALSE, FORCE
from datadog_sync.utils.resource_utils import (
    CustomClientHTTPError,
    ResourceConnectionError,
    SkipResource,
    check_diff,
    create_global_downtime,
    dump_resources,
    prep_resource,
    init_topological_sorter,
)
from typing import Dict, TYPE_CHECKING, List, Optional, Tuple

from datadog_sync.utils.workers import Workers

if TYPE_CHECKING:
    from datadog_sync.utils.configuration import Configuration
    from graphlib import TopologicalSorter


class ResourcesHandler:
    def __init__(self, config: Configuration) -> None:
        self.config = config
        self.resources_manager: ResourcesManager = ResourcesManager(config)
        self.sorter: Optional[TopologicalSorter] = None
        self.worker: Optional[Workers] = None

    async def init_async(self) -> None:
        self.worker: Workers = Workers(self.config)

    async def apply_resources(self) -> Tuple[int, int]:
        # Import resources that are missing but needed for resource connections
        if self.config.force_missing_dependencies and self.resources_manager.all_missing_resources:
            self.config.logger.info("Importing missing dependencies...")
            await self.worker.init_workers(self._force_missing_dep_import_cb, None, None)
            for _id, resource_type in self.resources_manager.all_missing_resources.items():
                self.worker.work_queue.put_nowait((resource_type, _id))
            await self.worker.schedule_workers()

            dump_resources(self.config, self.resources_manager.all_missing_resources.values(), SOURCE_ORIGIN)
            self.config.logger.info("finished importing missing dependencies")

        # handle resource cleanups
        if self.config.cleanup != FALSE and self.resources_manager.all_cleanup_resources:
            cleanup = _cleanup_prompt(self.config, self.resources_manager.all_cleanup_resources)
            if cleanup:
                self.config.logger.info("Cleaning up resources...")
                await self.worker.init_workers(self._cleanup_worker, None, None)
                for _id, resource_type in self.resources_manager.all_cleanup_resources.items():
                    self.worker.work_queue.put_nowait((resource_type, _id))
                await self.worker.schedule_workers()
                self.config.logger.info("finished cleaning up resources")
                dump_resources(self.config, self.resources_manager.all_cleanup_resources.values(), DESTINATION_ORIGIN)

        # Run pre-apply hooks
        resources = set(self.resources_manager.all_resources_to_type.values())
        await self.worker.init_workers(self._pre_apply_hook_cb, None, len(resources))
        for resource_type in set(self.resources_manager.all_resources_to_type.values()):
            self.worker.work_queue.put_nowait(resource_type)
        await self.worker.schedule_workers()

        # Additional pre-apply actions
        if self.config.create_global_downtime:
            await create_global_downtime(self.config)

        total = len(self.resources_manager.all_resources_to_type.keys())
        # initalize topological sorters
        self.sorter = init_topological_sorter(self.resources_manager.dependencies_graph)
        await self.worker.init_workers(self._apply_resource_cb, lambda: not self.sorter.is_active(), None)
        await self.worker.schedule_workers_with_pbar(total=total, additional_coros=[self.run_sorter()])
        self.config.logger.info(f"Finished syncing resource items. {self.worker.counter}.")

        # dump synced resources
        synced_resource_types = set(self.resources_manager.all_resources_to_type.values())
        dump_resources(self.config, synced_resource_types, DESTINATION_ORIGIN)

    async def _apply_resource_cb(self, q_item: List) -> None:
        resource_type, _id = q_item

        try:
            r_class = self.config.resources[resource_type]
            resource = r_class.resource_config.source_resources[_id]

            if not r_class.resource_config.concurrent:
                await r_class.resource_config.async_lock.acquire()

            if _id not in self.resources_manager.all_missing_resources:
                if not r_class.filter(resource):
                    self.worker.counter.increment_filtered()
                    return

            # Run hooks
            await r_class._pre_resource_action_hook(_id, resource)
            r_class.connect_resources(_id, resource)

            if _id in r_class.resource_config.destination_resources:
                diff = check_diff(r_class.resource_config, resource, r_class.resource_config.destination_resources[_id])
                if diff:
                    self.config.logger.debug(f"Running update for {resource_type} with {_id}")

                    prep_resource(r_class.resource_config, resource)
                    await r_class._update_resource(_id, resource)

                    self.config.logger.debug(f"Finished update for {resource_type} with {_id}")
            else:
                self.config.logger.debug(f"Running create for {resource_type} with id: {_id}")

                prep_resource(r_class.resource_config, resource)
                await r_class._create_resource(_id, resource)

                self.config.logger.debug(f"finished create for {resource_type} with id: {_id}")
            self.worker.counter.increment_success()
        except SkipResource as e:
            self.config.logger.info(str(e))
            self.worker.counter.increment_skipped()
        except ResourceConnectionError:
            self.worker.counter.increment_skipped()
        except Exception as e:
            self.worker.counter.increment_failure()
            self.config.logger.error(str(e))
        finally:
            # always place in done queue regardless of exception thrown
            self.sorter.done(_id)
            if not r_class.resource_config.concurrent:
                r_class.resource_config.async_lock.release()

    async def diffs(self) -> None:
        # Run pre-apply hooks
        resources = set(self.resources_manager.all_resources_to_type.values())
        await self.worker.init_workers(self._pre_apply_hook_cb, None, len(resources))
        for resource_type in resources:
            self.worker.work_queue.put_nowait(resource_type)
        await self.worker.schedule_workers()

        # Check diffs for individual resource items
        await self.worker.init_workers(self._diffs_worker_cb, None, None)
        for _id, resource_type in self.resources_manager.all_resources_to_type.items():
            self.worker.work_queue.put_nowait((resource_type, _id, False))
        for _id, resource_type in self.resources_manager.all_cleanup_resources.items():
            self.worker.work_queue.put_nowait((resource_type, _id, True))
        await self.worker.schedule_workers()

    async def _diffs_worker_cb(self, q_item: List) -> None:
        resource_type, _id, delete = q_item
        r_class = self.config.resources[resource_type]

        if delete:
            self.config.logger.info(
                "{} resource with source ID {} to be deleted: \n {}".format(
                    resource_type,
                    _id,
                    pformat(r_class.config.resources[resource_type].resource_config.destination_resources[_id]),
                )
            )
        else:
            resource = self.config.resources[resource_type].resource_config.source_resources[_id]

            if not r_class.filter(resource):
                return
            await r_class._pre_resource_action_hook(_id, resource)

            try:
                r_class.connect_resources(_id, resource)
            except ResourceConnectionError:
                return

            if _id in r_class.resource_config.destination_resources:
                diff = check_diff(r_class.resource_config, r_class.resource_config.destination_resources[_id], resource)
                if diff:
                    self.config.logger.info(
                        "{} resource source ID {} diff: \n {}".format(resource_type, _id, pformat(diff))
                    )
            else:
                self.config.logger.info(
                    "Resource to be added {} source ID {}: \n {}".format(resource_type, _id, pformat(resource))
                )

    async def import_resources(self) -> None:
        # Get all resources for each resource type
        tmp_storage = defaultdict(list)
        resources = self.config.resources_arg
        await self.worker.init_workers(self._import_get_resources_cb, None, len(resources), tmp_storage)
        for resource_type in resources:
            self.worker.work_queue.put_nowait(resource_type)
        await self.worker.schedule_workers_with_pbar(total=len(resources))
        self.config.logger.info(f"Finished getting resources. {self.worker.counter}")

        # Begin importing individual resource items
        self.config.logger.info("Importing individual resource items")
        await self.worker.init_workers(self._import_resource, None, None)
        total = 0
        for k, v in tmp_storage.items():
            total += len(v)
            for resource in v:
                self.worker.work_queue.put_nowait((k, resource))
        await self.worker.schedule_workers_with_pbar(total=total)
        self.config.logger.info(f"Finished importng individual resource items. {self.worker.counter}.")

        # Dump resources
        dump_resources(self.config, set(self.config.resources_arg), SOURCE_ORIGIN)

    async def _import_get_resources_cb(self, resource_type: str, tmp_storage) -> None:
        self.config.logger.info("Getting resources for %s", resource_type)

        r_class = self.config.resources[resource_type]
        r_class.resource_config.source_resources.clear()

        try:
            get_resp = await r_class._get_resources(self.config.source_client)
            self.worker.counter.increment_success()
            tmp_storage[resource_type] = get_resp
        except Exception as e:
            self.config.logger.error(f"Error while getting resources {resource_type}: {str(e)}")
            self.worker.counter.increment_failure()

    async def _import_resource(self, q_item: List) -> None:
        resource_type, resource = q_item
        r_class = self.config.resources[resource_type]

        if not r_class.filter(resource):
            self.worker.counter.increment_filtered()
            return

        try:
            await r_class._import_resource(resource=resource)
            self.worker.counter.increment_success()
        except SkipResource as e:
            self.worker.counter.increment_skipped()
            self.config.logger.debug(str(e))
        except Exception as e:
            self.worker.counter.increment_failure()
            self.config.logger.error(f"Error while importing resource {resource_type}: {str(e)}")

    async def _force_missing_dep_import_cb(self, q_item: List):
        resource_type, _id = q_item
        try:
            _id = await self.config.resources[resource_type]._import_resource(_id=_id)
        except CustomClientHTTPError as e:
            self.config.logger.error(f"error importing dependency {resource_type} with id {_id}: {str(e)}")
            return

        self.resources_manager.all_resources_to_type[_id] = resource_type
        missing_deps = self.resources_manager._resource_connections(_id, resource_type)
        self.resources_manager.dependencies_graph[_id] = missing_deps
        for missing_id in missing_deps:
            self.worker.work_queue.put_nowait((self.resources_manager.all_missing_resources[missing_id], missing_id))

    async def _cleanup_worker(self, q_item: List) -> None:
        resource_type, _id = q_item
        self.config.logger.info(f"deleting resource type {resource_type} with id: {_id}")
        try:
            await self.config.resources[resource_type]._delete_resource(_id)
            self.worker.counter.increment_success()
        except Exception as e:
            self.config.logger.error(f"error deleting resource {resource_type} with id {_id}: {str(e)}")
            self.worker.counter.increment_failure()

    async def _pre_apply_hook_cb(self, resource_type: str) -> None:
        try:
            await self.config.resources[resource_type]._pre_apply_hook()
        except Exception as e:
            self.config.logger.warning(f"Error while running pre-apply hook: {str(e)}")

    async def run_sorter(self):
        loop = asyncio.get_event_loop()
        while await loop.run_in_executor(None, self.sorter.is_active):
            for _id in self.sorter.get_ready():
                if _id not in self.resources_manager.all_resources_to_type:
                    # at this point, we already attempted to import missing resources
                    # so mark the node as complete and continue
                    self.sorter.done(_id)
                    continue
                await self.worker.work_queue.put((self.resources_manager.all_resources_to_type[_id], _id))
            await asyncio.sleep(0)


def _cleanup_prompt(config: Configuration, resources_to_cleanup: Dict[str, str], prompt: bool = True) -> bool:
    if config.cleanup == FORCE or not prompt:
        return True
    elif config.cleanup == TRUE:
        for _id, resource_type in resources_to_cleanup.items():
            config.logger.warning(
                f"Following resource will be deleted: \n"
                f"{pformat(config.resources[resource_type].resource_config.destination_resources[_id])}"
            )

        return confirm("Delete above resources from destination org?")
    else:
        return False
