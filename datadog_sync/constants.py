# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from enum import Enum

# Environment variables
DD_SOURCE_API_URL = "DD_SOURCE_API_URL"
DD_SOURCE_API_KEY = "DD_SOURCE_API_KEY"
DD_SOURCE_APP_KEY = "DD_SOURCE_APP_KEY"
DD_DESTINATION_API_URL = "DD_DESTINATION_API_URL"
DD_DESTINATION_API_KEY = "DD_DESTINATION_API_KEY"
DD_DESTINATION_APP_KEY = "DD_DESTINATION_APP_KEY"
DD_HTTP_CLIENT_RETRY_TIMEOUT = "DD_HTTP_CLIENT_RETRY_TIMEOUT"
DD_HTTP_CLIENT_TIMEOUT = "DD_HTTP_CLIENT_TIMEOUT"
DD_RESOURCES = "DD_RESOURCES"
MAX_WORKERS = "MAX_WORKERS"
DD_FILTER = "DD_FILTER"
DD_FILTER_OPERATOR = "DD_FILTER_OPERATOR"
DD_CLEANUP = "DD_CLEANUP"
DD_VALIDATE = "DD_VALIDATE"
DD_VERIFY_DDR_STATUS = "DD_VERIFY_DDR_STATUS"

# Default variables
DEFAULT_API_URL = "https://api.datadoghq.com"

LOGGER_NAME = "datadog_sync_cli"
VALIDATE_ENDPOINT = "/api/v1/validate"

# Bool constants
FALSE = 0
TRUE = 1
FORCE = 2


# Commands
class Command(Enum):
    IMPORT = "import"
    SYNC = "sync"
    DIFFS = "diffs"
    MIGRATE = "migrate"
    RESET = "reset"


# Origin
class Origin(Enum):
    ALL = "all"
    SOURCE = "source"
    DESTINATION = "destination"


class Metrics(Enum):
    PREFIX = "datadog.org-sync"
    ACTION = "action"
    ORIGIN_PRODUCT = 24


# Status
class Status(Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILURE = "failure"


# DDR Status
class DDR_Status(Enum):
    ONBOARDING = 1
    PASSIVE = 2
    FAILOVER = 3
    ACTIVE = 4
    RECOVERY = 5


VALID_DDR_STATES = [
    DDR_Status.ONBOARDING,
    DDR_Status.PASSIVE,
    DDR_Status.RECOVERY,
]
