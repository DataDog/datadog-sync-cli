from datadog_api_client.v2 import ApiException
from datadog_api_client.v2.api import users_api, roles_api

from datadog_sync.model.base_resource import BaseResource


RESOURCE_NAME = "user"


class User(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_NAME)
