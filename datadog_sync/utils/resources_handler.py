def import_resources(config, import_missing_deps=False):
    resources = config.resources
    if import_missing_deps:
        resources = {k: v for k, v in config.resources.items() if k in config.missing_deps}

    for resource_type, resource in resources.items():
        if resource_type in config.missing_deps:
            continue

        resource.import_resources()


def apply_resources(config):
    force_missing_deps = config.force_missing_dependencies
    if not force_missing_deps and config.missing_deps:
        pretty_missing_deps = "\n".join(["- " + resource for resource in config.missing_deps])

        config.logger.warning(
            f"Ensure following dependencies are up to date as well:\n{pretty_missing_deps}\n"
            f"To auto import and sync dependent resources, use --force-missing-dependencies flag.",
        )

    if force_missing_deps:
        import_resources(config, import_missing_deps=True)

    for resource_type, resource in config.resources.items():
        # sync resource
        if force_missing_deps or resource_type not in config.missing_deps:
            resource.apply_resources()


def check_diffs(config):
    for resource_type, resource in config.resources.items():
        if resource_type in config.missing_deps:
            continue

        resource.check_diffs()
