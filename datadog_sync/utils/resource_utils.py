import logging
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

log = logging.getLogger(__name__)

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
    log.info("Processing Resources")
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

def replace(keys_list, r_obj, resource_to_connect, connection_resources_obj):
    if len(keys_list) == 1 and keys_list[0] in r_obj:
        replace_ids(keys_list[0], r_obj, resource_to_connect, connection_resources_obj)
        return

    if isinstance(r_obj, list):
        for k in r_obj:
            replace(keys_list, k, resource_to_connect, connection_resources_obj)

    if isinstance(r_obj, dict):
        if keys_list[0] in r_obj:
            replace(keys_list[1:], r_obj[keys_list[0]], resource_to_connect, connection_resources_obj)


def replace_ids(key, r_obj, resource_to_connect, connection_resources_obj):
    if resource_to_connect in connection_resources_obj:
        if "type" in r_obj and r_obj["type"] == "composite":
            ids = re.findall("[0-9]+", r_obj[key])
            for _id in ids:
                new_id = f"{connection_resources_obj[resource_to_connect][_id]['id']}"
                if _id in connection_resources_obj[resource_to_connect]:
                    r_obj[key] = re.sub(_id + r"([^#]|$)", new_id + "# ", r_obj[key])

            r_obj[key] = (r_obj[key].replace("#", "")).strip()

            return

        if isinstance(r_obj[key], list):
            i = 0
            while i < len(r_obj[key]):
                _id = r_obj[key][i]
                if _id in connection_resources_obj[resource_to_connect]:
                    r_obj[key][i] = f"{connection_resources_obj[resource_to_connect][_id]['id']}"
                i += 1
        else:
            if r_obj[key] in connection_resources_obj[resource_to_connect]:
                if resource_to_connect == "synthetics_tests":
                    r_obj[key] = f"{connection_resources_obj[resource_to_connect][r_obj[key]]['public_id']}"
                else:
                    r_obj[key] = f"{connection_resources_obj[resource_to_connect][r_obj[key]]['id']}"
