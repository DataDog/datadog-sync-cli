import logging
import os
import json
import subprocess
from shutil import copyfile
from distutils.dir_util import copy_tree

from datadog_sync.constants import (
    DEFAULT_STATE_PATH,
    DEFAULT_STATE_NAME,
    RESOURCE_DIR,
    RESOURCE_FILE_PATH,
    TERRAFORMER_FILTER,
    RESOURCE_VARIABLES_PATH,
    RESOURCE_STATE_PATH,
    EMPTY_VARIABLES_REMOTE_STATE,
    VALUES_FILE,
)

log = logging.getLogger("__name__")

def run_command(cmd, env=[]):
    env_copy = os.environ.copy()
    env_copy.update(env)

    log.info("Running command '%s'", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            env=env_copy,
            capture_output=True,
            text=True,
            check=True
        )

        # subprocess output with indent
        if len(proc.stdout) > 0:
            log.info("\n\t" + proc.stdout.replace('\n', '\n\t'))

        if len(proc.stderr) > 0:
            log.error("\n\t" + proc.stderr.replace('\n', '\n\t'))

    except subprocess.CalledProcessError as e:
        log.error("Error running command", " ".join(cmd), e)


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
    absolute_var_file_path = root_path + "/" + VALUES_FILE

    resource_dir = RESOURCE_DIR.format(resource.resource_name)
    resource_plugin_path = resource_dir + "/.terraform/"

    if not os.path.exists(resource_plugin_path):
        os.mkdir(resource_plugin_path)
    copy_tree(".terraform/", resource_plugin_path)

    os.chdir(resource_dir)

    run_command(["terraform", "apply", "--auto-approve", f"-var-file={absolute_var_file_path}"], env)

    copyfile(
        "./terraform.tfstate",
        "{}/{}/{}".format(
            root_path,
            DEFAULT_STATE_PATH,
            DEFAULT_STATE_NAME.format(resource.resource_name),
        ),
    )
    os.chdir(root_path)


def translate_id(_id):
    return _id.translate({ord(c): "-%04X-" % ord(c) for c in ":-"})


def create_remote_state(resource, resource_connected):
    variables_path = RESOURCE_VARIABLES_PATH.format(resource)
    resource_connected_state_path = RESOURCE_STATE_PATH.format(resource_connected)
    if os.path.exists(variables_path):
        with open(variables_path, "r") as f:
            v = json.load(f)
        if "data" in v and resource_connected in v["data"]["terraform_remote_state"]:
            pass
        else:
            if os.path.exists(resource_connected_state_path):
                if "data" not in v:
                    v = {**EMPTY_VARIABLES_REMOTE_STATE, **v}
                v["data"]["terraform_remote_state"][resource_connected] = {
                    "backend": "local",
                    "config": {"path": f"../../resources/{resource_connected}/terraform.tfstate"},
                }
                with open(variables_path, "w") as f:
                    json.dump(v, f, indent=2)
    else:
        v = {}
        if os.path.exists(resource_connected_state_path):
            v = EMPTY_VARIABLES_REMOTE_STATE
            v["data"]["terraform_remote_state"][resource_connected] = {
                "backend": "local",
                "config": {"path": f"../../resources/{resource_connected}/terraform.tfstate"},
            }
        with open(variables_path, "a+") as f:
            json.dump(v, f, indent=2)
