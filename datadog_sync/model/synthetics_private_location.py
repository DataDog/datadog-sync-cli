from datadog_sync.utils.base_resource import BaseResource


RESOURCE_NAME = "synthetics_private_location"


class SyntheticsPrivateLocation(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_NAME)
