from datadog_sync.utils.base_resource import BaseResource


RESOURCE_TYPE = "logs_custom_pipeline"


class LogsCustomPipeline(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_TYPE)
