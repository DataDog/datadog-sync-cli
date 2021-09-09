import os

from datadog_sync.constants import DESTINATION_ORIGIN, SOURCE_ORIGIN, SOURCE_RESOURCES_DIR, DESTINATION_RESOURCES_DIR
from datadog_sync.utils.resource_utils import write_resources_file


def handle_interrupt(func, dump=True):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except KeyboardInterrupt:
            cfg = args[0].obj["config"]
            if cfg.current_executor:
                cfg.current_executor.shutdown(wait=True, cancel_futures=True)
            if dump:
                for resource_type, resource in cfg.resources.items():
                    # Ensure directories exist.
                    os.makedirs(SOURCE_RESOURCES_DIR, exist_ok=True)
                    os.makedirs(DESTINATION_RESOURCES_DIR, exist_ok=True)

                    # Dump all resources
                    write_resources_file(resource_type, SOURCE_ORIGIN, resource.resource_config.source_resources)
                    write_resources_file(
                        resource_type, DESTINATION_ORIGIN, resource.resource_config.destination_resources
                    )

    return wrapper
