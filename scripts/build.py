import platform
import argparse

import PyInstaller.__main__

from datadog_sync._version import __version__

PACKAGE_NAME="datadog-sync-cli"
ENTRY_POINT="datadog_sync/cli.py"
DEFAULT_NAME=f"{PACKAGE_NAME}-{platform.system()}-{platform.machine()}"
OUTPUT_DIR="dist"

parser = argparse.ArgumentParser(
    description="Build binary"
)
parser.add_argument("--onefile", "-f", action="store_true", help="Create a one-file bundled executable.")
parser.add_argument("--file-name", "-n", help="App name.", default=DEFAULT_NAME)
parser.add_argument("--output", "-o", help="Binary output directory.", default=OUTPUT_DIR)
args = parser.parse_args()

# Build args
cmd_args=[ENTRY_POINT]
cmd_args.extend(["--name", args.file_name])
cmd_args.extend(["--distpath", args.output])
if args.onefile:
    cmd_args.append("--onefile")

PyInstaller.__main__.run(
    cmd_args,
)
