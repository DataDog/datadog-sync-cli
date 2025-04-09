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

LOCAL_STORAGE_TYPE = "local"
S3_STORAGE_TYPE = "s3"
STORAGE_TYPES = [
    LOCAL_STORAGE_TYPE,
    S3_STORAGE_TYPE,
]

DD_DESTINATION_RESOURCES_PATH = "DD_DESTINATION_RESOURCES_PATH"
DD_SOURCE_RESOURCES_PATH = "DD_SOURCE_RESOURCES_PATH"

# S3 env parameter names
AWS_ACCESS_KEY_ID = "AWS_ACCESS_KEY_ID"
AWS_BUCKET_NAME = "AWS_BUCKET_NAME"
AWS_BUCKET_KEY_PREFIX_SOURCE = "AWS_BUCKET_KEY_PREFIX_SOURCE"
AWS_BUCKET_KEY_PREFIX_DESTINATION = "AWS_BUCKET_KEY_PREFIX_DESTINATION"
AWS_REGION_NAME = "AWS_REGION_NAME"
AWS_SECRET_ACCESS_KEY = "AWS_SECRET_ACCESS_KEY"
AWS_SESSION_TOKEN = "AWS_SESSION_TOKEN"
AWS_CONFIG_PROPERTIES = [
    "aws_bucket_name",
    "aws_region_name",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
]

# Default variables
DEFAULT_API_URL = "https://api.datadoghq.com"

LOGGER_NAME = "datadog_sync_cli"
VALIDATE_ENDPOINT = "/api/v1/validate"

# Bool constants
FALSE = 0
TRUE = 1
FORCE = 2

# State parameters
SOURCE_PATH_PARAM = "source_resources_path"
SOURCE_PATH_DEFAULT = "resources/source"
DESTINATION_PATH_PARAM = "destination_resources_path"
DESTINATION_PATH_DEFAULT = "resources/destination"
RESOURCE_PER_FILE = "resource_per_file"


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
