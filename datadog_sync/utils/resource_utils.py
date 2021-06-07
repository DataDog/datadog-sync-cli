import re


def replace(keys_list, r_obj, resource_to_connect, connection_resources_obj):
    if resource_to_connect not in connection_resources_obj:
        return

    if isinstance(r_obj, list):
        for k in r_obj:
            replace(keys_list, k, resource_to_connect, connection_resources_obj)
    else:
        keys_list = keys_list.split(".", 1)

        if len(keys_list) == 1 and keys_list[0] in r_obj:
            replace_ids(keys_list[0], r_obj, resource_to_connect, connection_resources_obj)
            return

        if isinstance(r_obj, dict):
            if keys_list[0] in r_obj:
                replace(keys_list[1], r_obj[keys_list[0]], resource_to_connect, connection_resources_obj)


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
