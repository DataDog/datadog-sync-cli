import copy
from concurrent.futures import ThreadPoolExecutor, wait

from requests.exceptions import HTTPError
from pprint import pformat

from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.utils.custom_client import paginated_request


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
    def __init__(self, config):
        super().__init__(config, RESOURCE_TYPE, BASE_PATH, excluded_attributes=EXCLUDED_ATTRIBUTES)

    def import_resources(self):
        roles = {}
        source_client = self.config.source_client

        try:
            resp = paginated_request(source_client.get)(self.base_path)
        except HTTPError as e:
            self.logger.error("error importing roles: %s", e.response.text)
            return

        self.import_resources_concurrently(roles, resp)

        # Write resources to file
        self.write_resources_file("source")

    def process_resource_import(self, role, roles):
        roles[role["id"]] = role

    def apply_resources(self):
        self.open_resources()
        source_permission, destination_permission = self.get_permissions()
        source_roles_mapping = self.get_source_roles_mapping()
        destination_roles_mapping = self.get_destination_roles_mapping()

        self.apply_resources_concurrently(
            {},
            source_permission=source_permission,
            destination_permission=destination_permission,
            source_roles_mapping=source_roles_mapping,
            destination_roles_mapping=destination_roles_mapping,
        )

        self.write_resources_file("destination")

    def prepare_resource_and_apply(self, _id, role, local_destination_resources, connection_resource_obj, **kwargs):
        source_permission = kwargs.get("source_permission")
        destination_permission = kwargs.get("destination_permission")
        source_roles_mapping = kwargs.get("source_roles_mapping")
        destination_roles_mapping = kwargs.get("destination_roles_mapping")

        # Remap permissions if different datacenters
        self.remap_permissions(role, source_permission, destination_permission)
        # Remap role id's
        self.remap_role_id(role, source_roles_mapping, destination_roles_mapping)

        if _id in self.destination_resources:
            self.update_role(_id, role)
        elif role["attributes"]["name"] in destination_roles_mapping:
            self.destination_resources[_id] = role
        else:
            self.create_role(_id, role)

    def create_role(self, _id, role):
        destination_client = self.ctx.obj.get("destination_client")
        role_copy = copy.deepcopy(role)
        self.remove_excluded_attr(role_copy)

        payload = {"data": role_copy}
        try:
            resp = destination_client.post(self.base_path, payload)
        except HTTPError as e:
            self.logger.error("error creating role: %s", e.response.text)
            return
        self.destination_resources[_id] = resp.json()["data"]

    def update_role(self, _id, role):
        destination_client = self.ctx.obj.get("destination_client")
        role_copy = copy.deepcopy(role)
        payload = {"data": role_copy}
        self.remove_excluded_attr(role_copy)

        diff = self.check_diff(role, self.destination_resources[_id])
        if diff:
            role_copy["id"] = self.destination_resources[_id]["id"]
            try:
                resp = destination_client.patch(self.base_path + f"/{self.destination_resources[_id]['id']}", payload)
            except HTTPError as e:
                self.logger.error("error updating role: %s", e.response.text)
                return

            self.destination_resources[_id] = resp.json()["data"]

    def check_diffs(self):
        self.open_resources()
        source_permission, destination_permission = self.get_permissions()
        source_roles_mapping = self.get_source_roles_mapping()
        destination_roles_mapping = self.get_destination_roles_mapping()

        for _id, role in self.source_resources.items():
            self.remap_permissions(role, source_permission, destination_permission)
            self.remap_role_id(role, source_roles_mapping, destination_roles_mapping)

            # if self.resource_connections:
            #     self.connect_resources(role, connection_resource_obj)

            if _id in self.destination_resources:
                diff = self.check_diff(self.destination_resources[_id], role)
                if diff:
                    self.logger.info("%s resource ID %s diff: \n %s", self.resource_type, _id, pformat(diff))
            else:
                self.logger.info("Resource to be added %s: \n %s", self.resource_type, pformat(role))

    def get_permissions(self):
        source_permission_obj = {}
        destination_permission_obj = {}

        source_client = self.config.source_client
        destination_client = self.config.destination_client
        try:
            source_permissions = source_client.get(PERMISSIONS_BASE_PATH).json()["data"]
            destination_permissions = destination_client.get(PERMISSIONS_BASE_PATH).json()["data"]
        except HTTPError as e:
            self.logger.error("error getting permissions: %s", e.response.text)
            return

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
        if self.config.source_client.host != self.config.destination_client.host:
            if "permissions" in role["relationships"]:
                for permission in role["relationships"]["permissions"]["data"]:
                    if permission["id"] in source_permission:
                        permission["id"] = destination_permission[source_permission[permission["id"]]]

    def get_destination_roles_mapping(self):
        destination_client = self.config.destination_client
        destination_roles_mapping = {}

        try:
            destination_roles_resp = paginated_request(destination_client.get)(self.base_path)
        except HTTPError as e:
            self.logger.error("error retrieving roles: %s", e.response.text)
            return

        for role in destination_roles_resp:
            destination_roles_mapping[role["attributes"]["name"]] = role["id"]
        return destination_roles_mapping

    def get_source_roles_mapping(self):
        source_roles_mapping = {}
        for role in self.source_resources.values():
            source_roles_mapping[role["id"]] = role["attributes"]["name"]
        return source_roles_mapping
