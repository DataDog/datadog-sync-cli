import logging

from datadog_sync.constants import LOGGER_NAME


class Configuration(object):
    def __init__(
        self,
        logger=None,
        source_client=None,
        destination_client=None,
        resources=None,
        missing_deps=None,
        filters=None,
        skip_failed_resource_connections=None,
    ):
        if not logger:
            # fallback to default logger if not provided
            logger = logging.getLogger(LOGGER_NAME)
        self.logger = logger
        self.source_client = source_client
        self.destination_client = destination_client
        self.resources = resources
        self.missing_deps = missing_deps
        self.filters = filters
        self.skip_failed_resource_connections = skip_failed_resource_connections
