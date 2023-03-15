# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from collections import deque
from concurrent.futures import wait
from copy import deepcopy

from click import confirm
from pprint import pformat

from datadog_sync.constants import DESTINATION_ORIGIN, SOURCE_ORIGIN
from datadog_sync.utils.graph_manager import GraphManager, init_topological_sorter
from datadog_sync.utils.resource_utils import (
    CustomClientHTTPError,
    LoggedException,
    ResourceConnectionError,
    check_diff,
    dump_resource_files,
    prep_resource,
    thread_pool_executor,
)


class ResourceHandler:
    def __init__(self, config) -> None:
        self.config = config
        self.graph_manager = GraphManager(config)
        self.sorter = None

    def apply_resources(self):
        # Init executors
        parralel_executor = thread_pool_executor(self.config.max_workers)
        serial_executor = thread_pool_executor(1)
        futures = []

        # Import resources that are missing but needed for resource connections
        if self.config.force_missing_dependencies and bool(self.graph_manager.missing_resources):
            self.config.logger.info("importing missing dependencies")

            seen_resource_types = set()
            while True:
                while True:
                    # consume all of the current missing dependencies
                    try:
                        q_item = self.graph_manager.missing_resources.popleft()
                        seen_resource_types.add(q_item[1])
                        futures.append(parralel_executor.submit(self._force_missing_dep_import_worker, *q_item))
                    except Exception:
                        break
                # Wait for current badge of imports to finish
                wait(futures)

                # Check if queue is empty after importing all missing resources.
                # This will not be empty if the imported resources have further missing dependencies.
                if not bool(self.graph_manager.missing_resources):
                    break

            futures.clear()
            # Dump seen resources
            dump_resource_files(self.config, seen_resource_types, SOURCE_ORIGIN)

            self.config.logger.info("finished importing missing dependencies")

        # handle resource cleanups
        if self.config.cleanup.lower() != "false":
            cleanup = _cleanup_prompt(self.config, self.graph_manager.all_cleanup_resource)
            if cleanup:
                for _id, resource_type in self.graph_manager.all_cleanup_resource.items():
                    futures.append(parralel_executor.submit(self._cleanup_worker, _id, resource_type))
                wait(futures)
                futures.clear()

        # Run pre-apply hooks
        for resource_type in set(self.graph_manager.all_resources.values()):
            futures.append(parralel_executor.submit(self.config.resources[resource_type].pre_apply_hook))
        wait(futures)
        for future in futures:
            try:
                future.result()
            except Exception as e:
                self.config.logger.warning(f"Error while running pre-apply hook: {str(e)}")
        futures.clear()

        # initalize topological sorters
        self.sorter = init_topological_sorter(self.graph_manager.dependencies_graph)

        while self.sorter.is_active():
            for _id in self.sorter.get_ready():
                if _id not in self.graph_manager.all_resources:
                    # at this point, we already attempted to import missing resources
                    # mark the node as complete and continue
                    self.sorter.done(_id)
                    continue

                if self.config.resources[self.graph_manager.all_resources[_id]].resource_config.concurrent:
                    futures.append(
                        parralel_executor.submit(
                            self._apply_resource_worker, _id, self.graph_manager.all_resources[_id]
                        )
                    )
                else:
                    futures.append(
                        serial_executor.submit(self._apply_resource_worker, _id, self.graph_manager.all_resources[_id])
                    )

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
        synced_resource_types = set(self.graph_manager.all_resources.values())
        cleanedup_resource_types = set(self.graph_manager.all_cleanup_resource.values())
        dump_resource_files(self.config, synced_resource_types.union(cleanedup_resource_types), DESTINATION_ORIGIN)

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
            # always mark the node as done regardless of exception thrown
            self.sorter.done(_id)

    def _force_missing_dep_import_worker(self, _id, resource_type):
        try:
            self.config.resources[resource_type].import_resource(_id=_id)
        except CustomClientHTTPError as e:
            self.config.logger.error(f"error importing {resource_type} with id {_id}: {str(e)}")
            return

        self.graph_manager.all_resources[_id] = resource_type
        self.graph_manager.dependencies_graph[_id] = self.graph_manager._resource_connections(_id, resource_type)

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


def import_resources(config):
    for resource_type in config.resources_arg:
        resource = config.resources[resource_type]

        config.logger.info("Importing %s", resource_type)
        successes, errors = resource.import_resources()
        config.logger.info(f"Finished importing {resource_type}: {successes} successes, {errors} errors")


def check_diffs(config):
    for resource_type in config.resources_arg:
        resource = config.resources[resource_type]
        # # Set resources to cleanup
        # resource.resource_config.resources_to_cleanup = _get_resources_to_cleanup(resource_type, config, prompt=False)

        resource.check_diffs()


def _cleanup_prompt(config, resources_to_cleanup, prompt=True):
    if config.cleanup.lower() == "force" or not prompt:
        return True
    elif config.cleanup.lower() == "true":
        for _id, resource_type in resources_to_cleanup.items():
            config.logger.warning(
                f"Following resource will be deleted: \n"
                f"{pformat(config.resources[resource_type].resource_config.destination_resources[_id])}"
            )

        return confirm("Delete above resources from destination org?")
