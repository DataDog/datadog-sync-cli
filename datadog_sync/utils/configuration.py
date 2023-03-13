# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import logging
from collections import defaultdict, OrderedDict
from dataclasses import dataclass
from typing import (
    Type,
    Any,
    Union,
    Dict,
    Tuple,
    DefaultDict,
    List,
    Optional,
)

from datadog_sync import models
from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.utils.log import Log
from datadog_sync.utils.filter import Filter, process_filters
from datadog_sync.constants import CMD_DIFFS, CMD_IMPORT, CMD_SYNC, LOGGER_NAME, VALIDATE_ENDPOINT
from datadog_sync.utils.resource_utils import CustomClientHTTPError


@dataclass
class Configuration(object):
    logger: Union[Log, logging.Logger, None] = None
    source_client: Optional[CustomClient] = None
    destination_client: Optional[CustomClient] = None
    resources: Optional[List[str]] = None
    initialized_resources: Optional[Dict[str, BaseResource]] = None
    filters: Optional[Dict[str, Filter]] = None
    filter_operator: Optional[str] = None
    force_missing_dependencies: Optional[bool] = None
    skip_failed_resource_connections: Optional[bool] = None
    max_workers: Optional[int] = None
    cleanup: Optional[str] = None


def build_config(cmd, **kwargs: Any) -> Configuration:
    # configure logger
    logger = Log(kwargs.get("verbose"))

    # configure Filter
    filters = process_filters(kwargs.get("filter"))
    filter_operator = kwargs.get("filter_operator")

    source_api_url = kwargs.get("source_api_url")
    destination_api_url = kwargs.get("destination_api_url")

    # Initialize the datadog API Clients based on cmd
    retry_timeout = kwargs.get("http_client_retry_timeout")
    source_auth = {
        "apiKeyAuth": kwargs.get("source_api_key", ""),
        "appKeyAuth": kwargs.get("source_app_key", ""),
    }
    source_client = CustomClient(source_api_url, source_auth, retry_timeout)

    destination_auth = {
        "apiKeyAuth": kwargs.get("destination_api_key", ""),
        "appKeyAuth": kwargs.get("destination_app_key", ""),
    }
    destination_client = CustomClient(destination_api_url, destination_auth, retry_timeout)

    # Validate the clients. For import we only validate the source client
    # For sync/diffs we validate the destination client.
    validate = kwargs.get("validate")
    if validate:
        if cmd in [CMD_SYNC, CMD_DIFFS]:
            _validate_client(destination_client)
        if cmd == CMD_IMPORT:
            _validate_client(source_client)
        logger.info("clients validated successfully")

    # Additional settings
    force_missing_dependencies = kwargs.get("force_missing_dependencies")
    skip_failed_resource_connections = kwargs.get("skip_failed_resource_connections")
    max_workers = kwargs.get("max_workers", 10)
    cleanup = kwargs.get("cleanup")
    
    # Initialize Configuration
    config = Configuration(
        logger=logger,
        source_client=source_client,
        destination_client=destination_client,
        filters=filters,
        filter_operator=filter_operator,
        force_missing_dependencies=force_missing_dependencies,
        skip_failed_resource_connections=skip_failed_resource_connections,
        max_workers=max_workers,
        cleanup=cleanup,
    )
    
    # Initialize resources
    initialized_resources = init_resources(config)
    resources_arg = kwargs.get("resources", "")
    if resources_arg:
        resources = resources_arg.lower().split(",")
        unknown_resources = list(set(resources) - set(initialized_resources.keys()))
        if unknown_resources:
            logger.warning("invalid resources. Skipping: %s", unknown_resources)
    else:
        resources = list(initialized_resources.keys())
    
    config.resources = resources
    config.initialized_resources = initialized_resources

    return config


# TODO: add unit tests
def init_resources(cfg: Configuration) -> Dict[str, BaseResource]:
    """Returns dict of initialized resources"""

    initialized_resources = dict(
        (cls.resource_type, cls(cfg))
        for cls in models.__dict__.values()
        if isinstance(cls, type) and issubclass(cls, BaseResource)
    )
    
    return initialized_resources


def _validate_client(client: CustomClient):
    try:
        client.get(VALIDATE_ENDPOINT).json()
    except CustomClientHTTPError as e:
        logger = logging.getLogger(LOGGER_NAME)
        logger.error(f"invalid api key: {e}")
        exit(1)
