import os
import subprocess
import logging
from shutil import copyfile
from distutils.dir_util import copy_tree


from datadog_sync.constants import (
    DEFAULT_STATE_PATH,
    DEFAULT_STATE_NAME,
    RESOURCE_DIR,
    RESOURCE_FILE_PATH,
    RESOURCE_STATE_PATH,
    TERRAFORMER_FILTER,
)

log = logging.getLogger(__name__)


def run_command(cmd, env=[]):
    env_copy = os.environ.copy()
    env_copy.update(env)
    try:
        subprocess.run(
            cmd,
            env=env_copy,
            # stdout=subprocess.PIPE,
            # stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as e:
        print("Error running command", " ".join(cmd), e)


def terraformer_import(ctx):
    resource_list = []
    filter_list = []

    # Initialize terraform in current directory
    run_command(["terraform", "init"])

    for resource in ctx.obj["resources"]:
        if resource.resource_filter:
            filter_list.append(TERRAFORMER_FILTER.format(resource.resource_filter))
        resource_list.append(resource.resource_name)

    env = {
        "DATADOG_API_KEY": ctx.obj.get("source_api_key"),
        "DATADOG_APP_KEY": ctx.obj.get("source_app_key"),
        "DATADOG_HOST": ctx.obj.get("source_api_url"),
        "DD_HTTP_CLIENT_RETRY_ENABLED": "true",
    }

    run_command(
        [
            ctx.obj.get("terraformer_bin_path"),
            "import",
            "datadog",
            f'--resources={",".join(resource_list)}',
            " ".join(filter_list),
            "-O=json",
            "--path-pattern={output}/{service}/",
            "--path-output=resources",
            "-c=false",
        ],
        env,
    )

    for resource in ctx.obj["resources"]:
        if os.path.exists(RESOURCE_FILE_PATH.format(resource.resource_name)):
            state_file_name = DEFAULT_STATE_NAME.format(resource.resource_name)
            if os.path.exists(DEFAULT_STATE_PATH + state_file_name):
                copyfile(
                    DEFAULT_STATE_PATH + state_file_name,
                    RESOURCE_STATE_PATH.format(resource.resource_name),
                )


def terraform_apply_resource(ctx, resource):
    env = {
        "DD_API_KEY": ctx.obj.get("destination_api_key"),
        "DD_APP_KEY": ctx.obj.get("destination_app_key"),
        "DD_HOST": ctx.obj.get("destination_api_url"),
        "DD_HTTP_CLIENT_RETRY_ENABLED": "true",
    }
    root_path = ctx.obj["root_path"]

    if not os.path.exists(DEFAULT_STATE_PATH):
        os.mkdir(DEFAULT_STATE_PATH)

    resource_dir = RESOURCE_DIR.format(resource.resource_name)
    if os.path.exists(resource_dir):
        resource_plugin_path = resource_dir + "/.terraform/"
        if not os.path.exists(resource_plugin_path):
            os.mkdir(resource_plugin_path)
        copy_tree(".terraform/", resource_plugin_path)

        os.chdir(resource_dir)

        run_command(["terraform", "apply", "--auto-approve"], env)

        copyfile(
            "./terraform.tfstate",
            "{}/{}/{}".format(
                root_path,
                DEFAULT_STATE_PATH,
                DEFAULT_STATE_NAME.format(resource.resource_name),
            ),
        )
        os.chdir(root_path)
