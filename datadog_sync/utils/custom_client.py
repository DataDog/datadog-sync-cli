# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import asyncio
from datetime import datetime
import ssl
import time
import logging
import platform
from dataclasses import dataclass
from typing import Awaitable, Dict, List, Optional, Callable
from urllib.parse import urlparse

import aiohttp
import certifi

from datadog_sync.constants import DDR_Status, LOGGER_NAME, Metrics
from datadog_sync.utils.resource_utils import CustomClientHTTPError

log = logging.getLogger(LOGGER_NAME)


def request_with_retry(func: Awaitable) -> Awaitable:
    async def wrapper(*args, **kwargs):
        retry = True
        default_backoff = 5
        retry_count = 0
        timeout = time.time() + args[0].retry_timeout
        err_text = None

        while retry and timeout > time.time():
            async with await func(*args, **kwargs) as resp:
                err_text = await resp.text()
                try:
                    resp.raise_for_status()
                    try:
                        return await resp.json()
                    except aiohttp.ContentTypeError:
                        return await resp.text()
                except aiohttp.ClientResponseError as e:
                    if e.status == 429 and "x-ratelimit-reset" in e.headers:
                        try:
                            sleep_duration = int(e.headers["x-ratelimit-reset"])
                        except ValueError:
                            sleep_duration = retry_count * default_backoff
                        if (sleep_duration + time.time()) > timeout:
                            log.debug(f"{e}. retry timeout has or will exceed timeout duration")
                            raise CustomClientHTTPError(e, message=err_text)
                        log.debug(f"{e}. retrying request after {sleep_duration}s")
                        await asyncio.sleep(sleep_duration)
                        retry_count += 1
                        continue
                    elif e.status >= 500 or e.status == 429:
                        sleep_duration = retry_count * default_backoff
                        if (sleep_duration + time.time()) > timeout:
                            log.debug("retry timeout has or will exceed timeout duration")
                            raise CustomClientHTTPError(e, message=err_text)
                        log.debug(f"{e}. retrying request after {sleep_duration}s")
                        await asyncio.sleep(retry_count * default_backoff)
                        retry_count += 1
                        continue
                    raise CustomClientHTTPError(e, message=err_text)
        raise Exception("retry timeout has reached. Last error: " + err_text)

    return wrapper


class CustomClient:
    def __init__(
        self,
        host: Optional[str],
        auth: Dict[str, str],
        retry_timeout: int,
        timeout: int,
        send_metrics: bool,
    ) -> None:
        self.url_object = UrlObject.from_str(host)
        self.timeout = timeout
        self.session = None
        self.retry_timeout = retry_timeout
        self.default_pagination = PaginationConfig()
        self.auth = auth
        self.send_metrics = send_metrics

    async def _init_session(self):
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context))
        self.session.headers.update(build_default_headers(self.auth))

    async def _end_session(self):
        try:
            await self.session.close()
        except Exception:
            pass

    @request_with_retry
    async def get(self, path, domain=None, subdomain=None, **kwargs):
        url = self.url_object.build_url(path, domain=domain, subdomain=subdomain)
        return self.session.get(url, timeout=self.timeout, **kwargs)

    @request_with_retry
    async def post(self, path, body, domain=None, subdomain=None, **kwargs):
        url = self.url_object.build_url(path, domain=domain, subdomain=subdomain)
        return self.session.post(url, json=body, timeout=self.timeout, **kwargs)

    @request_with_retry
    async def put(self, path, body, domain=None, subdomain=None, **kwargs):
        url = self.url_object.build_url(path, domain=domain, subdomain=subdomain)
        return self.session.put(url, json=body, timeout=self.timeout, **kwargs)

    @request_with_retry
    async def patch(self, path, body, domain=None, subdomain=None, **kwargs):
        url = self.url_object.build_url(path, domain=domain, subdomain=subdomain)
        return self.session.patch(url, json=body, timeout=self.timeout, **kwargs)

    @request_with_retry
    async def delete(self, path, domain=None, subdomain=None, body=None, **kwargs):
        url = self.url_object.build_url(path, domain=domain, subdomain=subdomain)
        return self.session.delete(url, json=body, timeout=self.timeout, **kwargs)

    def paginated_request(self, func: Awaitable) -> Awaitable:
        async def wrapper(*args, **kwargs):
            pagination_config = kwargs.pop("pagination_config", self.default_pagination)

            page_size = pagination_config.page_size
            page_number = pagination_config.page_number
            remaining = 1
            resources = []
            kwargs["params"] = kwargs.get("params", {}) or {}
            idx = 0
            original_page_size = page_size
            restore_page_size = False
            resources_attempted = 0
            saved_idx = idx
            save_idx = True
            while remaining > 0:
                log.debug(
                    f"fetching {args[0]} "
                    f"{pagination_config.page_number_param}: {page_number} "
                    f"{pagination_config.page_size_param}: {page_size} "
                    f"idx: {idx} "
                    f"remaining: {remaining}"
                )
                remaining = 0
                params = {
                    pagination_config.page_size_param: page_size,
                    pagination_config.page_number_param: page_number,
                }
                kwargs["params"].update(params)

                try:
                    # call the actual awaitable function
                    resp = await func(*args, **kwargs)
                    resp_len = 0

                    # add resources from the page to our list
                    if pagination_config.response_list_accessor:
                        resources.extend(resp[pagination_config.response_list_accessor])
                        resp_len = len(resp[pagination_config.response_list_accessor])
                    else:
                        resources.extend(resp)
                        resp_len = len(resp)

                    # if it's a partial page then we're done, it's the last page
                    if resp_len < page_size:
                        break

                    # restore the page size if we had to lower it to deal w/ a bad resource return
                    resources_attempted += resp_len
                    if restore_page_size:
                        if resources_attempted % original_page_size == 0:
                            page_size = original_page_size
                            page_number = pagination_config.page_number_func(idx, page_size, page_number)
                            restore_page_size = False
                            idx = saved_idx
                            save_idx = True

                    remaining = pagination_config.remaining_func(idx, resp, page_size, page_number)
                except CustomClientHTTPError as err:
                    if err.status_code >= 500:
                        log.warning("500 error during a paginated request, attempting to isolate")

                        # save the index so we can come back to it after dealing with this batch
                        if save_idx:
                            saved_idx = idx
                            save_idx = False

                        # we're in the except and our page size is 1, we've found the bad resources
                        error_handled = False
                        if page_size == 1:
                            log.warning("Error isolated, skipping resource:")
                            log.warning(
                                f"Fetching {args[0]} "
                                f"{pagination_config.page_number_param}: {page_number} "
                                f"{pagination_config.page_size_param}: {page_size} "
                            )
                            resources_attempted += 1
                            error_handled = True
                        else:
                            remaining = 1  # keep going

                        if error_handled:
                            restore_page_size = True
                        else:
                            # reduce the page size by 50% to isolate the bad resource
                            new_page_size = page_size // 2
                            # page size can't be 0
                            if new_page_size == 0:
                                new_page_size = 1
                            # to start on the right page number the resources we've attempted so far
                            # need to be evenly divisible by the page_size
                            while resources_attempted % new_page_size != 0:
                                new_page_size -= 1

                            # set the page_size, idx, and page_number in that order
                            page_size = new_page_size
                            idx = resources_attempted // page_size - 1
                            page_number = pagination_config.page_number_func(idx, page_size, page_number)

                # made it through the try/except no increase the page number and idx
                page_number = pagination_config.page_number_func(idx, page_size, page_number)
                idx += 1

            # return our list of good resources
            return resources

        return wrapper

    async def send_metric(self, metric: str, tags: List[str] = None) -> None:
        if not self.send_metrics:
            return None
        path = "/api/v2/series"
        timestamp = int(datetime.now().timestamp())
        full_metric = f"{Metrics.PREFIX.value}.{metric}"
        body = {
            "series": [
                {
                    "metadata": {
                        "origin": {
                            "origin_product": Metrics.ORIGIN_PRODUCT.value,
                        },
                    },
                    "metric": full_metric,
                    "type": 0,
                    "points": [{"timestamp": timestamp, "value": 1}],
                    "tags": tags,
                }
            ]
        }
        await self.post(path, body)

    async def get_ddr_status(self) -> Dict:
        path = "/api/v2/hamr"
        resp = await self.get(path)
        if not resp:
            return None

        data = resp.get("data")
        if not data:
            return None

        attributes = data.get("attributes")
        if not attributes:
            return None

        ddr_status = attributes.get("HamrStatus")
        if not ddr_status:
            return None

        return DDR_Status(ddr_status)


def build_default_headers(auth_obj: Dict[str, str]) -> Dict[str, str]:
    headers = {
        "DD-API-KEY": auth_obj.get("apiKeyAuth", ""),
        "DD-APPLICATION-KEY": auth_obj.get("appKeyAuth", ""),
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


@dataclass
class UrlObject(object):
    protocol: str = ""
    domain: str = ""
    subdomain: str = ""
    _default: str = ""

    @classmethod
    def from_str(cls, url: str):
        if url:
            parsed_url = urlparse(url)
            domain = parsed_url.netloc
            subdomain = None

            if parsed_url.netloc.count(".") >= 2:
                res = parsed_url.netloc.split(".")
                domain = ".".join(res[-2:])
                subdomain = ".".join(res[:-2])

            return cls(
                protocol=parsed_url.scheme,
                domain=domain,
                subdomain=subdomain,
                _default=url,
            )
        return cls()

    def build_url(
        self,
        path,
        protocol: Optional[str] = None,
        domain: Optional[str] = None,
        subdomain: Optional[str] = None,
    ) -> str:
        if all(arg is None for arg in (protocol, domain, subdomain)):
            return self._default + path

        # Rebuild the URL with the new values
        url = ""
        if protocol is not None:
            url += f"{protocol}://" if protocol else ""
        elif self.protocol:
            url += f"{self.protocol}://" if self.protocol else ""

        if subdomain is not None:
            url += f"{subdomain}." if subdomain else ""
        elif self.subdomain:
            url += f"{self.subdomain}." if self.subdomain else ""

        if domain is not None:
            url += f"{domain}"
        else:
            url += f"{self.domain}"

        return url + path
