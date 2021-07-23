from datadog_sync import models
from datadog_sync.utils.base_resource import BaseResource
from datadog_sync.utils.configuration import get_import_order


str_to_class = dict(
    (cls.resource_type, cls)
    for cls in models.__dict__.values()
    if isinstance(cls, type) and issubclass(cls, BaseResource)
)


all_resources = get_import_order(
    [cls for cls in models.__dict__.values() if isinstance(cls, type) and issubclass(cls, BaseResource)], str_to_class
)
