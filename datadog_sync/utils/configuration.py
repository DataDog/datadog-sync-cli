# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
from dataclasses import dataclass, field
import logging
import sys
import time
from typing import Any, Optional, Union, Dict, List

from datadog_sync.constants import (
    Command,
    AWS_CONFIG_PROPERTIES,
    DESTINATION_PATH_DEFAULT,
    DESTINATION_PATH_PARAM,
    FALSE,
    FORCE,
    LOCAL_STORAGE_TYPE,
    LOGGER_NAME,
    RESOURCE_PER_FILE,
    S3_STORAGE_TYPE,
    SOURCE_PATH_DEFAULT,
    SOURCE_PATH_PARAM,
    TRUE,
    VALIDATE_ENDPOINT,
    VALID_DDR_STATES,
)
from datadog_sync import models
from datadog_sync.model.logs_pipelines import LogsPipelines
from datadog_sync.model.logs_custom_pipelines import LogsCustomPipelines
from datadog_sync.model.downtimes import Downtimes
from datadog_sync.model.downtime_schedules import DowntimeSchedules
from datadog_sync.utils.custom_client import CustomClient
from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.utils.log import Log
from datadog_sync.utils.filter import Filter, process_filters
from datadog_sync.utils.resource_utils import CustomClientHTTPError
from datadog_sync.utils.state import State
from datadog_sync.utils.storage.storage_types import StorageType


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
    create_global_downtime: bool
    validate: bool
    send_metrics: bool
    state: State
    verify_ddr_status: bool
    backup_before_reset: bool
    show_progress_bar: bool
    allow_self_lockout: bool
    allow_partial_permissions_roles: List[str] = field(default_factory=list)
    resources: Dict[str, BaseResource] = field(default_factory=dict)
    resources_arg: List[str] = field(default_factory=list)

    async def init_async(self, cmd: Command):
        await self.source_client._init_session()
        await self.destination_client._init_session()
        for resource in self.resources.values():
            await resource.init_async()

        # Validate the clients.
        if self.validate:
            if cmd in [Command.SYNC, Command.DIFFS, Command.MIGRATE, Command.RESET]:
                try:
                    await _validate_client(self.destination_client)
                except Exception:
                    sys.exit(1)
            if cmd in [Command.IMPORT, Command.MIGRATE, Command.RESET]:
                try:
                    await _validate_client(self.source_client)
                except Exception:
                    sys.exit(1)
            self.logger.info("clients validated successfully")

        # Don't sync if DDR is active
        if self.verify_ddr_status:
            if cmd in [Command.SYNC, Command.DIFFS, Command.MIGRATE, Command.RESET]:
                try:
                    await _verify_ddr_status(self.destination_client)
                except Exception as err:
                    self.logger.error(
                        f"The destination DDR verification failed. {err} Use the --verify-ddr-status flag to override."
                    )
                    sys.exit(1)
            if cmd in [Command.IMPORT, Command.DIFFS, Command.MIGRATE]:
                try:
                    await _verify_ddr_status(self.source_client)
                except Exception as err:
                    self.logger.error(
                        f"The source DDR verification failed. {err} Use the --verify-ddr-status flag to override."
                    )
                    sys.exit(1)
            self.logger.info("DDR verified successfully")
        else:
            self.logger.warning("DDR verification skipped.")

        if cmd in [Command.IMPORT] and self.send_metrics:
            await self.source_client.send_metric(f"{cmd.value}.start")
            try:
                await self.destination_client.send_metric(f"{cmd.value}.start")
            except Exception:
                self.logger.info("Optional destination not defined")
        if cmd in [Command.SYNC, Command.RESET] and self.send_metrics:
            await self.destination_client.send_metric(f"{cmd.value}.start")
            try:
                await self.source_client.send_metric(f"{cmd.value}.start")
            except Exception:
                self.logger.info("Optional source not defined")
        if cmd in [Command.MIGRATE] and self.send_metrics:
            await self.source_client.send_metric(f"{cmd.value}.start")
            await self.destination_client.send_metric(f"{cmd.value}.start")

    async def exit_async(self):
        await self.source_client._end_session()
        await self.destination_client._end_session()


def build_config(cmd: Command, **kwargs: Optional[Any]) -> Configuration:
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
    send_metrics = kwargs.get("send_metrics")
    verify_ssl = kwargs.get("verify_ssl_certificates", True)

    source_auth = {}
    if k := kwargs.get("source_api_key"):
        source_auth["apiKeyAuth"] = k
    if k := kwargs.get("source_app_key"):
        source_auth["appKeyAuth"] = k
    source_client = CustomClient(source_api_url, source_auth, retry_timeout, timeout, send_metrics, verify_ssl)

    destination_auth = {}
    if k := kwargs.get("destination_api_key"):
        destination_auth["apiKeyAuth"] = k
    if k := kwargs.get("destination_app_key"):
        destination_auth["appKeyAuth"] = k
    destination_client = CustomClient(
        destination_api_url,
        destination_auth,
        retry_timeout,
        timeout,
        send_metrics,
        verify_ssl,
    )

    # Additional settings
    force_missing_dependencies = kwargs.get("force_missing_dependencies")
    skip_failed_resource_connections = kwargs.get("skip_failed_resource_connections")
    max_workers = kwargs.get("max_workers")
    create_global_downtime = kwargs.get("create_global_downtime")
    validate = kwargs.get("validate")
    verify_ddr_status = kwargs.get("verify_ddr_status")
    backup_before_reset = not kwargs.get("do_not_backup")
    show_progress_bar = kwargs.get("show_progress_bar")
    allow_self_lockout = kwargs.get("allow_self_lockout", False)

    # Parse allow_partial_permissions_roles
    allow_partial_permissions_roles = []
    if allow_partial_str := kwargs.get("allow_partial_permissions_roles"):
        allow_partial_permissions_roles = [p.strip() for p in allow_partial_str.split(",")]

    cleanup = kwargs.get("cleanup")
    if cleanup:
        cleanup = {
            "true": TRUE,
            "false": FALSE,
            "force": FORCE,
        }[cleanup.lower()]

    # determine where the states are stored
    storage_type = kwargs.get("storage_type", "local").lower()
    config = {}

    if storage_type == S3_STORAGE_TYPE:
        logger.info("Using AWS S3 to store state files")
        storage_type = StorageType.AWS_S3_BUCKET

        local_source_resources_path = kwargs.get(SOURCE_PATH_PARAM, SOURCE_PATH_DEFAULT)
        source_resources_path = kwargs.get("aws_bucket_key_prefix_source", local_source_resources_path)

        local_destination_resources_path = kwargs.get(DESTINATION_PATH_PARAM, DESTINATION_PATH_DEFAULT)
        destination_resources_path = kwargs.get("aws_bucket_key_prefix_destination", local_destination_resources_path)

        for aws_config_property in AWS_CONFIG_PROPERTIES:
            property_value = kwargs.get(aws_config_property, None)
            if not property_value:
                logger.warning(f"Missing AWS configuration parameter: {aws_config_property}")
            config[aws_config_property] = property_value
    elif storage_type == LOCAL_STORAGE_TYPE:
        logger.info("Using local filesystem to store state files")
        storage_type = StorageType.LOCAL_FILE
        source_resources_path = kwargs.get(SOURCE_PATH_PARAM, SOURCE_PATH_DEFAULT)
        destination_resources_path = kwargs.get(DESTINATION_PATH_PARAM, DESTINATION_PATH_DEFAULT)
    else:
        raise ValueError("Unsupported storage type")

    # Confusing, but the source for the import needs to be the destination of the reset
    # If a destination is going to be reset then a backup needs to be preformed. A back up
    # is just an import, the source of that import is the destination of the reset.
    if cmd == Command.RESET:
        cleanup = TRUE
        source_client = CustomClient(
            destination_api_url,
            destination_auth,
            retry_timeout,
            timeout,
            send_metrics,
            verify_ssl,
        )
        source_resources_path = f"{destination_resources_path}/.backup/{str(time.time())}"

    resource_per_file = kwargs.get(RESOURCE_PER_FILE, False)
    # Initialize state
    state = State(
        type_=storage_type,
        source_resources_path=source_resources_path,
        destination_resources_path=destination_resources_path,
        config=config,
        resource_per_file=resource_per_file,
    )

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
        create_global_downtime=create_global_downtime,
        validate=validate,
        send_metrics=send_metrics,
        state=state,
        verify_ddr_status=verify_ddr_status,
        backup_before_reset=backup_before_reset,
        show_progress_bar=show_progress_bar,
        allow_self_lockout=allow_self_lockout,
        allow_partial_permissions_roles=allow_partial_permissions_roles,
    )

    # Initialize resource classes
    resources = init_resources(config)
    resources_arg_str = kwargs.get("resources", None)
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
            sys.exit(1)

        resources_arg = list(set(resources_arg) & set(resources.keys()))
    else:
        resources_arg = list(resources.keys())

    config.resources = resources
    config.resources_arg = resources_arg

    _handle_deprecated(config, resources_arg_str is not None)

    return config


def init_resources(cfg: Configuration) -> Dict[str, BaseResource]:
    """Returns dict of initialized resources"""

    resources = dict(
        (cls.resource_type, cls(cfg))
        for cls in models.__dict__.values()
        if isinstance(cls, type) and issubclass(cls, BaseResource)
    )

    return resources


async def _verify_ddr_status(client: CustomClient) -> None:
    ddr_state = await client.get_ddr_status()
    if not ddr_state:
        raise ConnectionError("This indicates that no DDR status could be retrieved.")

    if ddr_state not in VALID_DDR_STATES:
        raise ValueError(f"This indicates disaster recovery is in progress. DDR status retrieved: {ddr_state.name}")


async def _validate_client(client: CustomClient) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    try:
        await client.get(VALIDATE_ENDPOINT)
    except CustomClientHTTPError as e:
        logger.error(f"invalid api key: {e}")
        raise e
    except Exception as e:
        logger.error(f"error while validating api key: {e}")
        raise e


def _handle_deprecated(config: Configuration, resources_arg_passed: bool):
    if resources_arg_passed:
        if LogsCustomPipelines.resource_type in config.resources_arg:
            config.logger.warning(
                "`logs_custom_pipelines` resource has been deprecated in favor of `logs_pipelines`"
                + "Consider upgrading by renaming existing state files."
                + "`logs_custom_pipelines.json` -> `logs_pipelines.json` and using resource type"
                + "`logs_pipelines`."
            )
        if (
            LogsCustomPipelines.resource_type in config.resources_arg
            and LogsPipelines.resource_type in config.resources_arg
        ):
            config.logger.error(
                "`logs_custom_pipelines` and `logs_pipelines` resource should not"
                + " be used together as it will cause duplication."
            )
            sys.exit(1)

        if Downtimes.resource_type in config.resources_arg:
            config.logger.warning("`downtimes` resource has been deprecated in favor of `downtime_schedules`.")
        if Downtimes.resource_type in config.resources_arg and DowntimeSchedules.resource_type in config.resources_arg:
            config.logger.error(
                "`downtimes` and `downtime_schedules` resource should not"
                + " be used together as it will cause duplication."
            )
            sys.exit(1)

    else:
        # Use logs_custom_pipeline resource if its state files exist.
        # Otherwise fall back on logs_pipelines
        if (
            config.state.source[LogsCustomPipelines.resource_type]
            or config.state.destination[LogsCustomPipelines.resource_type]
        ):
            config.logger.warning(
                "`logs_custom_pipelines` resource has been deprecated in favor of `logs_pipelines`. "
                + "Consider upgrading by renaming existing state files"
                + "`logs_custom_pipelines.json` -> `logs_pipelines.json`"
            )
            config.resources_arg.remove(LogsPipelines.resource_type)
        else:
            config.resources_arg.remove(LogsCustomPipelines.resource_type)

        # Use downtimes resource if its state files exist.
        # Otherwise fall back on downtime_schedules
        if config.state.source[Downtimes.resource_type] or config.state.destination[Downtimes.resource_type]:
            config.logger.warning(
                "`downtimes` resource has been deprecated in favor of `downtime_schedules`. "
                + "Consider upgrading by removing the existing state files"
                + "`downtimes.json` from source and destination directory."
            )
            config.resources_arg.remove(DowntimeSchedules.resource_type)
        else:
            config.resources_arg.remove(Downtimes.resource_type)
