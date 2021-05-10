from datadog_sync.model.base_resource import BaseResource


RESOURCE_NAME = "logs_custom_pipeline"


class LogsCustomPipeline(BaseResource):
    def __init__(self, ctx):
        super().__init__(ctx, RESOURCE_NAME)
