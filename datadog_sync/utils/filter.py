# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import logging
from re import Pattern, DOTALL, compile

from datadog_sync.constants import LOGGER_NAME
from typing import Dict, List


FILTER_TYPE_KEY = "Type"
FILTER_NAME_KEY = "Name"
FILTER_VALUE_KEY = "Value"
FILTER_OPERATOR_KEY = "Operator"
NOT_OPERATOR = "not"
REQUIRED_KEYS = [FILTER_TYPE_KEY, FILTER_NAME_KEY, FILTER_VALUE_KEY]

log = logging.getLogger(LOGGER_NAME)


class Filter:
    def __init__(self, resource_type: str, attr_name: str, attr_re: Pattern, operator: str = ""):
        self.resource_type = resource_type
        self.attr_name = attr_name.split(".")
        self.attr_re = attr_re
        self.operator = operator

    def is_match(self, resource):
        result = self._is_match_helper(self.attr_name, resource)

        if self.operator == NOT_OPERATOR:
            return not result

        return result

    def _is_match_helper(self, k_list, resource):
        if len(k_list) == 1:
            if k_list[0] in resource:
                return self._is_match(resource[k_list[0]])
            return False
        else:
            if k_list[0] not in resource:
                return False
            if isinstance(resource[k_list[0]], list):
                match = False
                for r in resource[k_list[0]]:
                    if self._is_match_helper(k_list[1:], r):
                        match = True
                        break
                return match

            return self._is_match_helper(k_list[1:], resource[k_list[0]])

    def _is_match(self, value):
        if isinstance(value, list):
            print("hmmmm", list(filter(lambda attr: self.attr_re.match(str(attr)), value)))
            return len(list(filter(lambda attr: self.attr_re.match(str(attr)), value))) > 0

        if isinstance(value, bool):
            # Match json bool [true, false]
            return self.attr_re.match(str(value).lower()) is not None

        return self.attr_re.match(str(value)) is not None


def process_filters(filter_list: List[str]) -> Dict[str, List[Filter]]:
    filters: Dict[str, List[Filter]] = {}

    if not filter_list:
        return filters

    for _filter in filter_list:
        f_dict = {}
        f_list = _filter.strip("; ").split(";")

        invalid_filter = False
        for option in f_list:
            try:
                f_dict.update(dict([option.split("=", 1)]))
            except ValueError:
                log.warning("invalid filter option: %s, filter: %s", option, _filter)
                invalid_filter = True
                break
        if invalid_filter:
            continue

        # Check if required keys are present:
        for k in REQUIRED_KEYS:
            if k not in f_dict:
                log.warning("invalid filter missing key %s in filter: %s", k, _filter)
                invalid_filter = True
                break
        if invalid_filter:
            continue

        try:
            # Compile regex for the filter
            f_dict[FILTER_VALUE_KEY] = compile(f_dict[FILTER_VALUE_KEY], flags=DOTALL)
        except Exception:
            log.warning("invalid regex value for filter: %s", _filter)
            continue

        f_instance = Filter(
            f_dict[FILTER_TYPE_KEY].lower(),
            f_dict[FILTER_NAME_KEY],
            f_dict[FILTER_VALUE_KEY],
            operator=f_dict.get(FILTER_OPERATOR_KEY, "").lower(),
        )
        if f_instance.resource_type not in filters:
            filters[f_instance.resource_type] = []

        filters[f_instance.resource_type].append(f_instance)

    return filters
