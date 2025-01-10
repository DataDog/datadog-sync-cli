# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from enum import Enum


class StorageType(Enum):
    LOCAL_FILE = 1
    AWS_S3_BUCKET = 2
