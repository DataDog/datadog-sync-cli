from datadog_sync.commands.sync import sync
from datadog_sync.commands._import import _import


ALL_COMMANDS = [
    sync,
    _import,
]
