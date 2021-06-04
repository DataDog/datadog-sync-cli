from datadog_sync.commands.sync import sync
from datadog_sync.commands._import import _import
from datadog_sync.commands.diff import diffs


ALL_COMMANDS = [
    sync,
    _import,
    diffs,
]
