from datadog_sync.commands.sync import sync
from datadog_sync.commands._import import _import
from datadog_sync.commands.diff import diff


ALL_COMMANDS = [
    sync,
    _import,
    diff,
]
