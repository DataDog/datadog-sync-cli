# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import asyncio
from collections import defaultdict
from copy import deepcopy
from typing import Dict, TYPE_CHECKING, List, Optional, Set, Tuple

from click import confirm
from pprint import pformat

from datadog_sync.constants import TRUE, FALSE, FORCE, Command, Origin, Status
from datadog_sync.utils.resource_utils import (
    CustomClientHTTPError,
    ResourceConnectionError,
    SkipResource,
    check_diff,
    create_global_downtime,
    find_attr,
    prep_resource,
    init_topological_sorter,
)
from datadog_sync.utils.workers import Workers


if TYPE_CHECKING:
    from datadog_sync.utils.configuration import Configuration
    from graphlib import TopologicalSorter


class ResourcesHandler:
    def __init__(self, config: Configuration) -> None:
        self.config = config
        self.sorter: Optional[TopologicalSorter] = None
        self.worker: Optional[Workers] = None
        self._dependency_graph = Optional[Dict[Tuple[str, str], List[Tuple[str, str]]]]

    async def init_async(self) -> None:
        self.worker: Workers = Workers(self.config)

    async def apply_resources(self) -> Tuple[int, int]:
        # Build dependency graph and missing resources
        self._dependency_graph, missing = self.get_dependency_graph()

        # Import resources that are missing but needed for resource connections
        if self.config.force_missing_dependencies and missing:
            self.config.logger.info("Importing missing dependencies...")
            await self.worker.init_workers(self._force_missing_dep_import_cb, None, len(missing))
            for item in missing:
                self.worker.work_queue.put_nowait(item)
            await self.worker.schedule_workers()
            self.config.logger.info("finished importing missing dependencies")

        # handle resource cleanups
        if self.config.cleanup != FALSE:
            cleanup_resources = self.config.state.get_resources_to_cleanup(self.config.resources_arg)
            if cleanup_resources:
                cleanup = _cleanup_prompt(self.config, cleanup_resources)
                if cleanup:
                    self.config.logger.info("Cleaning up resources...")
                    await self.worker.init_workers(self._cleanup_worker, None, None)
                    for i in cleanup_resources:
                        self.worker.work_queue.put_nowait(i)
                    await self.worker.schedule_workers()
                    self.config.logger.info("finished cleaning up resources")

        # Run pre-apply hooks
        resource_types = set(i[0] for i in self._dependency_graph)
        await self.worker.init_workers(self._pre_apply_hook_cb, None, len(resource_types))
        for resource_type in resource_types:
            self.worker.work_queue.put_nowait(resource_type)
        await self.worker.schedule_workers()

        # Additional pre-apply actions
        if self.config.create_global_downtime:
            await create_global_downtime(self.config)

        # initalize topological sorters
        self.sorter = init_topological_sorter(self._dependency_graph)
        await self.worker.init_workers(self._apply_resource_cb, lambda: not self.sorter.is_active(), None)
        await self.worker.schedule_workers_with_pbar(
            total=len(self._dependency_graph), additional_coros=[self.run_sorter()]
        )
        self.config.logger.info(f"Finished syncing resource items. {self.worker.counter}.")

        self.config.state.dump_state()

    async def _apply_resource_cb(self, q_item: List) -> None:
        resource_type, _id = q_item

        try:
            r_class = self.config.resources[resource_type]
            resource = deepcopy(self.config.state.source[resource_type][_id])

            if not r_class.resource_config.concurrent:
                await r_class.resource_config.async_lock.acquire()

            if not r_class.filter(resource):
                self.worker.counter.increment_filtered()
                return

            # Run hooks
            await r_class._pre_resource_action_hook(_id, resource)
            r_class.connect_resources(_id, resource)

            if _id in self.config.state.destination[resource_type]:
                diff = check_diff(r_class.resource_config, resource, self.config.state.destination[resource_type][_id])

                if not diff:
                    raise (SkipResource(_id, resource_type, "No differences detected."))

                self.config.logger.debug(f"Running update for {resource_type} with {_id}")
                prep_resource(r_class.resource_config, resource)
                await r_class._update_resource(_id, resource)
                await r_class._send_action_metrics(
                    Command.SYNC.value, _id, Status.SUCCESS, tags=["action_sub_type:update"]
                )
                self.config.logger.debug(f"Finished update for {resource_type} with {_id}")

            else:
                self.config.logger.debug(f"Running create for {resource_type} with id: {_id}")
                prep_resource(r_class.resource_config, resource)
                await r_class._create_resource(_id, resource)
                await r_class._send_action_metrics(
                    Command.SYNC.value, _id, Status.SUCCESS, tags=["action_sub_type:create"]
                )
                self.config.logger.debug(f"finished create for {resource_type} with id: {_id}")

            self.worker.counter.increment_success()

        except SkipResource as e:
            self.config.logger.info(str(e))
            self.worker.counter.increment_skipped()
            await r_class._send_action_metrics(Command.SYNC.value, _id, Status.SKIPPED, tags=["reason:up_to_date"])
        except ResourceConnectionError:
            self.worker.counter.increment_skipped()
            await r_class._send_action_metrics(Command.SYNC.value, _id, Status.SKIPPED, tags=["reason:connection_error"])
        except Exception as e:
            self.worker.counter.increment_failure()
            self.config.logger.error(str(e))
            await r_class._send_action_metrics(Command.SYNC.value, _id, Status.FAILURE)
        finally:
            # always place in done queue regardless of exception thrown
            self.sorter.done(q_item)
            if not r_class.resource_config.concurrent:
                r_class.resource_config.async_lock.release()

    async def diffs(self) -> None:
        self._dependency_graph, _ = self.get_dependency_graph()

        # Run pre-apply hooks
        resource_types = set(i[0] for i in self._dependency_graph.keys())
        await self.worker.init_workers(self._pre_apply_hook_cb, None, len(resource_types))
        for resource_type in resource_types:
            self.worker.work_queue.put_nowait(resource_type)
        await self.worker.schedule_workers()

        # Check diffs for individual resource items
        await self.worker.init_workers(self._diffs_worker_cb, None, None)
        for resource_type, _id in self._dependency_graph.keys():
            self.worker.work_queue.put_nowait((resource_type, _id, False))
        if self.config.cleanup != FALSE:
            for resource_type, _id in self.config.state.get_resources_to_cleanup(self.config.resources_arg).keys():
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
                    pformat(self.config.state.destination[resource_type][_id]),
                )
            )
        else:
            resource = self.config.state.source[resource_type][_id]

            if not r_class.filter(resource):
                return
            await r_class._pre_resource_action_hook(_id, resource)

            try:
                r_class.connect_resources(_id, resource)
            except ResourceConnectionError:
                return

            if _id in self.config.state.destination[resource_type]:
                diff = check_diff(r_class.resource_config, self.config.state.destination[resource_type][_id], resource)
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
        await self.worker.init_workers(self._import_get_resources_cb, None, len(self.config.resources_arg), tmp_storage)
        for resource_type in self.config.resources_arg:
            self.worker.work_queue.put_nowait(resource_type)
        await self.worker.schedule_workers_with_pbar(total=len(self.config.resources_arg))
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
        self.config.logger.info(f"Finished importing individual resource items. {self.worker.counter}.")

        # Dump resources
        self.config.state.dump_state(Origin.SOURCE)

    async def _import_get_resources_cb(self, resource_type: str, tmp_storage) -> None:
        self.config.logger.info("Getting resources for %s", resource_type)

        r_class = self.config.resources[resource_type]
        self.config.state.source[resource_type].clear()

        try:
            get_resp = await r_class._get_resources(self.config.source_client)
            self.worker.counter.increment_success()
            tmp_storage[resource_type] = get_resp
        except Exception as e:
            self.worker.counter.increment_failure()
            self.config.logger.error(f"Error while getting resources {resource_type}: {str(e)}")

    async def _import_resource(self, q_item: List) -> None:
        resource_type, resource = q_item
        _id = resource["id"]
        r_class = self.config.resources[resource_type]

        if not r_class.filter(resource):
            self.worker.counter.increment_filtered()
            return

        try:
            await r_class._import_resource(resource=resource)
            self.worker.counter.increment_success()
            await r_class._send_action_metrics(Command.IMPORT.value, _id, Status.SUCCESS)
        except SkipResource as e:
            self.worker.counter.increment_skipped()
            await r_class._send_action_metrics(Command.IMPORT.value, _id, Status.SKIPPED)
            self.config.logger.debug(str(e))
        except Exception as e:
            self.worker.counter.increment_failure()
            await r_class._send_action_metrics(Command.IMPORT.value, _id, Status.FAILURE)
            self.config.logger.error(f"Error while importing resource {resource_type}: {str(e)}")

    async def _force_missing_dep_import_cb(self, q_item: List):
        resource_type, _id = q_item
        try:
            _id = await self.config.resources[resource_type]._import_resource(_id=_id)
        except CustomClientHTTPError as e:
            self.config.logger.error(f"error importing dependency {resource_type} with id {_id}: {str(e)}")
            return

        failed_connections, missing_deps = self._resource_connections(resource_type, _id)
        self._dependency_graph[q_item] = failed_connections
        for missing_id in missing_deps:
            self.worker.work_queue.put_nowait(missing_id)

    async def _cleanup_worker(self, q_item: List) -> None:
        resource_type, _id = q_item
        self.config.logger.info(f"deleting resource type {resource_type} with id: {_id}")

        r_class = self.config.resources[resource_type]
        try:
            if not r_class.resource_config.concurrent:
                await r_class.resource_config.async_lock.acquire()

            await r_class._delete_resource(_id)
            self.worker.counter.increment_success()
            await r_class._send_action_metrics("delete", _id, Status.SUCCESS)
        except Exception as e:
            self.worker.counter.increment_failure()
            await r_class._send_action_metrics("delete", _id, Status.FAILURE)
            self.config.logger.error(f"error deleting resource {resource_type} with id {_id}: {str(e)}")
        finally:
            if not r_class.resource_config.concurrent:
                r_class.resource_config.async_lock.release()

    async def _pre_apply_hook_cb(self, resource_type: str) -> None:
        try:
            await self.config.resources[resource_type]._pre_apply_hook()
        except Exception as e:
            self.config.logger.warning(f"Error while running pre-apply hook: {str(e)}")

    async def run_sorter(self):
        loop = asyncio.get_event_loop()
        while await loop.run_in_executor(None, self.sorter.is_active):
            for node in self.sorter.get_ready():
                if node[1] not in self.config.state.source[node[0]]:
                    # at this point, we already attempted to import missing resources
                    # so mark the node as complete and continue
                    self.sorter.done(node)
                    continue
                await self.worker.work_queue.put(node)
            await asyncio.sleep(0)

    def get_dependency_graph(self) -> Tuple[Dict[Tuple[str, str], List[Tuple[str, str]]], Set[Tuple[str, str]]]:
        """Build the dependency graph for all resources.

        Returns:
            Tuple[Dict[Tuple[str, str], List[Tuple[str, str]]], Set[Tuple[str, str]]]: Returns
            a tuple of the dependency graph and missing resources.
        """
        dependency_graph = {}
        missing_resources = set()

        for resource_type, _id in self.config.state.get_all_resources(self.config.resources_arg).keys():
            deps, missing = self._resource_connections(resource_type, _id)
            dependency_graph[(resource_type, _id)] = deps
            missing_resources.update(missing)

        return dependency_graph, missing_resources

    def _resource_connections(self, resource_type: str, _id: str) -> Tuple[Set[Tuple[str, str]], Set[Tuple[str, str]]]:
        """Returns the failed connections and missing resources for a given resource.
        Failed connections are all dependencies of given resource that is not in destination state.
        Missing resources are all resources that have not been imported yet in source state.

        Args:
            resource_type (str): Type of the resource
            _id (str): Resource id

        Returns:
            Tuple[Set[Tuple[str, str]], Set[Tuple[str, str]]]: failed_connections, missing_resources
        """
        failed_connections = set()
        missing_resources = set()

        if not self.config.resources[resource_type].resource_config.resource_connections:
            return failed_connections, missing_resources

        resource = deepcopy(self.config.state.source[resource_type][_id])
        if self.config.resources[resource_type].resource_config.resource_connections:
            for resource_to_connect, v in self.config.resources[
                resource_type
            ].resource_config.resource_connections.items():
                for attr_connection in v:
                    failed = find_attr(
                        attr_connection,
                        resource_to_connect,
                        resource,
                        self.config.resources[resource_type].connect_id,
                    )
                    if failed:
                        # After retrieving all of the failed connections, we check if
                        # the resources are imported. Otherwise append to missing with its type.
                        for f_id in failed:
                            if f_id not in self.config.state.source[resource_to_connect]:
                                missing_resources.add((resource_to_connect, f_id))

                            failed_connections.add((resource_to_connect, f_id))

        return failed_connections, missing_resources


def _cleanup_prompt(
    config: Configuration, resources_to_cleanup: Dict[Tuple[str, str], str | None], prompt: bool = True
) -> bool:
    if config.cleanup == FORCE or not prompt:
        return True
    elif config.cleanup == TRUE:
        for resource_type, _id in resources_to_cleanup:
            config.logger.warning(
                f"Following resource will be deleted: \n" f"{pformat(config.state.destination[resource_type][_id])}"
            )

        return confirm("Delete above resources from destination org?")
    else:
        return False
