import copy
from concurrent.futures import ThreadPoolExecutor, wait

from deepdiff import DeepDiff

from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.utils.custom_client import paginated_request


RBAC_NOT_ENABLED_MESSAGE = "Custom RBAC is not enabled for this account"
RESOURCE_TYPE = "roles"
EXCLUDED_ATTRIBUTES = [
    "root['id']",
    "root['attributes']['created_at']",
    "root['attributes']['modified_at']",
    "root['attributes']['user_count']",
]
BASE_PATH = "/api/v2/roles"
PERMISSIONS_BASE_PATH = "/api/v2/permissions"


class Roles(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_TYPE, BASE_PATH, excluded_attributes=EXCLUDED_ATTRIBUTES)

    def import_resources(self):
        roles = {}
        source_client = self.ctx.obj.get("source_client")

        roles_resp = paginated_request(source_client.get)(BASE_PATH)

        with ThreadPoolExecutor() as executor:
            wait([executor.submit(self.process_resource, role, roles) for role in roles_resp])

        # Write resources to file
        self.write_resources_file("source", roles)

    def process_resource(self, role, roles):
        roles[role["id"]] = role

    def apply_resources(self):
        source_roles, local_destination_roles = self.open_resources()
        source_permission, destination_permission = self.get_permissions()
        source_roles_mapping = self.get_source_roles_mapping(source_roles)
        destination_roles_mapping = self.get_destination_roles_mapping()

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(
                        self.prepare_resource_and_apply,
                        _id,
                        role,
                        local_destination_roles,
                        source_permission,
                        destination_permission,
                        source_roles_mapping,
                        destination_roles_mapping,
                    )
                    for _id, role in source_roles.items()
                ]
            )

        self.write_resources_file("destination", local_destination_roles)

    def prepare_resource_and_apply(
        self,
        _id,
        role,
        local_destination_roles,
        source_permission,
        destination_permission,
        source_roles_mapping,
        destination_roles_mapping,
    ):
        destination_client = self.ctx.obj.get("destination_client")

        # Remap permissions if different datacenters
        self.remap_permissions(role, source_permission, destination_permission)
        # Remap role id's
        self.remap_role_id(role, source_roles_mapping, destination_roles_mapping)

        # Create copy and remove excluded fields as the API does not allow additional properties
        role_copy = copy.deepcopy(role)
        self.remove_excluded_attr(role_copy)

        payload = {"data": role_copy}
        if _id in local_destination_roles:
            diff = DeepDiff(role, local_destination_roles[_id], ignore_order=True, exclude_paths=EXCLUDED_ATTRIBUTES)
            if diff:
                role_copy["id"] = local_destination_roles[_id]["id"]
                resp = destination_client.patch(BASE_PATH + f"/{local_destination_roles[_id]['id']}", payload)
                if RBAC_NOT_ENABLED_MESSAGE in resp.text:
                    local_destination_roles[_id] = role
                else:
                    local_destination_roles[_id] = resp.json()["data"]
        elif role["attributes"]["name"] in destination_roles_mapping:
            local_destination_roles[_id] = role
        else:
            resp = destination_client.post(BASE_PATH, payload)
            if RBAC_NOT_ENABLED_MESSAGE in resp.text:
                local_destination_roles[_id] = role
            else:
                local_destination_roles[_id] = resp.json()["data"]

    def get_permissions(self):
        source_permission_obj = {}
        destination_permission_obj = {}

        source_client = self.ctx.obj.get("source_client")
        destination_client = self.ctx.obj.get("destination_client")

        source_permissions = source_client.get(PERMISSIONS_BASE_PATH).json()["data"]
        destination_permissions = destination_client.get(PERMISSIONS_BASE_PATH).json()["data"]
        for permission in source_permissions:
            source_permission_obj[permission["id"]] = permission["attributes"]["name"]
        for permission in destination_permissions:
            destination_permission_obj[permission["attributes"]["name"]] = permission["id"]

        return source_permission_obj, destination_permission_obj

    def remap_role_id(self, role, source_roles_mapping, destination_role_mapping):
        if role["id"] in source_roles_mapping:
            if role["id"] in source_roles_mapping and source_roles_mapping[role["id"]] in destination_role_mapping:
                role["id"] = destination_role_mapping[source_roles_mapping[role["id"]]]

    def remap_permissions(self, role, source_permission, destination_permission):
        if self.ctx.obj.get("source_api_url") != self.ctx.obj.get("destination_api_url"):
            if "permissions" in role["relationships"]:
                for permission in role["relationships"]["permissions"]["data"]:
                    if permission["id"] in source_permission:
                        permission["id"] = destination_permission[source_permission[permission["id"]]]

    def get_destination_roles_mapping(self):
        destination_client = self.ctx.obj.get("destination_client")
        destination_roles_mapping = {}

        destination_roles_resp = paginated_request(destination_client.get)(BASE_PATH)

        for role in destination_roles_resp:
            destination_roles_mapping[role["attributes"]["name"]] = role["id"]
        return destination_roles_mapping

    def get_source_roles_mapping(self, source_roles):
        source_roles_mapping = {}
        for role in source_roles.values():
            source_roles_mapping[role["id"]] = role["attributes"]["name"]
        return source_roles_mapping
