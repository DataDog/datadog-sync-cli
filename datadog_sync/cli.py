from click import group

from datadog_sync.commands import ALL_COMMANDS


@group()
def cli():
    """Initialize cli"""
    pass


# Register all click sub-commands
for command in ALL_COMMANDS:
    cli.add_command(command)
