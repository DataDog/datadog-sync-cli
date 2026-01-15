# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import asyncio
from collections import defaultdict
from copy import deepcopy
from time import sleep
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
        self.cleanup_sorter: Optional[TopologicalSorter] = None
        self.worker: Optional[Workers] = None
        self._dependency_graph = Optional[Dict[Tuple[str, str], List[Tuple[str, str]]]]

    async def init_async(self) -> None:
        self.worker: Workers = Workers(self.config)

    async def reset(self) -> None:
        if self.config.backup_before_reset:
            await self.import_resources()
        else:
            # make the warning red and give the user time to hit ctrl-c
            self.config.logger.warning("\n\033[91m\nABOUT TO RESET WITHOUT BACKUP\033[00m\n")
            sleep(5)
            await self.import_resources_without_saving()

        # move the import data from source to destination
        self.config.state._data.destination = self.config.state._data.source

        for resource_type in self.config.resources_arg:
            resources = {}
            for _id, resource in self.config.state._data.destination[resource_type].items():
                resources[(resource_type, _id)] = resource

            if resources:
                delete = _cleanup_prompt(self.config, resources)
                if delete:
                    self.config.logger.info("deleting resources...")
                    await self.worker.init_workers(self._cleanup_worker, None, None)
                    for resource in resources:
                        self.worker.work_queue.put_nowait(resource)
                    await self.worker.schedule_workers()
                    self.config.logger.info("finished deleting resources")

    async def apply_resources(self) -> Tuple[int, int]:
        # Build dependency graph and missing resources
        self._dependency_graph, missing = self.get_dependency_graph()

        # Import resources that are missing but needed for resource connections
        if self.config.force_missing_dependencies and missing:
            self.config.logger.info("importing missing dependencies...")
            await self.worker.init_workers(self._force_missing_dep_import_cb, None, len(missing))
            for item in missing:
                self.worker.work_queue.put_nowait(item)
            await self.worker.schedule_workers()
            self.config.logger.info("finished importing missing dependencies")
        else:
            self.config.logger.info("did not import missing dependencies...")

        # handle resource cleanups
        if self.config.cleanup != FALSE:
            cleanup_resources = self.config.state.get_resources_to_cleanup(self.config.resources_arg)
            if cleanup_resources:
                cleanup = _cleanup_prompt(self.config, cleanup_resources)
                if cleanup:
                    self.config.logger.info("cleaning up resources with dependency ordering...")

                    # Build reverse dependency graph for ordered cleanup
                    try:
                        cleanup_graph = self.get_cleanup_dependency_graph(cleanup_resources)
                        self.config.logger.info(
                            f"Built cleanup dependency graph with {len(cleanup_graph)} resources"
                        )

                        # Detect circular dependencies
                        from datadog_sync.utils.resource_utils import detect_circular_dependencies

                        cycle = detect_circular_dependencies(cleanup_graph)
                        if cycle:
                            # Circular dependency detected!
                            cycle_str = " -> ".join([f"{rt}:{rid}" for rt, rid in cycle])
                            self.config.logger.error(
                                f"Circular dependency detected in cleanup graph: {cycle_str}"
                            )
                            self.config.logger.error(
                                "Cannot safely delete resources. Please manually break circular references first."
                            )
                            raise ValueError(
                                f"Circular dependency in cleanup graph: {cycle_str}. "
                                "Manual intervention required to break the cycle."
                            )

                        # Initialize cleanup sorter
                        self.cleanup_sorter = init_topological_sorter(cleanup_graph)

                        # Initialize workers with cleanup sorter stop condition
                        await self.worker.init_workers(
                            self._cleanup_worker, lambda: not self.cleanup_sorter.is_active(), None
                        )

                        # Run cleanup with ordering
                        if self.config.show_progress_bar:
                            await self.worker.schedule_workers_with_pbar(
                                total=len(cleanup_graph), additional_coros=[self.run_cleanup_sorter()]
                            )
                        else:
                            await self.worker.schedule_workers(additional_coros=[self.run_cleanup_sorter()])

                        self.config.logger.info("finished cleaning up resources")

                    except ValueError as e:
                        # Circular dependency - already logged, re-raise
                        raise
                    except Exception as e:
                        # Unexpected error building or running cleanup graph
                        self.config.logger.error(f"Error during ordered cleanup: {str(e)}")
                        self.config.logger.warning(
                            "Falling back to unordered cleanup (may fail due to dependency issues)"
                        )

                        # Fallback to old unordered behavior
                        await self.worker.init_workers(self._cleanup_worker, None, None)
                        for i in cleanup_resources:
                            self.worker.work_queue.put_nowait(i)
                        await self.worker.schedule_workers()
                        self.config.logger.info("finished cleaning up resources (unordered fallback)")

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
        if self.config.show_progress_bar:
            await self.worker.schedule_workers_with_pbar(
                total=len(self._dependency_graph), additional_coros=[self.run_sorter()]
            )
        else:
            await self.worker.schedule_workers(additional_coros=[self.run_sorter()])
        self.config.logger.info(f"finished syncing resource items: {self.worker.counter}.")

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

            prep_resource(r_class.resource_config, resource)
            if _id in self.config.state.destination[resource_type]:
                destination_copy = deepcopy(self.config.state.destination[resource_type][_id])
                prep_resource(r_class.resource_config, destination_copy)
                diff = check_diff(r_class.resource_config, resource, destination_copy)
                if not diff:
                    raise SkipResource(_id, resource_type, "No differences detected.")

                self.config.logger.debug(f"Running update for {resource_type} with {_id}")
                await r_class._update_resource(_id, resource)
                await r_class._send_action_metrics(
                    Command.SYNC.value, _id, Status.SUCCESS.value, tags=["action_sub_type:update"]
                )
                self.config.logger.debug(f"Finished update for {resource_type} with {_id}")
            else:
                self.config.logger.debug(f"Running create for {resource_type} with id: {_id}")
                await r_class._create_resource(_id, resource)
                await r_class._send_action_metrics(
                    Command.SYNC.value, _id, Status.SUCCESS.value, tags=["action_sub_type:create"]
                )
                self.config.logger.debug(f"finished create for {resource_type} with id: {_id}")

            self.worker.counter.increment_success()

        except SkipResource as e:
            self.config.logger.info(f"skipping resource: {str(e)}", resource_type=resource_type, _id=_id)
            self.worker.counter.increment_skipped()
            await r_class._send_action_metrics(Command.SYNC.value, _id, Status.SKIPPED.value, tags=["reason:unknown"])
        except ResourceConnectionError:
            self.worker.counter.increment_skipped()
            await r_class._send_action_metrics(
                Command.SYNC.value, _id, Status.SKIPPED.value, tags=["reason:connection_error"]
            )
        except Exception as e:
            self.worker.counter.increment_failure()
            self.config.logger.error(str(e), resource_type=resource_type, _id=_id)
            await r_class._send_action_metrics(Command.SYNC.value, _id, Status.FAILURE.value)
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
                "to be deleted: \n {}".format(
                    pformat(self.config.state.destination[resource_type][_id]),
                ),
                resource_type=resource_type,
                _id=_id,
            )
        else:
            resource = self.config.state.source[resource_type][_id]

            if not r_class.filter(resource):
                return

            try:
                await r_class._pre_resource_action_hook(_id, resource)
            except SkipResource as e:
                self.config.logger.warning(f"skipping resource: resource_type:{resource_type} id:{_id}")
                self.config.logger.debug(str(e))
                return

            try:
                r_class.connect_resources(_id, resource)
            except ResourceConnectionError:
                return

            if _id in self.config.state.destination[resource_type]:
                # We have to compare the prepared versions to deal w/ non-nullable attributes
                destination_copy = deepcopy(self.config.state.destination[resource_type][_id])
                resource_copy = deepcopy(resource)
                prep_resource(r_class.resource_config, destination_copy)
                prep_resource(r_class.resource_config, resource_copy)
                diff = check_diff(r_class.resource_config, destination_copy, resource_copy)
                if diff:
                    self.config.logger.info("diff: \n {}".format(pformat(diff)), resource_type=resource_type, _id=_id)
            else:
                self.config.logger.info(f"to be created: {resource_type} {_id}")

    async def import_resources(self) -> None:
        await self.import_resources_without_saving()
        self.config.state.dump_state(Origin.SOURCE)

    async def import_resources_without_saving(self) -> None:
        # Get all resources for each resource type
        tmp_storage = defaultdict(list)
        await self.worker.init_workers(self._import_get_resources_cb, None, len(self.config.resources_arg), tmp_storage)
        for resource_type in self.config.resources_arg:
            self.worker.work_queue.put_nowait(resource_type)
        if self.config.show_progress_bar:
            await self.worker.schedule_workers_with_pbar(total=len(self.config.resources_arg))
        else:
            await self.worker.schedule_workers()
        self.config.logger.info(f"Finished getting resources. {self.worker.counter}")

        # Begin importing individual resource items
        self.config.logger.info("importing individual resource items")
        await self.worker.init_workers(self._import_resource, None, None)
        total = 0
        for k, v in tmp_storage.items():
            total += len(v)
            for resource in v:
                self.worker.work_queue.put_nowait((k, resource))
        if self.config.show_progress_bar:
            await self.worker.schedule_workers_with_pbar(total=total)
        else:
            await self.worker.schedule_workers()
        self.config.logger.info(f"finished importing individual resource items: {self.worker.counter}.")

    async def _import_get_resources_cb(self, resource_type: str, tmp_storage) -> None:
        self.config.logger.info("getting resources", resource_type=resource_type)

        r_class = self.config.resources[resource_type]
        self.config.state.source[resource_type].clear()

        try:
            get_resp = await r_class._get_resources(self.config.source_client)
            self.worker.counter.increment_success()
            tmp_storage[resource_type] = get_resp
        except TimeoutError:
            self.worker.counter.increment_failure()
            self.config.logger.error(f"TimeoutError while getting resources {resource_type}")
        except Exception as e:
            self.worker.counter.increment_failure()
            self.config.logger.error(f"Error while getting resources {resource_type}: {str(e)}")

    async def _import_resource(self, q_item: List) -> None:
        resource_type, resource = q_item
        _id = resource.get("id")
        r_class = self.config.resources[resource_type]

        if not r_class.filter(resource):
            self.worker.counter.increment_filtered()
            return

        try:
            await r_class._import_resource(resource=resource)
            self.worker.counter.increment_success()
            await r_class._send_action_metrics(Command.IMPORT.value, _id, Status.SUCCESS.value)
        except SkipResource as e:
            self.worker.counter.increment_skipped()
            await r_class._send_action_metrics(Command.IMPORT.value, _id, Status.SKIPPED.value)
            self.config.logger.info(f"skipping resource: {str(e)}", resource_type=resource_type, _id=_id)
            self.config.logger.debug(str(e))
        except Exception as e:
            self.worker.counter.increment_failure()
            await r_class._send_action_metrics(Command.IMPORT.value, _id, Status.FAILURE.value)
            self.config.logger.error(f"error while importing resource: resource_type:{resource_type} id:{_id}")
            self.config.logger.debug(f"error detail: {str(e)}", resource_type=resource_type)

    async def _force_missing_dep_import_cb(self, q_item: List):
        resource_type, _id = q_item
        try:
            _id = await self.config.resources[resource_type]._import_resource(_id=_id)
        except CustomClientHTTPError as e:
            self.config.logger.error(f"error importing dependency: {str(e)}", resource_type=resource_type, _id=_id)
            return

        failed_connections, missing_deps = self._resource_connections(resource_type, _id)
        self._dependency_graph[q_item] = failed_connections
        for missing_id in missing_deps:
            self.worker.work_queue.put_nowait(missing_id)

    async def _cleanup_worker(self, q_item: List) -> None:
        resource_type, _id = q_item
        self.config.logger.info("deleting resource", resource_type=resource_type, _id=_id)

        r_class = self.config.resources[resource_type]
        try:
            if not r_class.resource_config.concurrent:
                await r_class.resource_config.async_lock.acquire()

            await r_class._delete_resource(_id)
            self.worker.counter.increment_success()
            await r_class._send_action_metrics("delete", _id, Status.SUCCESS.value)
        except SkipResource as e:
            self.worker.counter.increment_skipped()
            await r_class._send_action_metrics("delete", _id, Status.SKIPPED.value, tags=["reason:unknown"])
            self.config.logger.info(f"skipping resource: {str(e)}", resource_type=resource_type, _id=_id)
            self.config.logger.info(f"skip deleting resource: {str(e)}", resource_type=resource_type, _id=_id)
        except Exception as e:
            self.worker.counter.increment_failure()
            await r_class._send_action_metrics("delete", _id, Status.FAILURE.value)
            self.config.logger.error(f"error deleting resource {resource_type} with id {_id}: {str(e)}")
        finally:
            # Mark as done in cleanup sorter if it exists
            if hasattr(self, 'cleanup_sorter') and self.cleanup_sorter:
                self.cleanup_sorter.done(q_item)

            if not r_class.resource_config.concurrent:
                r_class.resource_config.async_lock.release()

    async def _pre_apply_hook_cb(self, resource_type: str) -> None:
        try:
            await self.config.resources[resource_type]._pre_apply_hook()
        except Exception as e:
            self.config.logger.warning(f"error while running pre-apply hook: {str(e)}", resource_type=resource_type)

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

    async def run_cleanup_sorter(self):
        """Mirror of run_sorter() but for cleanup operations.

        Continuously feeds deletion-ready resources to workers in proper order.
        Resources are only deleted after all their dependents are deleted.
        """
        loop = asyncio.get_event_loop()

        while await loop.run_in_executor(None, self.cleanup_sorter.is_active):
            for node in self.cleanup_sorter.get_ready():
                resource_type, _id = node

                # Verify resource still exists in destination
                if _id not in self.config.state.destination[resource_type]:
                    # Already deleted or doesn't exist
                    self.config.logger.debug(
                        f"Resource {resource_type}:{_id} already deleted, marking as done"
                    )
                    self.cleanup_sorter.done(node)
                    continue

                # Add to work queue for deletion
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

    def get_cleanup_dependency_graph(
        self, cleanup_resources: Dict[Tuple[str, str], str | None]
    ) -> Dict[Tuple[str, str], Set[Tuple[str, str]]]:
        """Build REVERSE dependency graph for cleanup.

        For deletion, we need to delete dependents BEFORE dependencies.
        This inverts the normal creation graph.

        Args:
            cleanup_resources: Resources to be deleted from destination

        Returns:
            Dict mapping (resource_type, _id) to set of resources that depend on it

        Example:
            If Dashboard depends on Monitor:
            Creation graph: {("dashboards", "dash-1"): [("monitors", "mon-1")]}
            Cleanup graph:  {("monitors", "mon-1"): {("dashboards", "dash-1")}}

            This means: Monitor "mon-1" can only be deleted AFTER Dashboard "dash-1"
        """
        reverse_graph = defaultdict(set)

        # Initialize all cleanup resources with empty dependencies
        for resource_key in cleanup_resources.keys():
            reverse_graph[resource_key] = set()

        # Build reverse dependencies by scanning destination state
        for resource_type, _id in cleanup_resources.keys():
            if resource_type not in self.config.resources:
                continue

            r_config = self.config.resources[resource_type].resource_config
            if not r_config.resource_connections:
                continue

            # Get the actual resource from destination state (already in memory)
            if _id not in self.config.state.destination[resource_type]:
                self.config.logger.debug(
                    f"Resource {resource_type}:{_id} not in destination state, skipping dependency analysis"
                )
                continue

            resource = self.config.state.destination[resource_type][_id]

            # For each dependency this resource has
            for dep_type, attr_paths in r_config.resource_connections.items():
                for attr_path in attr_paths:
                    # Find dependency IDs in the resource
                    dep_ids = self._extract_dependency_ids(resource, attr_path, dep_type)

                    # For each dependency, add THIS resource as a dependent
                    for dep_id in dep_ids:
                        dep_key = (dep_type, dep_id)
                        # Only include if the dependency is also being cleaned up
                        if dep_key in cleanup_resources:
                            # This says: "dep_key must be deleted AFTER (resource_type, _id)"
                            reverse_graph[dep_key].add((resource_type, _id))
                            self.config.logger.debug(
                                f"Dependency edge: {dep_type}:{dep_id} <- {resource_type}:{_id}"
                            )

        return dict(reverse_graph)

    def _extract_dependency_ids(self, resource: Dict, attr_path: str, dep_type: str) -> Set[str]:
        """Extract dependency IDs from a resource at the given attribute path.

        Args:
            resource: The resource dict
            attr_path: Dot-separated path like "widgets.definition.alert_id"
            dep_type: Type of dependency (for context)

        Returns:
            Set of dependency IDs found
        """
        ids = set()

        def extract_from_obj(obj, path_parts):
            if not obj or not path_parts:
                return

            current_key = path_parts[0]
            remaining = path_parts[1:]

            if isinstance(obj, list):
                for item in obj:
                    extract_from_obj(item, path_parts)
            elif isinstance(obj, dict):
                if current_key in obj:
                    if not remaining:
                        # We're at the target attribute
                        value = obj[current_key]
                        if value:
                            if isinstance(value, list):
                                ids.update(str(v) for v in value if v)
                            else:
                                ids.add(str(value))
                    else:
                        # Keep traversing
                        extract_from_obj(obj[current_key], remaining)

        path_parts = attr_path.split(".")
        extract_from_obj(resource, path_parts)
        return ids


def _cleanup_prompt(
    config: Configuration, resources_to_cleanup: Dict[Tuple[str, str], str | None], prompt: bool = True
) -> bool:
    if config.cleanup == FORCE or not prompt:
        return True
    elif config.cleanup == TRUE:
        for resource_type, _id in resources_to_cleanup:
            config.logger.warning(
                f"Resource will be deleted: \n" f"{pformat(config.state.destination[resource_type][_id])}",
                resource_type=resource_type,
                _id=_id,
            )

        return confirm(f"Delete above {len(resources_to_cleanup)} resources from destination org?")
    else:
        return False
