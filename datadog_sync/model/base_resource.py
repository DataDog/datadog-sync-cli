class BaseResource:
    def __init__(self, ctx, resource_nme):
        self.ctx = ctx
        self.ids = []
        self.resource_name = resource_nme
        self.get_resources()

    def get_resource_ids(self):
        return self.ids

    def post_import_processing(self):
        """
        This method is ran after resources are imported via terraformer. It is useful for remapping id's or other
        generated values.
        """
        pass
