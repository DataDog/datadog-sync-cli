# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import asyncio
import os
from sys import exit

from click import confirm
from pprint import pformat

from datadog_sync.constants import (
    DESTINATION_RESOURCES_DIR,
    SOURCE_RESOURCES_DIR,
    Command,
    DESTINATION_ORIGIN,
    SOURCE_ORIGIN,
)
from datadog_sync.utils.configuration import build_config
from datadog_sync.utils.resources_manager import ResourcesManager
from datadog_sync.constants import TRUE, FALSE, FORCE
from datadog_sync.utils.resource_utils import (
    CustomClientHTTPError,
    LoggedException,
    ResourceConnectionError,
    SkipResource,
    check_diff,
    create_global_downtime,
    dump_resources,
    prep_resource,
    thread_pool_executor,
    init_topological_sorter,
    write_resources_file,
)
from typing import Dict, TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from datadog_sync.utils.configuration import Configuration
    from graphlib import TopologicalSorter


async def run_cmd_async(cmd: Command, **kwargs):
    # Build config
    cfg = build_config(cmd, **kwargs)

    # Initiate async items
    await cfg._init(cmd)

    # Initiate resources handler
    handler = ResourcesHandler(cfg)

    cfg.logger.info(f"Starting {cmd.value}...")

    # Run specific handler
    if cmd == Command.IMPORT:
        os.makedirs(SOURCE_RESOURCES_DIR, exist_ok=True)
        await handler.import_resources()
    elif cmd == Command.SYNC:
        os.makedirs(DESTINATION_RESOURCES_DIR, exist_ok=True)
        await handler.apply_resources()
    elif cmd == Command.DIFFS:
        await handler.diffs()
    else:
        cfg.logger.error(f"Command {cmd.value} not found")
        exit(1)

    cfg.logger.info(f"Finished {cmd.value}")

    # Cleanup session before exit
    await cfg._exit_cleanup()

    if cfg.logger.exception_logged:
        exit(1)


class ResourcesHandler:
    def __init__(self, config: Configuration) -> None:
        self.config = config

        # Additional config for resource manager
        self.resources_manager: ResourcesManager = ResourcesManager(config)
        self.sorter: Optional[TopologicalSorter] = None

        # Queues for async processing
        self.work_queue: asyncio.Queue = asyncio.Queue()  # work task queue
        self.finished: asyncio.Queue = asyncio.Queue()  # finished task queue

    async def apply_resources(self) -> Tuple[int, int]:
        # Import resources that are missing but needed for resource connections
        serial_executor = thread_pool_executor(1)
        tasks = []
        if self.config.force_missing_dependencies and not self.resources_manager.missing_resources_queue.empty():
            self.config.logger.info("importing missing dependencies")

            seen_resource_types = set()
            while True:
                while True:
                    # consume all of the current missing dependencies
                    try:
                        q_item = self.resources_manager.missing_resources_queue.get_nowait()
                        seen_resource_types.add(q_item[1])
                        tasks.append(asyncio.create_task(self._force_missing_dep_import_worker(*q_item)))
                    except Exception:
                        break
                # Wait for current badge of imports to finish
                await asyncio.gather(*tasks, return_exceptions=True)

                # Check if queue is empty after importing all missing resources.
                # This will not be empty if the imported resources have further missing dependencies.
                if self.resources_manager.missing_resources_queue.empty():
                    break

            tasks.clear()
            # Dump seen resources
            dump_resources(self.config, seen_resource_types, SOURCE_ORIGIN)

            self.config.logger.info("finished importing missing dependencies")

        # handle resource cleanups
        if self.config.cleanup != FALSE and self.resources_manager.all_cleanup_resources:
            cleanup = _cleanup_prompt(self.config, self.resources_manager.all_cleanup_resources)
            if cleanup:
                for _id, resource_type in self.resources_manager.all_cleanup_resources.items():
                    tasks.append(asyncio.create_task(self._cleanup_worker(_id, resource_type)))
                await asyncio.gather(*tasks, return_exceptions=True)
                tasks.clear()

        # Run pre-apply hooks
        for resource_type in set(self.resources_manager.all_resources.values()):
            tasks.append(asyncio.create_task(self.config.resources[resource_type]._pre_apply_hook()))

        # Additional pre-apply actions
        if self.config.create_global_downtime:
            tasks.append(asyncio.create_task(create_global_downtime(self.config)))

        await asyncio.gather(*tasks, return_exceptions=True)
        for task in tasks:
            try:
                task.result()
            except Exception as e:
                self.config.logger.warning(f"Error while running pre-apply hook: {str(e)}")
        tasks.clear()

        # initalize topological sorters
        self.sorter = init_topological_sorter(self.resources_manager.dependencies_graph)

        while self.sorter.is_active():
            for _id in self.sorter.get_ready():
                if _id not in self.resources_manager.all_resources:
                    # at this point, we already attempted to import missing resources
                    # so mark the node as complete and continue
                    self.sorter.done(_id)
                    continue

                tasks.append(
                    asyncio.create_task(self._apply_resource_worker(_id, self.resources_manager.all_resources[_id]))
                )

            await asyncio.gather(*tasks, return_exceptions=True)

        await asyncio.gather(*tasks, return_exceptions=True)
        successes = errors = 0
        for task in tasks:
            try:
                task.result()
            except ResourceConnectionError:
                # This should already be handled in connect_resource method
                continue
            except LoggedException:
                errors += 1
            except Exception as e:
                self.config.logger.error(str(e))
                errors += 1
            else:
                successes += 1

        # shutdown executors
        serial_executor.shutdown()

        # dump synced resources
        synced_resource_types = set(self.resources_manager.all_resources.values())
        cleanedup_resource_types = set(self.resources_manager.all_cleanup_resources.values())
        dump_resources(self.config, synced_resource_types.union(cleanedup_resource_types), DESTINATION_ORIGIN)

        return successes, errors

    async def _apply_resource_worker(self, _id: str, resource_type: str) -> None:
        r_class = self.config.resources[resource_type]
        resource = r_class.resource_config.source_resources[_id]
        if _id not in self.resources_manager.all_missing_resources:
            if not r_class.filter(resource):
                return

        if not r_class.resource_config.concurrent:
            await r_class.resource_config.async_lock.acquire()

        try:
            # Run hooks
            await r_class._pre_resource_action_hook(_id, resource)
            r_class.connect_resources(_id, resource)

            if _id in r_class.resource_config.destination_resources:
                diff = check_diff(r_class.resource_config, resource, r_class.resource_config.destination_resources[_id])
                if diff:
                    self.config.logger.info(f"Running update for {resource_type} with {_id}")

                    prep_resource(r_class.resource_config, resource)
                    try:
                        await r_class._update_resource(_id, resource)
                    except Exception as e:
                        self.config.logger.error(
                            f"Error while updating resource {resource_type}. source ID: {_id} -  Error: {str(e)}"
                        )
                        raise LoggedException(e)

                    self.config.logger.info(f"Finished update for {resource_type} with {_id}")
            else:
                self.config.logger.info(f"Running create for {resource_type} with {_id}")

                prep_resource(r_class.resource_config, resource)
                try:
                    await r_class._create_resource(_id, resource)
                except Exception as e:
                    self.config.logger.error(
                        f"Error while creating resource {resource_type}. source ID: {_id} - Error: {str(e)}"
                    )
                    raise LoggedException(e)

                self.config.logger.info(f"finished create for {resource_type} with {_id}")

        finally:
            # always place in done queue regardless of exception thrown
            self.sorter.done(_id)
            if not r_class.resource_config.concurrent:
                r_class.resource_config.async_lock.release()

    async def diffs(self) -> None:
        # Run pre-apply hooks
        tasks = [
            asyncio.create_task(self.config.resources[resource_type]._pre_apply_hook())
            for resource_type in set(self.resources_manager.all_resources.values())
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        tasks = []
        for _id, resource_type in self.resources_manager.all_resources.items():
            tasks.append(asyncio.create_task(self._diffs_worker(_id, resource_type)))

        for _id, resource_type in self.resources_manager.all_cleanup_resources.items():
            tasks.append(asyncio.create_task(self._diffs_worker(_id, resource_type, delete=True)))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _diffs_worker(self, _id, resource_type, delete=False) -> None:
        r_class = self.config.resources[resource_type]

        if delete:
            print(
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
                    print("{} resource source ID {} diff: \n {}".format(resource_type, _id, pformat(diff)))
            else:
                print("Resource to be added {} source ID {}: \n {}".format(resource_type, _id, pformat(resource)))

    async def import_resources(self) -> None:
        tasks = [
            asyncio.create_task(self._import_resources_helper(resource_type))
            for resource_type in self.config.resources_arg
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _import_resources_helper(self, resource_type: str) -> None:
        self.config.logger.info("Importing %s", resource_type)

        r_class = self.config.resources[resource_type]
        r_class.resource_config.source_resources.clear()

        try:
            get_resp = await r_class._get_resources(self.config.source_client)
        except Exception as e:
            self.config.logger.error(f"Error while importing resources {resource_type}: {str(e)}")
            return 0, 0

        tasks = []
        for r in get_resp:
            if not r_class.filter(r):
                continue
            tasks.append(asyncio.create_task(r_class._import_resource(resource=r)))

        await asyncio.gather(*tasks, return_exceptions=True)

        successes = errors = 0
        for task in tasks:
            try:
                task.result()
            except SkipResource as e:
                self.config.logger.debug(str(e))
            except Exception as e:
                self.config.logger.error(f"Error while importing resource {resource_type}: {str(e)}")
                errors += 1
            else:
                successes += 1

        write_resources_file(resource_type, SOURCE_ORIGIN, r_class.resource_config.source_resources)

        self.config.logger.info(f"Finished importing {resource_type}: {successes} successes, {errors} errors")

    async def _force_missing_dep_import_worker(self, _id: str, resource_type: str):
        try:
            _id = await self.config.resources[resource_type]._import_resource(_id=_id)
        except CustomClientHTTPError as e:
            self.config.logger.error(f"error importing {resource_type} with id {_id}: {str(e)}")
            return

        self.resources_manager.all_resources[_id] = resource_type
        self.resources_manager.dependencies_graph[_id] = self.resources_manager._resource_connections(
            _id, resource_type
        )

    async def _cleanup_worker(self, _id: str, resource_type: str) -> None:
        self.config.logger.info(f"deleting resource type {resource_type} with id: {_id}")
        await self.config.resources[resource_type]._delete_resource(_id)


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


# async def worker(tasks: WorkQueue, results: FinishedQueue) -> NoReturn:
#     while True:
#         package = await tasks.get()
#         await build(package)
#         results.put_nowait(package)
