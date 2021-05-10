import json
import os

from datadog_sync.constants import (
    RESOURCE_FILE_PATH,
    RESOURCE_OUTPUT_PATH,
    RESOURCE_OUTPUT_CONNECT,
    RESOURCE_VARIABLES_PATH,
    RESOURCE_STATE_PATH,
    EMPTY_VARIABLES_FILE
)

CONNECT_RESOURCES_OBJ = {
    "dashboard": {
        "monitor": [
            {
                "widget.alert_value_definition.alert_id": "id",
                "widget.group_definition.widget.alert_value_definition.alert_id": "id",
                "widget.alert_graph_definition.alert_id": "id",
                "widget.group_definition.widget.alert_graph_definition.alert_id": "id",
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

        r_name = "datadog_{}".format(resource)
        for test, r in resources["resource"][r_name].items():
            for c in connections:
                for k, v in c.items():
                    replace(
                        k.split("."),
                        r,
                        resource_to_connect,
                        resource_to_connect_state_outputs,
                    )

        with open(resources_path, "w") as f:
            json.dump(resources, f, indent=2)


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


def replace(keys, var, resource, outputs):
    if len(keys) == 1 and keys[0] in var:
        if isinstance(var[keys[0]], list):
            i = 0
            while i < len(var[keys[0]]):
                for k in outputs:
                    if (
                        var[keys[0]][i].translate(
                            {ord(c): "-%04X-" % ord(c) for c in "-"}
                        )
                        in k
                    ):
                        var[keys[0]][i] = RESOURCE_OUTPUT_CONNECT.format(resource, k)
                        break
                i += 1
        else:
            for k in outputs:
                if (
                    var[keys[0]].translate({ord(c): "-%04X-" % ord(c) for c in "-"})
                    in k
                ):
                    var[keys[0]] = RESOURCE_OUTPUT_CONNECT.format(resource, k)
                    break

    if isinstance(var, list):
        for k in var:
            replace(keys, k, resource, outputs)

    if isinstance(var, dict):
        if keys[0] in var:
            replace(keys[1:], var[keys[0]], resource, outputs)
