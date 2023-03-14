# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
from collections import deque
from concurrent.futures import wait

from click import confirm
from pprint import pformat

from datadog_sync.utils.queue_manager import QueueManager
from datadog_sync.utils.resource_utils import thread_pool_executor


class ResourceHandler:
    def __init__(self, config) -> None:
        self.config = config
        self.queue_manager = QueueManager(config)

    def apply_resources(self):
        # Run pre-apply hook with the resources
        for resource_type in self.config.resources_arg:
            try:
                self.config.resources[resource_type].pre_apply_hook()
            except Exception as e:
                self.config.logger.error(f"Error while applying resources {resource_type}: {str(e)}")

        # Init executors
        parralel_executor = thread_pool_executor(self.config.max_workers)
        serial_executor = thread_pool_executor(1)

        futures = []
        while self.queue_manager.sorter.is_active():
            for _id in self.queue_manager.sorter.get_ready():
                if _id in self.queue_manager.missing_resources:
                    if self.config.resources[self.queue_manager.missing_resources[_id]].resource_config.concurrent:
                        futures.append(parralel_executor.submit(self._apply_resource_worker, _id, True))
                    else:
                        futures.append(serial_executor.submit(self._apply_resource_worker, _id, True))
                else:
                    if self.config.resources[self.queue_manager.all_resources[_id]].resource_config.concurrent:
                        futures.append(parralel_executor.submit(self._apply_resource_worker, _id))
                    else:
                        futures.append(serial_executor.submit(self._apply_resource_worker, _id))

        wait(futures)

        parralel_executor.shutdown
        serial_executor.shutdown

    def _apply_resource_worker(self, _id, missing=False):
        if missing:
            print("from missing", _id)
        else:
            print("not from missing", _id)
        self.queue_manager.sorter.done(_id)


def import_resources(config):
    for resource_type in config.resources_arg:
        resource = config.resources[resource_type]

        config.logger.info("Importing %s", resource_type)
        successes, errors = resource.import_resources()
        config.logger.info(f"Finished importing {resource_type}: {successes} successes, {errors} errors")


# def apply_resources(config):
#     queue_manager = QueueManager(config)

# while queue_manager.sorter.is_active():
#     for node in queue_manager.sorter.get_ready():
#         print("woring on", node)
#         queue_manager.finished_queue.append(node)

#     node = queue_manager.finished_queue.popleft()
#     queue_manager.sorter.done(node)

#     # for resource_type in config.resources_arg:
#     #     resource = config.resources[resource_type]

#     #     resource.resource_config.resources_to_cleanup = _get_resources_to_cleanup(resource_type, config)

#     #     config.logger.info("Syncing resource: {}".format(resource_type))
#     #     successes, errors = resource.apply_resources()
#     #     config.logger.info(f"Finished syncing {resource_type}: {successes} successes, {errors} errors")

# def _apply_resource_worker(queue_manager, node):


def check_diffs(config):
    for resource_type in config.resources_arg:
        resource = config.resources[resource_type]
        # Set resources to cleanup
        resource.resource_config.resources_to_cleanup = _get_resources_to_cleanup(resource_type, config, prompt=False)

        resource.check_diffs()


def _get_resources_to_cleanup(resource_type, config, prompt=True):
    resource = config.resources[resource_type]

    # Cleanup resources
    resources_confirmed_to_remove = set()
    resources_to_be_removed = set(resource.resource_config.destination_resources.keys()) - set(
        resource.resource_config.source_resources.keys()
    )

    if config.cleanup.lower() == "force":
        return list(resources_to_be_removed)
    elif config.cleanup.lower() == "true":
        if not prompt:
            return list(resources_to_be_removed)
        for id in resources_to_be_removed:
            if confirm(
                f"{pformat(resource.resource_config.destination_resources[id])} \n"
                f"Above resource was deleted in source. Delete it in destination?"
            ):
                resources_confirmed_to_remove.add(id)

    return list(resources_confirmed_to_remove)
