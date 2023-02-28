# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

# Environment variables
DD_SOURCE_API_URL = "DD_SOURCE_API_URL"
DD_SOURCE_API_KEY = "DD_SOURCE_API_KEY"
DD_SOURCE_APP_KEY = "DD_SOURCE_APP_KEY"
DD_DESTINATION_API_URL = "DD_DESTINATION_API_URL"
DD_DESTINATION_API_KEY = "DD_DESTINATION_API_KEY"
DD_DESTINATION_APP_KEY = "DD_DESTINATION_APP_KEY"
DD_HTTP_CLIENT_RETRY_TIMEOUT = "DD_HTTP_CLIENT_RETRY_TIMEOUT"
DD_RESOURCES = "DD_RESOURCES"
MAX_WORKERS = "MAX_WORKERS"
DD_FILTER = "DD_FILTER"
DD_FILTER_OPERATOR = "DD_FILTER_OPERATOR"
DD_CLEANUP = "DD_CLEANUP"
DD_VALIDATE = "DD_VALIDATE"

# Default variables
DEFAULT_API_URL = "https://api.datadoghq.com"
RESOURCES_DIR = "resources/"
RESOURCE_FILE_PATH = "resources/{}/{}.json"
SOURCE_RESOURCES_DIR = "resources/source"
DESTINATION_RESOURCES_DIR = "resources/destination"

LOGGER_NAME = "datadog_sync_cli"
SOURCE_ORIGIN = "source"
DESTINATION_ORIGIN = "destination"
VALIDATE_ENDPOINT = "/api/v1/validate"

# Commands
CMD_IMPORT = "import"
CMD_SYNC = "sync"
CMD_DIFFS = "diffs"
