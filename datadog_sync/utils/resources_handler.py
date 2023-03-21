# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from collections import deque
from concurrent.futures import wait

from click import confirm
from pprint import pformat

from datadog_sync.constants import DESTINATION_ORIGIN, SOURCE_ORIGIN
from datadog_sync.utils.resources_manager import ResourcesManager
from datadog_sync.constants import TRUE, FALSE, FORCE
from datadog_sync.utils.resource_utils import (
    CustomClientHTTPError,
    LoggedException,
    ResourceConnectionError,
    check_diff,
    dump_resources,
    prep_resource,
    thread_pool_executor,
    init_topological_sorter,
    write_resources_file,
)
from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    from datadog_sync.utils.configuration import Configuration


class ResourcesHandler:
    def __init__(self, config: Configuration, init_manager: bool = True) -> None:
        self.config = config

        # Additional config for resource manager
        if init_manager:
            self.resources_manager = ResourcesManager(config)
            self.resource_done_queue = deque()
            self.sorter = None

    def apply_resources(self):
        # Init executors
        parralel_executor = thread_pool_executor(self.config.max_workers)
        serial_executor = thread_pool_executor(1)
        futures = []

        # Import resources that are missing but needed for resource connections
        if self.config.force_missing_dependencies and bool(self.resources_manager.missing_resources_queue):
            self.config.logger.info("importing missing dependencies")

            seen_resource_types = set()
            while True:
                while True:
                    # consume all of the current missing dependencies
                    try:
                        q_item = self.resources_manager.missing_resources_queue.popleft()
                        seen_resource_types.add(q_item[1])
                        futures.append(parralel_executor.submit(self._force_missing_dep_import_worker, *q_item))
                    except Exception:
                        break
                # Wait for current badge of imports to finish
                wait(futures)

                # Check if queue is empty after importing all missing resources.
                # This will not be empty if the imported resources have further missing dependencies.
                if not bool(self.resources_manager.missing_resources_queue):
                    break

            futures.clear()
            # Dump seen resources
            dump_resources(self.config, seen_resource_types, SOURCE_ORIGIN)

            self.config.logger.info("finished importing missing dependencies")

        # handle resource cleanups
        if self.config.cleanup != FALSE:
            cleanup = _cleanup_prompt(self.config, self.resources_manager.all_cleanup_resources)
            if cleanup:
                for _id, resource_type in self.resources_manager.all_cleanup_resources.items():
                    futures.append(parralel_executor.submit(self._cleanup_worker, _id, resource_type))
                wait(futures)
                futures.clear()

        # Run pre-apply hooks
        for resource_type in set(self.resources_manager.all_resources.values()):
            futures.append(parralel_executor.submit(self.config.resources[resource_type].pre_apply_hook))
        wait(futures)
        for future in futures:
            try:
                future.result()
            except Exception as e:
                self.config.logger.warning(f"Error while running pre-apply hook: {str(e)}")
        futures.clear()

        # initalize topological sorters
        self.sorter = init_topological_sorter(self.resources_manager.dependencies_graph)
        # initialize queue for finished resources
        self.resource_done_queue = deque()

        while self.sorter.is_active():
            for _id in self.sorter.get_ready():
                if _id not in self.resources_manager.all_resources:
                    # at this point, we already attempted to import missing resources
                    # so mark the node as complete and continue
                    self.sorter.done(_id)
                    continue

                if self.config.resources[self.resources_manager.all_resources[_id]].resource_config.concurrent:
                    futures.append(
                        parralel_executor.submit(
                            self._apply_resource_worker, _id, self.resources_manager.all_resources[_id]
                        )
                    )
                else:
                    futures.append(
                        serial_executor.submit(
                            self._apply_resource_worker, _id, self.resources_manager.all_resources[_id]
                        )
                    )
            try:
                node = self.resource_done_queue.popleft()
                self.sorter.done(node)
            except IndexError:
                pass

        wait(futures)
        successes = errors = 0
        for future in futures:
            try:
                future.result()
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
        parralel_executor.shutdown()
        serial_executor.shutdown()

        # dump synced resources
        synced_resource_types = set(self.resources_manager.all_resources.values())
        cleanedup_resource_types = set(self.resources_manager.all_cleanup_resources.values())
        dump_resources(self.config, synced_resource_types.union(cleanedup_resource_types), DESTINATION_ORIGIN)

        return successes, errors

    def import_resources(self) -> None:
        for resource_type in self.config.resources_arg:
            self.config.logger.info("Importing %s", resource_type)
            successes, errors = self._import_resources_helper(resource_type)
            self.config.logger.info(f"Finished importing {resource_type}: {successes} successes, {errors} errors")

    def diffs(self):
        executor = thread_pool_executor(self.config.max_workers)
        futures = []
        for _id, resource_type in self.resources_manager.all_resources.items():
            futures.append(executor.submit(self._diffs_worker, _id, resource_type))

        for _id, resource_type in self.resources_manager.all_cleanup_resources.items():
            futures.append(executor.submit(self._diffs_worker, _id, resource_type, delete=True))
        wait(futures)

    def _diffs_worker(self, _id, resource_type, delete=False):
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
            r_class.pre_resource_action_hook(_id, resource)

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

    def _import_resources_helper(self, resource_type: str) -> Tuple[int, int]:
        r_class = self.config.resources[resource_type]
        r_class.resource_config.source_resources.clear()

        try:
            get_resp = r_class.get_resources(self.config.source_client)
        except Exception as e:
            self.config.logger.error(f"Error while importing resources {self.resource_type}: {str(e)}")
            return 0, 0

        futures = []
        with thread_pool_executor(self.config.max_workers) as executor:
            for r in get_resp:
                if not r_class.filter(r):
                    continue
                futures.append(executor.submit(r_class.import_resource, resource=r))

        successes = errors = 0
        for future in futures:
            try:
                future.result()
            except Exception as e:
                self.config.logger.error(f"Error while importing resource {resource_type}: {str(e)}")
                errors += 1
            else:
                successes += 1

        write_resources_file(resource_type, SOURCE_ORIGIN, r_class.resource_config.source_resources)
        return successes, errors

    def _apply_resource_worker(self, _id, resource_type):
        try:
            r_class = self.config.resources[resource_type]
            resource = self.config.resources[resource_type].resource_config.source_resources[_id]

            # Run hooks
            r_class.pre_resource_action_hook(_id, resource)
            r_class.connect_resources(_id, resource)

            if _id in r_class.resource_config.destination_resources:
                diff = check_diff(r_class.resource_config, resource, r_class.resource_config.destination_resources[_id])
                if diff:
                    self.config.logger.info(f"Running update for {resource_type} with {_id}")

                    prep_resource(r_class.resource_config, resource)
                    try:
                        r_class.update_resource(_id, resource)
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
                    r_class.create_resource(_id, resource)
                except Exception as e:
                    self.config.logger.error(
                        f"Error while creating resource {resource_type}. source ID: {_id} - Error: {str(e)}"
                    )
                    raise LoggedException(e)

                self.config.logger.info(f"finished create for {resource_type} with {_id}")

        finally:
            # always place in done queue regardless of exception thrown
            self.resource_done_queue.append(_id)

    def _force_missing_dep_import_worker(self, _id, resource_type):
        try:
            self.config.resources[resource_type].import_resource(_id=_id)
        except CustomClientHTTPError as e:
            self.config.logger.error(f"error importing {resource_type} with id {_id}: {str(e)}")
            return

        self.resources_manager.all_resources[_id] = resource_type
        self.resources_manager.dependencies_graph[_id] = self.resources_manager._resource_connections(
            _id, resource_type
        )

    def _cleanup_worker(self, _id, resource_type):
        try:
            self.config.resources[resource_type].delete_resource(_id)
            self.config.resources[resource_type].resource_config.destination_resources.pop(_id, None)
        except Exception as e:
            if e.status_code == 404:
                self.config.resources[resource_type].resource_config.destination_resources.pop(_id, None)
                return

            self.config.logger.error(
                f"Error while deleting resource {self.resource_type}. source ID: {_id} - Error: {str(e)}"
            )
            raise LoggedException(e)


def _cleanup_prompt(config, resources_to_cleanup, prompt=True):
    if config.cleanup == FORCE or not prompt:
        return True
    elif config.cleanup == TRUE:
        for _id, resource_type in resources_to_cleanup.items():
            config.logger.warning(
                f"Following resource will be deleted: \n"
                f"{pformat(config.resources[resource_type].resource_config.destination_resources[_id])}"
            )

        return confirm("Delete above resources from destination org?")
