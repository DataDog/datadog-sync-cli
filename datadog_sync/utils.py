import os
import subprocess
from shutil import copyfile
from distutils.dir_util import copy_tree

from datadog_sync.constants import (
    RESOURCE_OUTPUT_PATH,
    DEFAULT_STATE_PATH,
    DEFAULT_STATE_NAME,
)

from datadog_sync.models import Dashboard, Monitor, Role, User


def get_resources(ctx):
    """Returns list of Resources. Order of resource applied are based on the list returned"""
    return [Role(ctx), User(ctx), Monitor(ctx), Dashboard(ctx)]


def run_command(cmd, env=[]):
    env_copy = os.environ.copy()
    env_copy.update(env)
    try:
        subprocess.run(cmd, env=env_copy)
    except subprocess.CalledProcessError as e:
        print("Error running command", " ".join(cmd), e)


def terraformer_import(ctx):
    resource_list = []

    # Initialize terraform in current directory
    run_command(["terraform", "init"])

    for resource in ctx.obj["resources"]:
        resource_list.append(resource.resource_name)

    env = {
        "DATADOG_API_KEY": ctx.obj.get("source_api_key"),
        "DATADOG_APP_KEY": ctx.obj.get("source_app_key"),
        "DATADOG_API_URL": ctx.obj.get("source_api_url"),
    }

    run_command(
        [
            ctx.obj.get("terraformer_bin_path"),
            "import",
            "datadog",
            f'--resources={",".join(resource_list)}',
            "-O=json",
        ],
        env,
    )


def terraform_apply_resources(ctx):
    root_path = ctx.obj.get("root_path")
    env = {
        "DD_API_KEY": ctx.obj.get("destination_api_key"),
        "DD_APP_KEY": ctx.obj.get("destination_app_key"),
        "DD_HOST": ctx.obj.get("destination_api_url"),
    }

    if not os.path.exists(root_path + DEFAULT_STATE_PATH):
        os.mkdir(root_path + DEFAULT_STATE_PATH)

    for resource in ctx.obj["resources"]:
        resource_dir = root_path + RESOURCE_OUTPUT_PATH.format(resource.resource_name)
        if os.path.exists(resource_dir + "/" + resource.resource_name + ".tf.json"):
            state_file_name = DEFAULT_STATE_NAME.format(resource.resource_name)
            if os.path.exists(root_path + DEFAULT_STATE_PATH + state_file_name):
                copyfile(
                    root_path + DEFAULT_STATE_PATH + state_file_name,
                    resource_dir + "/terraform.tfstate",
                )

            resource_plugin_path = resource_dir + "/.terraform/"
            if not os.path.exists(resource_plugin_path):
                os.mkdir(resource_plugin_path)
            copy_tree(root_path + "/.terraform/", resource_plugin_path)

            os.chdir(resource_dir)
            run_command(["terraform", "apply", "--auto-approve"], env)
            copyfile(
                "./terraform.tfstate", root_path + DEFAULT_STATE_PATH + state_file_name
            )
            os.chdir(root_path)
