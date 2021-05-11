import json
import os
import re

from datadog_sync.constants import (
    RESOURCE_FILE_PATH,
    RESOURCE_OUTPUT_PATH,
    RESOURCE_OUTPUT_CONNECT,
    RESOURCE_VARIABLES_PATH,
    RESOURCE_STATE_PATH,
    EMPTY_VARIABLES_FILE,
)

CONNECT_RESOURCES_OBJ = {
    "dashboard_json": {
        "monitor": [
            {
                "dashboard.[json].widgets.definition.alert_id": "id",
                "dashboard.[json].widgets.definition.widgets.definition.alert_id": "id",
            }
        ]
    },
    "downtime": {
        "monitor": [
            {
                "monitor_id": "id",
            }
        ]
    },
    "monitor": {
        "monitor": [
            {
                "query": "id",
            }
        ]
    },
    "user": {"role": [{"roles": "id"}]},
}


def connect_resources(ctx):
    resources = ctx.obj.get("resources")
    for resource in resources:
        if resource.resource_name in CONNECT_RESOURCES_OBJ:
            for k in CONNECT_RESOURCES_OBJ[resource.resource_name].keys():
                connect(
                    resource.resource_name,
                    k,
                    CONNECT_RESOURCES_OBJ[resource.resource_name][k],
                )
                create_remote_state(resource.resource_name, k)


def connect(resource, resource_to_connect, connections):
    resources_path = RESOURCE_FILE_PATH.format(resource)
    resources_to_connect_output = RESOURCE_OUTPUT_PATH.format(resource_to_connect)
    if os.path.exists(resources_to_connect_output):
        with open(resources_path, "r") as f:
            resources = json.load(f)
        with open(resources_to_connect_output, "r") as f:
            resource_to_connect_state_outputs = json.load(f)["output"].keys()

        # Handle dashboard_json resource:
        r_name = "datadog_{}".format(resource)
        for _, r_obj in resources["resource"][r_name].items():
            for c in connections:
                for k, v in c.items():
                    replace(
                        k.split("."),
                        r_obj,
                        resource,
                        resource_to_connect,
                        resource_to_connect_state_outputs,
                    )

        with open(resources_path, "w") as f:
            json.dump(resources, f, indent=2)


def replace(keys, r_obj, resource, resource_to_connect, outputs):
    if len(keys) == 1 and keys[0] in r_obj:
        replace_keys(keys[0], r_obj, resource, resource_to_connect, outputs)

    if isinstance(r_obj, list):
        for k in r_obj:
            replace(keys, k, resource, resource_to_connect, outputs)

    if isinstance(r_obj, dict):
        if keys[0] in r_obj:
            if len(keys) > 1 and keys[1] == "[json]":
                js_obj = json.loads(r_obj[keys[0]])
                replace(keys[2:], js_obj, resource, resource_to_connect, outputs)
                r_obj[keys[0]] = json.dumps(js_obj)
            else:
                replace(keys[1:], r_obj[keys[0]], resource, resource_to_connect, outputs)


def replace_keys(key, r_obj, resource, resource_to_connect, outputs):
    # Handle special case of composite monitors which references other monitors in the query
    if resource == resource_to_connect and r_obj["type"] == "composite":
        ids = re.findall("[0-9]+", r_obj[key])
        for _id in ids:
            for name in outputs:
                if translate_id(_id) in name:
                    # We need to explicitly disable monitor validation
                    r_obj["validate"] = "false"
                    r_obj[key] = r_obj[key].replace(
                        _id, RESOURCE_OUTPUT_CONNECT.format(resource_to_connect, name)
                    )
                    break
        return

    if isinstance(r_obj[key], list):
        i = 0
        while i < len(r_obj[key]):
            for name in outputs:
                if translate_id(r_obj[key][i]) in name:
                    r_obj[key][i] = RESOURCE_OUTPUT_CONNECT.format(
                        resource_to_connect, name
                    )
                    break
            i += 1
    else:
        for name in outputs:
            if translate_id(r_obj[key]) in name:
                r_obj[key] = RESOURCE_OUTPUT_CONNECT.format(resource_to_connect, name)
                print(r_obj[key])
                return


def translate_id(_id):
    return _id.translate({ord(c): "-%04X-" % ord(c) for c in "-"})


def create_remote_state(resource, resource_connected):
    variables_path = RESOURCE_VARIABLES_PATH.format(resource)
    resource_connected_state_path = RESOURCE_STATE_PATH.format(resource_connected)
    if os.path.exists(variables_path):
        with open(variables_path, "r") as f:
            v = json.load(f)
        if resource_connected in v["data"]["terraform_remote_state"]:
            pass
        else:
            v["data"]["terraform_remote_state"][resource_connected] = {
                "backend": "local",
                "config": {
                    "path": f"../../resources/{resource_connected}/terraform.tfstate"
                },
            }
            with open(variables_path, "w") as f:
                json.dump(v, f, indent=2)
    elif os.path.exists(resource_connected_state_path):
        v = EMPTY_VARIABLES_FILE
        v["data"]["terraform_remote_state"][resource_connected] = {
            "backend": "local",
            "config": {
                "path": f"../../resources/{resource_connected}/terraform.tfstate"
            },
        }
        with open(variables_path, "a+") as f:
            json.dump(v, f, indent=2)
