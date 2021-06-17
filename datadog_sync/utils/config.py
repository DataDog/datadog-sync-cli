from datadog_sync.utils.log import Log
from datadog_sync.utils.custom_client import CustomClient


class Configuration(object):
    def __init__(self, logger=None, source_client=None, destination_client=None, resources=None):
        self.logger = logger
        self.source_client = source_client
        self.destination_client = destination_client
        self.resources = resources


def create_auth_obj(api_key, app_key):
    return {
        "apiKeyAuth": api_key,
        "appKeyAuth": app_key,
    }
