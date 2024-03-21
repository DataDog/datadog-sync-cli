# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import asyncio
import time
import logging
import platform
from dataclasses import dataclass
from typing import Awaitable, Dict, Optional, Callable

import aiohttp

from datadog_sync.constants import LOGGER_NAME
from datadog_sync.utils.resource_utils import CustomClientHTTPError

log = logging.getLogger(LOGGER_NAME)


def request_with_retry(func: Awaitable) -> Awaitable:
    async def wrapper(*args, **kwargs):
        retry = True
        default_backoff = 5
        retry_count = 0
        timeout = time.time() + args[0].retry_timeout
        resp = None

        while retry and timeout > time.time():
            try:
                resp = await func(*args, **kwargs)
                resp.raise_for_status()
                retry = False
            except aiohttp.ClientResponseError as e:
                if e.status == 429 and "x-ratelimit-reset" in e.headers:
                    try:
                        sleep_duration = int(e.headers["x-ratelimit-reset"])
                    except ValueError:
                        sleep_duration = retry_count * default_backoff
                        retry_count += 1
                    if (sleep_duration + time.time()) > timeout:
                        log.debug("retry timeout has or will exceed timeout duration")
                        raise CustomClientHTTPError(e)
                    await asyncio.sleep(sleep_duration)
                    continue
                elif e.status >= 500 or e.status == 429 or e.status == 403:
                    sleep_duration = retry_count * default_backoff
                    if (sleep_duration + time.time()) > timeout:
                        log.debug("retry timeout has or will exceed timeout duration")
                        raise CustomClientHTTPError(e)
                    await asyncio.sleep(retry_count * default_backoff)
                    retry_count += 1
                    continue
                raise CustomClientHTTPError(e)
        return await resp.json()

    return wrapper


class CustomClient:
    def __init__(self, host: Optional[str], auth: Dict[str, str], retry_timeout: int, timeout: int) -> None:
        self.host = host
        self.timeout = timeout
        self.session = None
        self.retry_timeout = retry_timeout
        self.default_pagination = PaginationConfig()
        self.auth = auth

    async def _init_session(self):
        self.session = aiohttp.ClientSession()
        self.session.headers.update(build_default_headers(self.auth))

    async def _end_session(self):
        try:
            await self.session.close()
        except Exception:
            pass

    @request_with_retry
    async def get(self, path, **kwargs):
        url = self.host + path
        return await self.session.get(url, timeout=self.timeout, **kwargs)

    @request_with_retry
    async def post(self, path, body, **kwargs):
        url = self.host + path
        return await self.session.post(url, json=body, timeout=self.timeout, **kwargs)

    @request_with_retry
    async def put(self, path, body, **kwargs):
        url = self.host + path
        return await self.session.put(url, json=body, timeout=self.timeout, **kwargs)

    @request_with_retry
    async def patch(self, path, body, **kwargs):
        url = self.host + path
        return await self.session.patch(url, json=body, timeout=self.timeout, **kwargs)

    @request_with_retry
    async def delete(self, path, body=None, **kwargs):
        url = self.host + path
        return await self.session.delete(url, json=body, timeout=self.timeout, **kwargs)

    def paginated_request(self, func: Awaitable) -> Awaitable:
        async def wrapper(*args, **kwargs):
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

                resp = await func(*args, **kwargs)

                resp_len = 0
                if pagination_config.response_list_accessor:
                    resources.extend(resp[pagination_config.response_list_accessor])
                    resp_len = len(resp[pagination_config.response_list_accessor])
                else:
                    resources.extend(resp)
                    resp_len = len(resp)

                if resp_len < page_size:
                    break

                remaining = pagination_config.remaining_func(idx, resp, page_size, page_number)
                page_number = pagination_config.page_number_func(idx, page_size, page_number)
                idx += 1
            return resources

        return wrapper


def build_default_headers(auth_obj: Dict[str, str]) -> Dict[str, str]:
    headers = {
        "DD-API-KEY": auth_obj["apiKeyAuth"],
        "DD-APPLICATION-KEY": auth_obj["appKeyAuth"],
        "Content-Type": "application/json",
        "User-Agent": _get_user_agent(),
    }
    return headers


def _get_user_agent() -> str:
    try:
        from datadog_sync.version import __version__ as version
    except (ModuleNotFoundError, ImportError):
        version = None

    return "datadog-sync-cli/{version} (python {pyver}; os {os}; arch {arch})".format(
        version=version,
        pyver=platform.python_version(),
        os=platform.system().lower(),
        arch=platform.machine().lower(),
    )


def remaining_func(idx, resp, page_size, page_number):
    return resp["meta"]["page"]["total_count"] - page_size * (page_number + 1)


def page_number_func(idx, page_size, page_number):
    return page_number + 1


@dataclass
class PaginationConfig(object):
    page_size: Optional[int] = 100
    page_size_param: Optional[str] = "page[size]"
    page_number: Optional[int] = 0
    page_number_param: Optional[str] = "page[number]"
    remaining_func: Optional[Callable] = remaining_func
    page_number_func: Optional[Callable] = page_number_func
    response_list_accessor: Optional[str] = "data"
