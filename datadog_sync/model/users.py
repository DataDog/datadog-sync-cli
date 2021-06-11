import copy
import logging
from concurrent.futures import ThreadPoolExecutor, wait

from requests import HTTPError

from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.utils.custom_client import paginated_request


RESOURCE_TYPE = "users"
EXCLUDED_ATTRIBUTES = [
    "root['id']",
    "root['attributes']['created_at']",
    "root['attributes']['title']",
    "root['attributes']['name']",
    "root['attributes']['status']",
    "root['attributes']['verified']",
    "root['attributes']['service_account']",
    "root['attributes']['handle']",
    "root['attributes']['icon']",
    "root['attributes']['modified_at']",
    "root['relationships']['org']",
]
BASE_PATH = "/api/v2/users"
ROLES_PATH = "/api/v2/roles/{}/users"
RESOURCE_CONNECTIONS = {"roles": ["relationships.roles.data.id"]}
GET_USERS_FILTER = {"filter[status]": "Active"}


log = logging.getLogger(__name__)


class Users(BaseResource):
    def __init__(self, ctx):
        super().__init__(
            ctx,
            RESOURCE_TYPE,
            BASE_PATH,
            excluded_attributes=EXCLUDED_ATTRIBUTES,
            resource_connections=RESOURCE_CONNECTIONS,
        )

    def import_resources(self):
        users = {}
        source_client = self.ctx.obj.get("source_client")

        try:
            users_resp = paginated_request(source_client.get)(self.base_path, params=GET_USERS_FILTER)
        except HTTPError as e:
            self.logger.error("Error while importing Users resource: %s", e)
            return

        with ThreadPoolExecutor() as executor:
            wait([executor.submit(self.process_resource_import, user, users) for user in users_resp])

        # Write resources to file
        self.write_resources_file("source", users)

    def process_resource_import(self, user, users):
        users[user["id"]] = user

    def apply_resources(self):
        source_users, local_destination_users = self.open_resources()
        remote_users = self.get_remote_destination_users()
        connection_resource_obj = self.get_connection_resources()

        with ThreadPoolExecutor() as executor:
            wait(
                [
                    executor.submit(
                        self.prepare_resource_and_apply,
                        _id,
                        user,
                        local_destination_users,
                        remote_users,
                        connection_resource_obj,
                    )
                    for _id, user in source_users.items()
                ]
            )

        self.write_resources_file("destination", local_destination_users)

    def prepare_resource_and_apply(self, _id, user, local_destination_users, remote_users, connection_resource_obj):
        destination_client = self.ctx.obj.get("destination_client")

        if self.resource_connections:
            self.connect_resources(user, connection_resource_obj)

        # Create copy
        resource_copy = copy.deepcopy(user)

        payload = {"data": resource_copy}
        if _id in local_destination_users:
            self.update_resource(_id, user, local_destination_users)
        elif user["attributes"]["handle"] in remote_users:
            remote_user = remote_users[user["attributes"]["handle"]]
            diff = self.check_diff(remote_user, user)
            if diff:
                self.update_user_roles(remote_user["id"], diff)
                self.remove_excluded_attr(resource_copy)
                resource_copy.pop("relationships", None)
                resource_copy["id"] = remote_user["id"]
                try:
                    resp = destination_client.patch(self.base_path + f"/{remote_user['id']}", payload)
                except HTTPError as e:
                    self.logger.error("error updating user: %s", e.response.json())
                local_destination_users[_id] = resp.json()["data"]
            else:
                local_destination_users[_id] = remote_user
        else:
            self.create_resource(_id, user, local_destination_users)

    def create_resource(self, _id, user, local_destination_users):
        destination_client = self.ctx.obj.get("destination_client")
        self.remove_excluded_attr(user)
        user["attributes"].pop("disabled", None)

        try:
            resp = destination_client.post(self.base_path, {"data": user})
        except HTTPError as e:
            self.logger.error("error creating user: %s", e)
        local_destination_users[_id] = resp.json()["data"]

    def update_resource(self, _id, user, local_destination_users):
        destination_client = self.ctx.obj.get("destination_client")
        self.remove_excluded_attr(user)

        diff = self.check_diff(local_destination_users[_id], user)
        if diff:
            self.update_user_roles(local_destination_users[_id]["id"], diff)
            self.remove_excluded_attr(user)
            user["id"] = local_destination_users[_id]["id"]
            user.pop("relationships", None)
            try:
                resp = destination_client.patch(
                    self.base_path + f"/{local_destination_users[_id]['id']}", {"data": user}
                )
            except HTTPError as e:
                self.logger.error("error updating user: %s, %s", e.response.json())
            local_destination_users[_id] = resp.json()["data"]

    def update_existing_user(self, _id, user, local_destination_users, remote_users):
        destination_client = self.ctx.obj.get("destination_client")
        remote_user = remote_users[user["attributes"]["handle"]]

        diff = self.check_diff(remote_user, user)
        if diff:
            self.remove_excluded_attr(user)
            self.update_user_roles(remote_user["id"], diff)
            user.pop("relationships", None)
            user["id"] = remote_user["id"]
            try:
                resp = destination_client.patch(self.base_path + f"/{remote_user['id']}", {"data": user})
            except HTTPError as e:
                self.logger.error("error updating user: %s", e.response.json())
            local_destination_users[_id] = resp.json()["data"]
        else:
            local_destination_users[_id] = remote_user

    def update_user_roles(self, _id, diff):
        for k, v in diff.items():
            if k == "iterable_item_added":
                for key, value in diff["iterable_item_added"].items():
                    if "roles" in key:
                        self.add_user_to_role(_id, value["id"])
            elif k == "iterable_item_removed":
                for key, value in diff["iterable_item_removed"].items():
                    if "roles" in key:
                        self.remove_user_from_role(_id, value["id"])
            elif k == "values_changed":
                for key, value in diff["values_changed"].items():
                    if "roles" in key:
                        self.remove_user_from_role(_id, value["old_value"])
                        self.add_user_to_role(_id, value["new_value"])

    def get_remote_destination_users(self):
        remote_user_obj = {}
        destination_client = self.ctx.obj.get("destination_client")

        try:
            remote_users = paginated_request(destination_client.get)(self.base_path, params=GET_USERS_FILTER)
        except HTTPError as e:
            self.logger.error("error retrieving remote users: %s", e)
            return

        for user in remote_users:
            remote_user_obj[user["attributes"]["email"]] = user

        return remote_user_obj

    def add_user_to_role(self, user_id, role_id):
        destination_client = self.ctx.obj.get("destination_client")
        payload = {"data": {"id": user_id, "type": "users"}}
        try:
            destination_client.post(ROLES_PATH.format(role_id), payload)
        except HTTPError as e:
            self.logger.error("error adding user: %s to role %s: %s", user_id, role_id, e)

    def remove_user_from_role(self, user_id, role_id):
        destination_client = self.ctx.obj.get("destination_client")
        payload = {"data": {"id": user_id, "type": "users"}}
        try:
            destination_client.delete(ROLES_PATH.format(role_id), payload)
        except HTTPError as e:
            self.logger.error("error removing user: %s from role %s: %s", user_id, role_id, e)
