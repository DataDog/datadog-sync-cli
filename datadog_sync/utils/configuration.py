# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import os
import logging
from sys import exit
from dataclasses import dataclass, field
from typing import Any, Optional, Union, Dict, List

from datadog_sync import models
from datadog_sync.model.logs_pipelines import LogsPipelines
from datadog_sync.model.logs_custom_pipelines import LogsCustomPipelines
from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.utils.log import Log
from datadog_sync.utils.filter import Filter, process_filters
from datadog_sync.constants import (
    CMD_DIFFS,
    CMD_IMPORT,
    CMD_SYNC,
    FALSE,
    FORCE,
    LOGGER_NAME,
    RESOURCE_FILE_PATH,
    TRUE,
    VALIDATE_ENDPOINT,
    VALIDATE_ENDPOINT_COOKIEAUTH,
)
from datadog_sync.utils.resource_utils import CustomClientHTTPError


@dataclass
class Configuration(object):
    logger: Union[Log, logging.Logger]
    source_client: CustomClient
    destination_client: CustomClient
    filters: Dict[str, List[Filter]]
    filter_operator: str
    force_missing_dependencies: bool
    skip_failed_resource_connections: bool
    max_workers: int
    cleanup: int
    resources: Dict[str, BaseResource] = field(default_factory=dict)
    resources_arg: List[str] = field(default_factory=list)


def build_config(cmd: str, **kwargs: Optional[Any]) -> Configuration:
    # configure logger
    logger = Log(kwargs.get("verbose"))

    # configure Filter
    filters = process_filters(kwargs.get("filter"))
    filter_operator = kwargs.get("filter_operator")

    source_api_url = kwargs.get("source_api_url")
    destination_api_url = kwargs.get("destination_api_url")

    # Initialize the datadog API Clients based on cmd
    retry_timeout = kwargs.get("http_client_retry_timeout")
    timeout = kwargs.get("http_client_timeout")
    source_auth = {
        "apiKeyAuth": kwargs.get("source_api_key", ""),
        "appKeyAuth": kwargs.get("source_app_key", ""),
        "cookieDogWeb": kwargs.get("source_cookie_dogweb", ""),
    }
    source_client = CustomClient(source_api_url, source_auth, retry_timeout, timeout)

    destination_auth = {
        "apiKeyAuth": kwargs.get("destination_api_key", ""),
        "appKeyAuth": kwargs.get("destination_app_key", ""),
        "cookieDogWeb": kwargs.get("destination_cookie_dogweb", ""),
        "x-csrf-token": kwargs.get("destination_csrf_token", ""),
    }
    destination_client = CustomClient(destination_api_url, destination_auth, retry_timeout, timeout)

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
    if cleanup:
        cleanup = {
            "true": TRUE,
            "false": FALSE,
            "force": FORCE,
        }[cleanup.lower()]

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
    resources = init_resources(config)
    resources_arg_str = kwargs.get("resources")
    if resources_arg_str:
        resources_arg = resources_arg_str.lower().split(",")
        unknown_resources = list(set(resources_arg) - set(resources.keys()))

        if unknown_resources:
            logger.warning("invalid resources. Discarding: %s", unknown_resources)
        if LogsCustomPipelines.resource_type in resources_arg:
            logger.warning(
                "`logs_custom_pipelines` resource has been deprecated in favor of `logs_pipelines`. "
                + "Consider upgrading by renaming existing state files"
                + "`logs_custom_pipelines.json` -> `logs_pipelines.json` and using resource type"
                + "`logs_pipelines`"
            )

        if LogsCustomPipelines.resource_type in resources_arg and LogsPipelines.resource_type in resources_arg:
            logger.error(
                "`logs_custom_pipelines` and `logs_pipelines` resource should not"
                + " be used together as it will cause duplication"
            )
            exit(1)

        resources_arg = list(set(resources_arg) & set(resources.keys()))
    else:
        resources_arg = list(resources.keys())

        # Use logs_custom_pipeline resource if its state files exist.
        # Otherwise fall back on logs_pipelines
        custom_pipeline_source = RESOURCE_FILE_PATH.format("source", LogsCustomPipelines.resource_type)
        custom_pipeline_destination = RESOURCE_FILE_PATH.format("destination", LogsCustomPipelines.resource_type)
        if os.path.exists(custom_pipeline_source) or os.path.exists(custom_pipeline_destination):
            logger.warning(
                "`logs_custom_pipelines` resource has been deprecated in favor of `logs_pipelines`. "
                + "Consider upgrading by renaming existing state files"
                + "`logs_custom_pipelines.json` -> `logs_pipelines.json`"
            )
            resources_arg.remove(LogsPipelines.resource_type)
        else:
            resources_arg.remove(LogsCustomPipelines.resource_type)

    config.resources = resources
    config.resources_arg = resources_arg

    return config


def init_resources(cfg: Configuration) -> Dict[str, BaseResource]:
    """Returns dict of initialized resources"""

    resources = dict(
        (cls.resource_type, cls(cfg))
        for cls in models.__dict__.values()
        if isinstance(cls, type) and issubclass(cls, BaseResource)
    )

    return resources


def _validate_client(client: CustomClient) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    try:
        if client.cookieauth:
            client.get(VALIDATE_ENDPOINT_COOKIEAUTH).json()
        else:
            client.get(VALIDATE_ENDPOINT).json()
    except CustomClientHTTPError as e:
        logger.error(f"invalid api key: {e}")
        exit(1)
    except Exception as e:
        logger.error(f"error while validating api key: {e}")
        exit(1)
