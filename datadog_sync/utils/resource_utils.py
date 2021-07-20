from concurrent.futures import ThreadPoolExecutor


class ResourceConnectionError(Exception):
    def __init__(self, resource_type, _id=None):
        self.resource_type = resource_type
        self._id = _id

        super(ResourceConnectionError, self).__init__(
            f"Failed to connect resource. Import and sync resource: {resource_type} {'with ID: ' + _id if _id else ''}"
        )


def find_attr(keys_list, resource_to_connect, r_obj, connect_func):
    _id = None
    if isinstance(r_obj, list):
        for k in r_obj:
            find_attr(keys_list, resource_to_connect, k, connect_func)
    else:
        keys_list = keys_list.split(".", 1)

        if len(keys_list) == 1 and keys_list[0] in r_obj:
            if not r_obj[keys_list[0]]:
                return
            connect_func(keys_list[0], r_obj, resource_to_connect)
            return

        if isinstance(r_obj, dict):
            if keys_list[0] in r_obj:
                find_attr(keys_list[1], resource_to_connect, r_obj[keys_list[0]], connect_func)


def thread_pool_executor(max_workers=None):
    return ThreadPoolExecutor(max_workers=max_workers)
