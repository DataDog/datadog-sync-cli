import json
import os
import re

from datadog_sync.constants import (
    RESOURCE_FILE_PATH,
    RESOURCE_VARIABLES_PATH,
    RESOURCE_OUTPUT_PATH,
    RESOURCE_OUTPUT_CONNECT,
    RESOURCE_VARS,
    VALUES_FILE,
)
from datadog_sync.utils.helpers import translate_id, create_remote_state

"""
Format of  the CONNECT_RESOURCE_OBJ is # {resource: resource_referenced: [attribute_key]}
Special keys:
- [JSON] - indicates that the previous attribute is in json string format. See dashboard_json example below.
- "VALUES" - list of attributes for the resource that are not generated on import.
These are generally sensitive values that are not returned by the api.
"""
CONNECT_RESOURCES_OBJ = {
    "dashboard_json": {
        "monitor": [
            "dashboard.[JSON].widgets.definition.alert_id",
            "dashboard.[JSON].widgets.definition.widgets.definition.alert_id",
        ],
    },
    "downtime": {
        "monitor": [
            "monitor_id",
        ]
    },
    "monitor": {
        "monitor": [
            "query",
        ]
    },
    "user": {"role": ["roles"]},
    "synthetics_test": {
        "synthetics_private_location": [
            "locations",
        ],
        "VALUES": [
            "api_step.request_client_certificate.key.content",
            "api_step.request_client_certificate.cert.content",
        ],
    },
}


def process_resources(ctx):
    resources_obj = ctx.obj.get("resources")
    for resource in resources_obj:
        resource_path = RESOURCE_FILE_PATH.format(resource.resource_type)
        r_name = "datadog_{}".format(resource.resource_type)
        if os.path.exists(resource_path) and resource.resource_type in CONNECT_RESOURCES_OBJ:
            # values.tf.json object
            values = dict()
            # variables.tf.json object
            variables = dict()
            # Outputs object. We store the outputs in an object so we do not have to load it from file every time.
            outputs = dict()

            for resource_to_connect in CONNECT_RESOURCES_OBJ[resource.resource_type].keys():
                create_remote_state(resource.resource_type, resource_to_connect)

                output_path = RESOURCE_OUTPUT_PATH.format(resource_to_connect)
                if os.path.exists(output_path):
                    with open(output_path, "r") as t:
                        outputs = json.load(t)["output"].keys()

            # Load all of the resources for the given resource
            with open(resource_path, "r") as f:
                resources = json.load(f)

            for r_name, r_obj in resources["resource"][r_name].items():
                for resource_to_connect in CONNECT_RESOURCES_OBJ[resource.resource_type].keys():
                    if resource_to_connect == "VALUES":
                        process_attributes(
                            resource_type=resource.resource_type,
                            r_obj=r_obj,
                            resource_to_connect=resource_to_connect,
                            connections=CONNECT_RESOURCES_OBJ[resource.resource_type][resource_to_connect],
                            r_name=r_name,
                            values=values,
                            variables=variables,
                        )
                    else:
                        process_attributes(
                            resource_type=resource.resource_type,
                            r_obj=r_obj,
                            resource_to_connect=resource_to_connect,
                            connections=CONNECT_RESOURCES_OBJ[resource.resource_type][resource_to_connect],
                            outputs=outputs,
                        )
            update_files(resource.resource_type, resources, values, variables)


def process_attributes(
    resource_type,
    r_obj,
    connections,
    outputs=None,
    resource_to_connect=None,
    r_name=None,
    values=None,
    variables=None,
):
    for c in connections:
        replace(
            key_str=c,
            keys_list=c.split("."),
            r_obj=r_obj,
            outputs=outputs,
            resource_type=resource_type,
            resource_to_connect=resource_to_connect,
            values=values,
            variables=variables,
            r_name=r_name,
        )


def replace(key_str, keys_list, r_obj, outputs, resource_type, resource_to_connect, values, variables, r_name):
    # Handle attributes that are not generated. Values.tf.var
    if len(keys_list) == 1 and resource_to_connect == "VALUES":
        replace_values(key_str, keys_list[0], r_obj, values, variables, r_name)
        return

    if len(keys_list) == 1 and keys_list[0] in r_obj:
        replace_ids(keys_list[0], r_obj, outputs, resource_type, resource_to_connect)
        return

    if isinstance(r_obj, list):
        for k in r_obj:
            replace(key_str, keys_list, k, outputs, resource_type, resource_to_connect, values, variables, r_name)

    if isinstance(r_obj, dict):
        if keys_list[0] in r_obj:
            # Handle nested JSON string attributes
            if len(keys_list) > 1 and keys_list[1] == "[JSON]":
                js_obj = json.loads(r_obj[keys_list[0]])
                replace(
                    key_str,
                    keys_list[2:],
                    js_obj,
                    outputs,
                    resource_type,
                    resource_to_connect,
                    values,
                    variables,
                    r_name,
                )
                r_obj[keys_list[0]] = json.dumps(js_obj)
            else:
                replace(
                    key_str,
                    keys_list[1:],
                    r_obj[keys_list[0]],
                    outputs,
                    resource_type,
                    resource_to_connect,
                    values,
                    variables,
                    r_name,
                )


def replace_values(key_str, key, r_obj, values, variables, r_name):
    if isinstance(r_obj, list):
        i = 0
        while i < len(r_obj):
            var_key = "{}--{}-{}".format(r_name, key_str.replace(".", "-"), i)
            val = RESOURCE_VARS.format(var_key)
            r_obj[i][key] = val
            variables[var_key] = {}
            values[var_key] = ""
            i += 1
    else:
        var_key = "{}--{}".format(r_name, key_str.replace(".", "-"))
        val = RESOURCE_VARS.format(var_key)
        r_obj[key] = val
        variables[var_key] = {}
        values[var_key] = ""
        return


def replace_ids(key, r_obj, outputs, resource, resource_to_connect):
    # Handle special case for composite monitors which references other monitors in the query
    if resource == "monitor" and resource == resource_to_connect and r_obj["type"] == "composite":
        ids = re.findall("[0-9]+", r_obj[key])
        for _id in ids:
            for output in outputs:
                if translate_id(_id) in output:
                    # We need to explicitly disable monitor validation
                    r_obj["validate"] = "false"
                    r_obj[key] = r_obj[key].replace(_id, RESOURCE_OUTPUT_CONNECT.format(resource_to_connect, output))
                    break
        return

    if isinstance(r_obj[key], list):
        i = 0
        while i < len(r_obj[key]):
            for name in outputs:
                if translate_id(r_obj[key][i]) in name:
                    r_obj[key][i] = RESOURCE_OUTPUT_CONNECT.format(resource_to_connect, name)
                    break
            i += 1
    else:
        for name in outputs:
            if translate_id(r_obj[key]) in name:
                r_obj[key] = RESOURCE_OUTPUT_CONNECT.format(resource_to_connect, name)
                return


def update_files(resource, resources, values, variables):
    variables_path = RESOURCE_VARIABLES_PATH.format(resource)
    resource_path = RESOURCE_FILE_PATH.format(resource)

    # Write 'resources'.tf.json
    with open(resource_path, "w") as f:
        json.dump(resources, f, indent=2)

    # Write variables.tf.json file
    if variables:
        with open(variables_path, "r") as f:
            v = json.load(f)
        if "variable" in v:
            v["variable"] = {**v["variable"], **variables}
        else:
            v["variable"] = variables
        with open(variables_path, "w") as f:
            json.dump(v, f, indent=2)

    # Write values.tfvars.json file
    if values:
        with open(VALUES_FILE, "r") as f:
            v = json.load(f)
        v = {**values, **v}
        with open(VALUES_FILE, "w") as f:
            json.dump(v, f, indent=2)
