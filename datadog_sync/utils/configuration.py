class Configuration(object):
    def __init__(self, logger=None, source_client=None, destination_client=None, resources=None, filters=None, import_existing=None):
        self.logger = logger
        self.source_client = source_client
        self.destination_client = destination_client
        self.resources = resources
        self.import_existing = import_existing
        self.filters = filters
