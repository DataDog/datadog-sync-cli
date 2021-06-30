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
    def __init__(self, filter_list):
        self.filters = dict()

        if len(filter_list) == 0:
            return
        self._process_filters(filter_list)

    def is_applicable(self, resource_type, resource):
        if resource_type not in self.filters:
            return True

        for f_obj in self.filters[resource_type]:
            filter_attr = f_obj[FILTER_NAME]
            filter_val = f_obj[FILTER_VALUE]

            if filter_attr in resource:
                if isinstance(resource[filter_attr], list):
                    return len(list(filter(lambda attr: match(filter_val, str(attr)), resource[filter_attr]))) > 0

                return match(filter_val, str(resource[filter_attr])) is not None

        # Filters for resource were specified but no matching attributes found
        return False

    def _process_filters(self, filter_list):
        for _filter in filter_list:
            self._process_filter(_filter)

    def _process_filter(self, _filter):
        f_dict = {}
        f_list = _filter.split(";")

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

        # Build and assign matcher to VALUE key
        f_dict[FILTER_VALUE] = self.build_regex(f_dict)

        resource = f_dict[FILTER_TYPE].lower()
        f_dict.pop(FILTER_TYPE)
        if resource not in self.filters:
            self.filters[resource] = []

        self.filters[resource].append(f_dict)

    @staticmethod
    def build_regex(f_dict):
        if FILTER_OPERATOR in f_dict and f_dict[FILTER_OPERATOR].lower() == SUBSTRING_OPERATOR:
            reg_exp = f".*{f_dict[FILTER_VALUE]}.*"
        else:
            reg_exp = f"^{f_dict[FILTER_VALUE]}$"

        return reg_exp
