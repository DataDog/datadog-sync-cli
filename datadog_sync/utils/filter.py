# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import logging
from re import match

from datadog_sync.constants import LOGGER_NAME
from typing import TYPE_CHECKING, Any, Dict, List, Tuple


FILTER_TYPE = "Type"
FILTER_NAME = "Name"
FILTER_VALUE = "Value"
FILTER_OPERATOR = "Operator"
SUBSTRING_OPERATOR = "substring"
REQUIRED_KEYS = [FILTER_TYPE, FILTER_NAME, FILTER_VALUE]

log = logging.getLogger(LOGGER_NAME)


class Filter:
    def __init__(self, resource_type: str, attr_name: str, attr_re: str):
        self.resource_type = resource_type
        self.attr_name = attr_name.split(".")
        self.attr_re = attr_re

    def is_match(self, resource):
        return self._is_match_helper(self.attr_name, resource)

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

        return match(self.attr_re, str(value)) is not None


def process_filters(filter_list: Tuple[()]) -> Dict[str, List[Filter]]:
    filters: Dict[str, List[Filter]] = {}

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
