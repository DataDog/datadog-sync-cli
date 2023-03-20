import os
import platform
import argparse

import PyInstaller.__main__
from github import Github

from datadog_sync._version import __version__

REPO = "datadog/datadog-sync-cli"
PACKAGE_NAME = "datadog-sync-cli"
ENTRY_POINT = "datadog_sync/cli.py"
DEFAULT_NAME = f"{PACKAGE_NAME}-{platform.system().lower()}-{platform.machine().lower()}"
OUTPUT_DIR = "dist"
GITHUB_ASSET_UPLOAD = ""

parser = argparse.ArgumentParser(description="Build binary")
parser.add_argument("--onefile", "-f", action="store_true", help="Create a one-file bundled executable.")
parser.add_argument("--file-name", "-n", help="App name.", default=DEFAULT_NAME)
parser.add_argument("--output", "-o", help="Binary output directory.", default=OUTPUT_DIR)
parser.add_argument("--upload", "-u", action="store_true", help="Upload artifact to latest release")

args = parser.parse_args()

# Build args
cmd_args = [ENTRY_POINT]
cmd_args.extend(["--name", args.file_name])
cmd_args.extend(["--distpath", args.output])
if args.onefile:
    cmd_args.append("--onefile")

PyInstaller.__main__.run(
    cmd_args,
)

if args.upload:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise Exception("github token is required to upload")
    g = Github(token)
    repo = g.get_repo(REPO)
    latest_release = repo.get_latest_release()

    latest_release.upload_asset(path=f"{OUTPUT_DIR}/{DEFAULT_NAME}")