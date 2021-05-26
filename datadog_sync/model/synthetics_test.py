from datadog_sync.utils.base_resource import BaseResource


RESOURCE_TYPE = "synthetics_test"


class SyntheticsTest(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_TYPE)
