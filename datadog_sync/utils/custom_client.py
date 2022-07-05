# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import time
import logging
import platform
from dataclasses import dataclass
from typing import Optional, Callable

import requests

from datadog_sync.constants import LOGGER_NAME
from datadog_sync.utils.resource_utils import CustomClientHTTPError

log = logging.getLogger(LOGGER_NAME)


def request_with_retry(func):
    def wrapper(*args, **kwargs):
        retry = True
        default_backoff = 5
        retry_count = 0
        timeout = time.time() + args[0].retry_timeout
        resp = None

        while retry and timeout > time.time():
            try:
                resp = func(*args, **kwargs)
                resp.raise_for_status()
                retry = False
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code
                if status_code == 429 and "x-ratelimit-reset" in e.response.headers:
                    try:
                        sleep_duration = int(e.response.headers["x-ratelimit-reset"])
                    except ValueError:
                        sleep_duration = retry_count * default_backoff
                        retry_count += 1
                    if (sleep_duration + time.time()) > timeout:
                        log.debug("retry timeout has or will exceed timeout duration")
                        raise CustomClientHTTPError(e.response)
                    time.sleep(sleep_duration)
                    continue
                elif status_code >= 500 or status_code == 429:
                    sleep_duration = retry_count * default_backoff
                    if (sleep_duration + time.time()) > timeout:
                        log.debug("retry timeout has or will exceed timeout duration")
                        raise CustomClientHTTPError(e.response)
                    time.sleep(retry_count * default_backoff)
                    retry_count += 1
                    continue
                raise CustomClientHTTPError(e.response)
        return resp

    return wrapper


class CustomClient:
    def __init__(self, host, auth, retry_timeout):
        self.host = host
        self.timeout = 30
        self.session = requests.Session()
        self.retry_timeout = retry_timeout
        self.session.headers.update(build_default_headers(auth))
        self.default_pagination = PaginationConfig()

    @request_with_retry
    def get(self, path, **kwargs):
        url = self.host + path
        return self.session.get(url, timeout=self.timeout, **kwargs)

    @request_with_retry
    def post(self, path, body, **kwargs):
        url = self.host + path
        return self.session.post(url, json=body, timeout=self.timeout, **kwargs)

    @request_with_retry
    def put(self, path, body, **kwargs):
        url = self.host + path
        return self.session.put(url, json=body, timeout=self.timeout, **kwargs)

    @request_with_retry
    def patch(self, path, body, **kwargs):
        url = self.host + path
        return self.session.patch(url, json=body, timeout=self.timeout, **kwargs)

    @request_with_retry
    def delete(self, path, body, **kwargs):
        url = self.host + path
        return self.session.delete(url, json=body, timeout=self.timeout, **kwargs)

    def paginated_request(self, func):
        def wrapper(*args, **kwargs):
            pagination_config = kwargs.pop("pagination_config", self.default_pagination)

            page_size = pagination_config.page_size
            page_number = pagination_config.page_number
            remaining = 1
            resources = []
            kwargs["params"] = kwargs.get("params", {}) or {}
            idx = 0
            while remaining > 0:
                params = {
                    pagination_config.page_size_param: page_size,
                    pagination_config.page_number_param: page_number,
                }
                kwargs["params"].update(params)

                resp = func(*args, **kwargs)
                resp.raise_for_status()

                resp_json = resp.json()
                resources.extend(resp_json["data"])
                if len(resp_json["data"]) < page_size:
                    remaining = 0
                    continue
                remaining = pagination_config.remaining_func(idx, resp_json, page_size, page_number)
                page_number = pagination_config.page_number_func(idx, page_size, page_number)
                idx += 1
            return resources

        return wrapper


def build_default_headers(auth_obj):
    headers = {
        "DD-API-KEY": auth_obj["apiKeyAuth"],
        "DD-APPLICATION-KEY": auth_obj["appKeyAuth"],
        "Content-Type": "application/json",
        "User-Agent": _get_user_agent(),
    }
    return headers


def _get_user_agent():
    from datadog_sync._version import __version__ as version

    return "datadog-sync-cli/{version} (python {pyver}; os {os}; arch {arch})".format(
        version=version,
        pyver=platform.python_version(),
        os=platform.system().lower(),
        arch=platform.machine().lower(),
    )


@dataclass
class PaginationConfig(object):
    page_size: Optional[int] = 100
    page_size_param: Optional[str] = "page[size]"
    page_number: Optional[int] = 0
    page_number_param: Optional[str] = "page[number]"
    remaining_func: Optional[Callable] = lambda idx, resp, page_size, page_number: (
        resp["meta"]["page"]["total_count"]
    ) - (page_size * (page_number + 1))
    page_number_func: Optional[Callable] = lambda idx, page_size, page_number: page_number + 1
