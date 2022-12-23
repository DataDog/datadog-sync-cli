# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from click import confirm

from pprint import pformat


def import_resources(config, import_missing_deps=False):
    resources = config.resources
    if import_missing_deps:
        resources = {
            k: v for k, v in config.resources.items() if k in config.missing_deps
        }

    for resource_type, resource in resources.items():
        if not import_missing_deps and resource_type in config.missing_deps:
            continue

        config.logger.info("Importing %s", resource_type)
        successes, errors = resource.import_resources()
        config.logger.info(
            f"Finished importing {resource_type}: {successes} successes, {errors} errors"
        )


def apply_resources(config):
    force_missing_deps = config.force_missing_dependencies
    if not force_missing_deps and config.missing_deps:
        pretty_missing_deps = "\n".join(
            ["- " + resource for resource in config.missing_deps]
        )

        config.logger.warning(
            f"Ensure following dependencies are up to date as well:\n{pretty_missing_deps}\n"
            f"To auto import and sync dependent resources, use --force-missing-dependencies flag.",
        )

    if force_missing_deps:
        import_resources(config, import_missing_deps=True)

    for resource_type, resource in config.resources.items():
        if force_missing_deps or resource_type not in config.missing_deps:
            # Set resources to cleanup
            resource.resource_config.resources_to_cleanup = cleanup_helper(
                resource, config
            )

            config.logger.info("Syncing resource: {}".format(resource_type))
            successes, errors = resource.apply_resources()
            config.logger.info(
                f"Finished syncing {resource_type}: {successes} successes, {errors} errors"
            )


def check_diffs(config):
    for resource_type, resource in config.resources.items():
        if resource_type in config.missing_deps:
            continue
        # Set resources to cleanup
        resource.resource_config.resources_to_cleanup = cleanup_helper(resource, config)

        resource.check_diffs()


def cleanup_helper(resource, config, in_diff=False):
    # Cleanup resources
    resources_confirmed_to_remove = set()
    resources_to_be_removed = set(
        resource.resource_config.destination_resources.keys()
    ) - set(resource.resource_config.source_resources.keys())

    if in_diff or config.cleanup.lower() == "force":
        return list(resources_to_be_removed)
    elif config.cleanup.lower() == "true":
        for id in resources_to_be_removed:
            if confirm(
                f"{pformat(resource.resource_config.destination_resources[id])} \n"
                f"Above resource was deleted in source. Delete it in destination?"
            ):
                resources_confirmed_to_remove.add(id)

    return list(resources_confirmed_to_remove)
