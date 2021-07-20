import logging
from re import match

from datadog_sync.constants import LOGGER_NAME


FILTER_TYPE = "Type"
FILTER_NAME = "Name"
FILTER_VALUE = "Value"
FILTER_OPERATOR = "Operator"
SUBSTRING_OPERATOR = "substring"
REQUIRED_KEYS = [FILTER_TYPE, FILTER_NAME, FILTER_VALUE]

log = logging.getLogger(LOGGER_NAME)


class Filter:
    def __init__(self, resource_type, attr_name, attr_re):
        self.resource_type = resource_type
        self.attr_name = attr_name
        self.attr_re = attr_re

    def is_match(self, resource):
        if self.attr_name in resource:
            if isinstance(resource[self.attr_name], list):
                return len(list(filter(lambda attr: match(self.attr_re, str(attr)), resource[self.attr_name]))) > 0
            return match(self.attr_re, str(resource[self.attr_name])) is not None

        return False


def process_filters(filter_list):
    filters = {}

    if not filter_list:
        return filters

    for _filter in filter_list:
        f_dict = {}
        f_list = _filter.strip("; ").split(";")

        for option in f_list:
            try:
                f_dict.update(dict([option.split("=", 1)]))
            except ValueError:
                log.warning("invalid filter option: %s, filter: %s", option, _filter)
                return

        # Check if required keys are present:
        for k in REQUIRED_KEYS:
            if k not in f_dict:
                log.warning("invalid filter missing key %s in filter: %s", k, _filter)
                return

        # Build and assign regex matcher to VALUE key
        f_dict[FILTER_VALUE] = build_regex(f_dict)

        f_instance = Filter(f_dict[FILTER_TYPE].lower(), f_dict[FILTER_NAME], f_dict[FILTER_VALUE])
        if f_instance.resource_type not in filters:
            filters[f_instance.resource_type] = []

        filters[f_instance.resource_type].append(f_instance)

    return filters


def build_regex(f_dict):
    if FILTER_OPERATOR in f_dict and f_dict[FILTER_OPERATOR].lower() == SUBSTRING_OPERATOR:
        reg_exp = f".*{f_dict[FILTER_VALUE]}.*"
    else:
        reg_exp = f"^{f_dict[FILTER_VALUE]}$"

    return reg_exp
