class BaseResource:
    def __init__(self, ctx, resource_name, resource_filter=None):
        self.ctx = ctx
        self.resource_name = resource_name
        self.resource_filter = resource_filter

    def post_import_processing(self):
        """
        This method is ran after resources are imported via terraformer. It is useful for remapping id's or other
        generated values.
        """
        pass
