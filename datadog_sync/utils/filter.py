# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import logging
from re import match

from datadog_sync.constants import LOGGER_NAME
from typing import Dict, List


FILTER_TYPE_KEY = "Type"
FILTER_NAME_KEY = "Name"
FILTER_VALUE_KEY = "Value"
FILTER_OPERATOR_KEY = "Operator"
SUBSTRING_OPERATOR = "substring"
EXACT_MATCH_OPERATOR = "exactmatch"
NOT_OPERATOR = "not"
REQUIRED_KEYS = [FILTER_TYPE_KEY, FILTER_NAME_KEY, FILTER_VALUE_KEY]

log = logging.getLogger(LOGGER_NAME)


class Filter:
    def __init__(self, resource_type: str, attr_name: str, attr_re: str, operator: str):
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
            return len(list(filter(lambda attr: match(self.attr_re, str(attr)), value))) > 0

        if isinstance(value, bool):
            # Match json bool [true, false]
            return match(self.attr_re, str(value).lower()) is not None

        return match(self.attr_re, str(value)) is not None


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

        if not f_dict.get(FILTER_OPERATOR_KEY):
            f_dict[FILTER_OPERATOR_KEY] = EXACT_MATCH_OPERATOR
            log.warning("Defaulting the Filter Operator to `ExactMatch` will be removed in the future")

        # Build and assign regex matcher to VALUE key
        f_dict[FILTER_VALUE_KEY] = build_regex(f_dict)

        f_instance = Filter(
            f_dict[FILTER_TYPE_KEY].lower(),
            f_dict[FILTER_NAME_KEY],
            f_dict[FILTER_VALUE_KEY],
            f_dict[FILTER_OPERATOR_KEY].lower(),
        )
        if f_instance.resource_type not in filters:
            filters[f_instance.resource_type] = []

        filters[f_instance.resource_type].append(f_instance)

    return filters


def build_regex(f_dict):
    # We are keeping this for backwards compatiblity. In the future this will be removed as the user can already 
    # acheive substring behavior using regex

    if FILTER_OPERATOR_KEY in f_dict and f_dict[FILTER_OPERATOR_KEY].lower() == SUBSTRING_OPERATOR:
        reg_exp = f".*{f_dict[FILTER_VALUE_KEY]}.*"
        log.warning("The Filter Operator `SubString` will be removed in future versions, please refer to the Best Practices Section in our README.md for more information.")
    else:
        reg_exp = f"^{f_dict[FILTER_VALUE_KEY]}$"

    return reg_exp
