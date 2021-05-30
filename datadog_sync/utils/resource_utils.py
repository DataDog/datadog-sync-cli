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
    if isinstance(r_obj[key], list):
        i = 0
        while i < len(r_obj[key]):
            # TODO: handle lists
            i += 1
    else:
        if r_obj[key] in connection_resources_obj[resource_to_connect]:
            r_obj[key] = f"{connection_resources_obj[resource_to_connect][r_obj[key]]['id']}"
