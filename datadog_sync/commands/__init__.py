# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from datadog_sync.commands.sync import sync
from datadog_sync.commands._import import _import
from datadog_sync.commands.diffs import diffs
from datadog_sync.commands.migrate import migrate


ALL_COMMANDS = [
    sync,
    _import,
    diffs,
    migrate,
]
