from datadog_sync.model.base_resource import BaseResource


RESOURCE_NAME = "synthetics_test"


class SyntheticsTest(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_NAME)
