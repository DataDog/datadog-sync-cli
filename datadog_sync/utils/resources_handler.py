# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from click import confirm

from pprint import pformat

from datadog_sync.utils.queue_manager import OrderManager


def import_resources(config):
    for resource_type in config.resources_arg:
        resource = config.resources[resource_type]

        config.logger.info("Importing %s", resource_type)
        successes, errors = resource.import_resources()
        config.logger.info(f"Finished importing {resource_type}: {successes} successes, {errors} errors")


def apply_resources(config):
    for resource_type in config.resources_arg:
        resource = config.resources[resource_type]

        resource.resource_config.resources_to_cleanup = _get_resources_to_cleanup(resource_type, config)

        config.logger.info("Syncing resource: {}".format(resource_type))
        successes, errors = resource.apply_resources()
        config.logger.info(f"Finished syncing {resource_type}: {successes} successes, {errors} errors")


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
