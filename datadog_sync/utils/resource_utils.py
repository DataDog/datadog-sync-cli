import re


class ResourceConnectionError(Exception):
    def __init__(self, resource_type, _id=None):
        self.resource_type = resource_type
        self._id = _id

        super(ResourceConnectionError, self).__init__(
            f"Failed to connect resource. Import and sync resource: {resource_type} {'with ID: ' + _id if _id else ''}"
        )


def replace(keys_list, origin, r_obj, resource_to_connect, resources):
    if resource_to_connect not in resources:
        raise ResourceConnectionError(resource_to_connect)

    if isinstance(r_obj, list):
        for k in r_obj:
            replace(keys_list, origin, k, resource_to_connect, resources)
    else:
        keys_list = keys_list.split(".", 1)

        if len(keys_list) == 1 and keys_list[0] in r_obj:
            # Handle cases where value is null/None
            if not r_obj[keys_list[0]]:
                return
            replace_ids(keys_list[0], origin, r_obj, resource_to_connect, resources)
            return

        if isinstance(r_obj, dict):
            if keys_list[0] in r_obj:
                replace(keys_list[1], origin, r_obj[keys_list[0]], resource_to_connect, resources)


def replace_ids(key, origin, r_obj, resource_to_connect, resources):
    destination_resources = resources[resource_to_connect].destination_resources

    if r_obj.get("type") == "composite" and key == "query":
        ids = re.findall("[0-9]+", r_obj[key])
        for _id in ids:
            if _id in destination_resources:
                new_id = f"{destination_resources[_id]['id']}"
                r_obj[key] = re.sub(_id + r"([^#]|$)", new_id + "# ", r_obj[key])
            else:
                raise ResourceConnectionError(resource_to_connect, _id)

        r_obj[key] = (r_obj[key].replace("#", "")).strip()

        return
    if resource_to_connect == "monitors" and key == "query":
        return

    if isinstance(r_obj[key], list):
        # case of monitor-based SLO
        if r_obj.get("type") == "monitor":
            for i in range(len(r_obj[key])):
                _id = str(r_obj[key][i])
                if _id in destination_resources:
                    r_obj[key][i] = destination_resources[_id]["id"]
                else:
                    raise ResourceConnectionError(resource_to_connect, _id)
            return
        else:
            for i in range(len(r_obj[key])):
                _id = r_obj[key][i]
                if not resources[resource_to_connect].validate_id(_id):
                    continue
                if _id in destination_resources:
                    r_obj[key][i] = f"{destination_resources[_id]['id']}"
                else:
                    raise ResourceConnectionError(resource_to_connect, _id)
            return
    else:
        if origin == "downtimes":
            _id = str(r_obj[key])
            if _id in destination_resources:
                r_obj[key] = destination_resources[_id]["id"]
            else:
                raise ResourceConnectionError(resource_to_connect, _id)
        else:
            if r_obj[key] in destination_resources:
                if resource_to_connect == "synthetics_tests":
                    r_obj[key] = f"{destination_resources[r_obj[key]]['public_id']}"
                else:
                    r_obj[key] = f"{destination_resources[r_obj[key]]['id']}"
            else:
                raise ResourceConnectionError(resource_to_connect, r_obj[key])
