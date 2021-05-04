class BaseResource:
    def __init__(self, ctx, resource_nme):
        self.ctx = ctx
        self.resource_name = resource_nme

    def post_import_processing(self):
        """
        This method is ran after resources are imported via terraformer. It is useful for remapping id's or other
        generated values.
        """
        pass
