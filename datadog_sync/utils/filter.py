import logging

from datadog_sync.constants import LOGGER_NAME


FILTER_TYPE = "Type"
FILTER_NAME = "Name"
FILTER_VALUE = "Value"

log = logging.getLogger(LOGGER_NAME)


class Filter:
    def __init__(self, filter_list):
        self.filters = dict()

        if len(filter_list) == 0:
            return
        self._process_filters(filter_list)

    def is_applicable(self, resource_type, resource):
        if resource_type not in self.filters:
            return True

        for f_obj in self.filters[resource_type]:
            pass

    def _process_filters(self, filter_list):
        for _filter in filter_list:
            self._process_filter(_filter)

    def _process_filter(self, _filter):
        try:
            f_dict = dict(f.split("=", 1) for f in _filter.split(";"))
        except ValueError:
            log.warning("invalid filter: %s", _filter)
            return

        if not {FILTER_TYPE, FILTER_NAME, FILTER_VALUE} <= set(f_dict):
            log.warning("invalid filter: %s", _filter)
            return

        resource = f_dict[FILTER_TYPE].lower()
        f_dict.pop(FILTER_TYPE)
        if resource not in self.filters:
            self.filters[resource] = list()

        self.filters[resource].append(f_dict)
