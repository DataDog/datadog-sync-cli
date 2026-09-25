# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import json
import logging
from re import Pattern, DOTALL, compile

import click

from datadog_sync.constants import LOGGER_NAME
from typing import Dict, List, Mapping, Union


FILTER_TYPE_KEY = "Type"
FILTER_NAME_KEY = "Name"
FILTER_VALUE_KEY = "Value"
FILTER_OPERATOR_KEY = "Operator"
SUBSTRING_OPERATOR = "substring"
EXACT_MATCH_OPERATOR = "exactmatch"
NOT_OPERATOR = "not"
REQUIRED_KEYS = [FILTER_TYPE_KEY, FILTER_NAME_KEY, FILTER_VALUE_KEY]

log = logging.getLogger(LOGGER_NAME)

FilterInput = Union[str, Mapping[str, str]]

FILTER_FILE_REQUIRED_STRING_FIELDS = ("type", "name", "value")


class Filter:
    def __init__(self, resource_type: str, attr_name: str, attr_re: Pattern, operator: str):
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
            return len(list(filter(lambda attr: self.attr_re.match(str(attr)), value))) > 0

        if isinstance(value, bool):
            # Match json bool [true, false]
            return self.attr_re.match(str(value).lower()) is not None

        return self.attr_re.match(str(value)) is not None


def _filter_dict(value: FilterInput) -> Dict[str, str]:
    """Normalize a single --filter entry (legacy ``;``-delimited string, or a
    filter-file JSON object) into the internal filter dict keyed by
    FILTER_TYPE_KEY/FILTER_NAME_KEY/FILTER_VALUE_KEY/FILTER_OPERATOR_KEY.

    Raises ValueError for a malformed legacy string (mirrors the previous
    dict([option.split("=", 1)]) behavior), so callers can log-and-skip the
    same way as before.
    """
    if isinstance(value, Mapping):
        return {
            FILTER_TYPE_KEY: value.get("type", ""),
            FILTER_NAME_KEY: value.get("name", ""),
            FILTER_VALUE_KEY: value.get("value", ""),
            FILTER_OPERATOR_KEY: value.get("operator") or EXACT_MATCH_OPERATOR,
        }
    result: Dict[str, str] = {}
    for option in value.strip("; ").split(";"):
        key, item = option.split("=", 1)
        result[key] = item
    return result


def load_filter_file(file_obj) -> List[Dict[str, str]]:
    """Parse --filter-file contents (a JSON array of filter objects).

    Each entry must be a JSON object with string `type`, `name`, `value`
    fields and an optional string `operator` field. Raises click.UsageError
    on malformed JSON, a non-array top level, or an entry missing/mistyping
    those fields.
    """
    raw = file_obj.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise click.UsageError(f"--filter-file: malformed JSON: {e}")
    if not isinstance(data, list):
        raise click.UsageError("--filter-file: expected a JSON array of filter objects")

    entries: List[Dict[str, str]] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise click.UsageError(f"--filter-file: entry {i} must be a JSON object")
        for field_name in FILTER_FILE_REQUIRED_STRING_FIELDS:
            field_value = item.get(field_name)
            if not isinstance(field_value, str):
                raise click.UsageError(f"--filter-file: entry {i} missing/invalid required string field {field_name!r}")
            if not field_value.strip():
                raise click.UsageError(f"--filter-file: entry {i} required field {field_name!r} must not be empty")
        operator = item.get("operator")
        if operator is not None and not isinstance(operator, str):
            raise click.UsageError(f"--filter-file: entry {i} field 'operator' must be a string")
        entries.append(item)
    return entries


def process_filters(filter_list: List[FilterInput]) -> Dict[str, List[Filter]]:
    filters: Dict[str, List[Filter]] = {}

    if not filter_list:
        return filters

    for _filter in filter_list:
        try:
            f_dict = _filter_dict(_filter)
        except ValueError:
            log.warning("invalid filter option in filter: %s", _filter)
            continue

        # Check if required keys are present:
        invalid_filter = False
        for k in REQUIRED_KEYS:
            if k not in f_dict:
                log.warning("invalid filter missing key %s in filter: %s", k, _filter)
                invalid_filter = True
                break
        if invalid_filter:
            continue

        # Default to EXACT_MATCH_OPERATOR for backward compatibility. This behavior will be removed in the
        # future as it can already be achieved using regex in the Value.
        if not f_dict.get(FILTER_OPERATOR_KEY):
            f_dict[FILTER_OPERATOR_KEY] = EXACT_MATCH_OPERATOR
            log.warning(
                "Defaulting to filter Operator `ExactMatch'. Please ensure the filter Value provided is "
                + "updated as this behavior will be removed in the future. See the official README.md "
                + "for more information."
            )

        # Build and assign regex matcher to VALUE key for the deprecated Operators
        if f_dict[FILTER_OPERATOR_KEY].lower() in [SUBSTRING_OPERATOR, EXACT_MATCH_OPERATOR]:
            handle_deprecated_operator(f_dict)

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
            f_dict[FILTER_OPERATOR_KEY].lower(),
        )
        if f_instance.resource_type not in filters:
            filters[f_instance.resource_type] = []

        filters[f_instance.resource_type].append(f_instance)

    return filters


def handle_deprecated_operator(f_dict):
    # We are keeping this for backwards compatiblity. In the future this will be removed as the user can already
    # acheive substring behavior using regex

    operator_lower = f_dict[FILTER_OPERATOR_KEY].lower()
    reg_exp = f_dict[FILTER_VALUE_KEY]

    if operator_lower == SUBSTRING_OPERATOR:
        reg_exp = f".*{f_dict[FILTER_VALUE_KEY]}.*"
        log.warning(
            "The Filter Operator `SubString` will be removed in future versions, please refer to the Best \
            Practices Section in our README.md for more information."
        )

    elif operator_lower == EXACT_MATCH_OPERATOR:
        reg_exp = f"^{f_dict[FILTER_VALUE_KEY]}$"

    f_dict[FILTER_VALUE_KEY] = reg_exp
