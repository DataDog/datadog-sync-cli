# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.
import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import ssl
import time
import logging
import platform
from dataclasses import dataclass
from typing import Awaitable, Dict, List, Optional, Callable
from urllib.parse import urlparse

import aiohttp
import certifi

from collections import Counter as _StdCounter
from datadog_sync.constants import DDR_Status, LOGGER_NAME, Metrics
from datadog_sync.utils.resource_utils import CustomClientHTTPError

log = logging.getLogger(LOGGER_NAME)


# HTTP statuses that signal upstream/edge overload rather than app-level failure.
# For these, hammering with tight-cycle retries makes the situation worse (the
# same request queue is still full). Use a longer, jittered exponential backoff
# that respects the Retry-After header when present.
_OVERLOAD_STATUSES = frozenset({502, 503, 504, 512})
# Jittered exponential backoff schedule for _OVERLOAD_STATUSES, in seconds.
# Index by retry_count. Falls off the end -> caller gives up. Matches the
# observed proxy queue drain time on the destination monitor API during the
# HAMR-392 storm (see PR body).
_OVERLOAD_BACKOFF_SCHEDULE = (30, 60, 120)


def _overload_sleep_duration(retry_count: int, retry_after_hdr: Optional[str]) -> int:
    """Return the number of seconds to wait before retrying an _OVERLOAD_STATUSES
    response. Honors Retry-After if present; otherwise falls back to
    _OVERLOAD_BACKOFF_SCHEDULE indexed by retry_count.
    """
    if retry_after_hdr:
        # Retry-After per RFC 7231 can be either a delta-seconds integer or an
        # HTTP-date. Try numeric first (covers integer and fractional forms
        # some servers send), then fall back to HTTP-date parsing.
        try:
            return max(0, int(float(retry_after_hdr)))
        except (TypeError, ValueError):
            pass
        try:
            when = parsedate_to_datetime(retry_after_hdr)
            if when is not None:
                # parsedate_to_datetime returns naive on ambiguous input; treat
                # naive as UTC per RFC 7231. Compute seconds-until-when,
                # clamped to 0 (past dates -> no wait).
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                delta = (when - datetime.now(timezone.utc)).total_seconds()
                return max(0, int(delta))
        except (TypeError, ValueError):
            pass
    idx = min(retry_count, len(_OVERLOAD_BACKOFF_SCHEDULE) - 1)
    return _OVERLOAD_BACKOFF_SCHEDULE[idx]


def request_with_retry(func: Awaitable) -> Awaitable:
    async def wrapper(*args, **kwargs):
        retry = True
        default_backoff = 5
        retry_count = 0
        timeout = time.time() + args[0].retry_timeout
        err_text = None
        max_retries = 3

        while retry and timeout > time.time() and retry_count <= max_retries:
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
                            log.warning(f"{e}. retry timeout has or will exceed timeout duration")
                            raise CustomClientHTTPError(e, message=err_text)
                        log.warning(f"{e}. retrying request after {sleep_duration}s")
                        await asyncio.sleep(sleep_duration)
                        retry_count += 1
                        log.debug(f"retry count: {retry_count}")
                        continue
                    elif e.status in _OVERLOAD_STATUSES:
                        # Edge/proxy timeouts and gateway overload: back off
                        # substantially longer than the 5s * retry_count
                        # baseline used for other 5xx. Honor Retry-After if the
                        # server sent one; otherwise use the fixed schedule.
                        # Increment the per-status counter so operators can
                        # dashboard "is the fix working?" long-term via
                        # client.overload_status_counter, rather than
                        # log-grep.
                        try:
                            args[0].overload_status_counter[e.status] += 1
                        except AttributeError:
                            # Older CustomClient instances or test doubles.
                            pass
                        # Cap at the schedule length rather than max_retries.
                        # This keeps every bucket in _OVERLOAD_BACKOFF_SCHEDULE
                        # reachable: with a 3-element schedule and retry_count
                        # starting at 0, we sleep schedule[0], schedule[1],
                        # schedule[2] on successive retries before giving up
                        # (subject to the retry_timeout total budget).
                        if retry_count >= len(_OVERLOAD_BACKOFF_SCHEDULE):
                            log.warning("retry count has exceeded overload backoff schedule length")
                            raise CustomClientHTTPError(e, message=err_text)
                        sleep_duration = _overload_sleep_duration(retry_count, e.headers.get("Retry-After"))
                        if (sleep_duration + time.time()) > timeout:
                            log.warning(
                                f"{e}. overload backoff ({sleep_duration}s) exceeds retry timeout budget; giving up"
                            )
                            raise CustomClientHTTPError(e, message=err_text)
                        log.warning(f"{e}. upstream overloaded; backing off {sleep_duration}s before retry")
                        await asyncio.sleep(sleep_duration)
                        retry_count += 1
                        log.debug(f"retry count: {retry_count}")
                        continue
                    elif e.status >= 500 or e.status == 429:
                        sleep_duration = retry_count * default_backoff
                        if (sleep_duration + time.time()) > timeout:
                            log.warning("retry timeout has or will exceed timeout duration")
                            raise CustomClientHTTPError(e, message=err_text)
                        if retry_count + 1 >= max_retries:
                            log.warning("retry count has or will exceed retry maximum")
                            raise CustomClientHTTPError(e, message=err_text)
                        log.warning(f"{e}. retrying request after {sleep_duration}s")
                        await asyncio.sleep(retry_count * default_backoff)
                        retry_count += 1
                        log.debug(f"retry count: {retry_count}")
                        continue
                    raise CustomClientHTTPError(e, message=err_text)
        raise Exception(f"retry limit exceeded timeout: {timeout} retry_count: {retry_count} error: {err_text}")

    return wrapper


class CustomClient:
    def __init__(
        self,
        host: Optional[str],
        auth: Dict[str, str],
        retry_timeout: int,
        timeout: int,
        send_metrics: bool,
        verify_ssl: bool = True,
    ) -> None:
        self.url_object = UrlObject.from_str(host)
        self.timeout = timeout
        self.session = None
        self.retry_timeout = retry_timeout
        self.default_pagination = PaginationConfig()
        self.auth = auth
        self.send_metrics = send_metrics
        self.verify_ssl = verify_ssl
        # Per-status counter of upstream-overload retries (see _OVERLOAD_STATUSES).
        # Exposed so callers (or a follow-up drainer coroutine) can emit a
        # metric like sync_cli.overload_status_encountered{status=512}. Kept as
        # an in-memory counter rather than emitting a metric inline to avoid
        # recursive-request risk during a proxy overload storm.
        self.overload_status_counter: _StdCounter = _StdCounter()

        # Metrics only work with API keys, not JWT
        # If JWT is present, metrics are not available
        self.metrics_available = bool(
            self.send_metrics
            and not auth.get("jwtAuth")  # JWT means no metrics
            and auth.get("apiKeyAuth")
            and auth.get("appKeyAuth")
        )

    async def _init_session(self):
        if self.verify_ssl:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context))
        else:
            log.warning(
                "WARNING: SSL certificate verification is disabled. "
                "This is insecure and should only be used in trusted environments."
            )
            self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))

        headers = build_default_headers(self.auth)
        self.session.headers.update(headers)

        # Log authentication configuration
        auth_method = "JWT" if "dd-auth-jwt" in headers else "API Keys"
        log.info(f"Initialized HTTP session with {auth_method} authentication for {self.url_object._default}")
        log.info(f"Session headers configured: {', '.join(headers.keys())}")

        # Log metrics availability
        if self.send_metrics:
            if self.metrics_available:
                log.info("Metrics enabled: Using API keys for /api/v2/series endpoint")
            else:
                log.warning(
                    "Metrics disabled: /api/v2/series endpoint requires DD-API-KEY and DD-APPLICATION-KEY headers. "
                    "Provide --source-api-key/--source-app-key to enable metrics."
                )

    async def _end_session(self):
        try:
            await self.session.close()
        except Exception:
            pass

    def _client_timeout(self) -> aiohttp.ClientTimeout:
        """Return a ClientTimeout with no hard total cap but a per-read socket deadline.

        ``total=None`` preserves the existing behaviour for slow-streaming responses
        where the body arrives progressively over a long period — those reads are
        unaffected because each individual socket read completes within the deadline.

        ``sock_read=self.timeout`` adds a per-chunk gap detector: if the server
        stops sending data mid-body for ``self.timeout`` seconds, an
        ``asyncio.TimeoutError`` is raised instead of blocking indefinitely.
        """
        return aiohttp.ClientTimeout(total=None, sock_read=self.timeout)

    @request_with_retry
    async def get(self, path, domain=None, subdomain=None, **kwargs):
        url = self.url_object.build_url(path, domain=domain, subdomain=subdomain)
        return self.session.get(url, timeout=self._client_timeout(), **kwargs)

    @request_with_retry
    async def post(self, path, body, domain=None, subdomain=None, **kwargs):
        url = self.url_object.build_url(path, domain=domain, subdomain=subdomain)
        return self.session.post(url, json=body, timeout=self._client_timeout(), **kwargs)

    @request_with_retry
    async def put(self, path, body, domain=None, subdomain=None, **kwargs):
        url = self.url_object.build_url(path, domain=domain, subdomain=subdomain)
        return self.session.put(url, json=body, timeout=self._client_timeout(), **kwargs)

    @request_with_retry
    async def patch(self, path, body, domain=None, subdomain=None, **kwargs):
        url = self.url_object.build_url(path, domain=domain, subdomain=subdomain)
        return self.session.patch(url, json=body, timeout=self._client_timeout(), **kwargs)

    @request_with_retry
    async def delete(self, path, domain=None, subdomain=None, body=None, **kwargs):
        url = self.url_object.build_url(path, domain=domain, subdomain=subdomain)
        return self.session.delete(url, json=body, timeout=self._client_timeout(), **kwargs)

    @request_with_retry
    async def _post_raw(self, session: aiohttp.ClientSession, url: str, body: dict):
        return session.post(url, json=body, timeout=self._client_timeout())

    async def post_unauthenticated(self, url: str, payload: dict) -> None:
        ssl_ctx = ssl.create_default_context(cafile=certifi.where()) if self.verify_ssl else False
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=ssl_ctx),
            headers={"Content-Type": "application/json", "User-Agent": _get_user_agent()},
        ) as session:
            await self._post_raw(session, url, payload)

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
                        remaining = 1  # after this error we need to keep processing
                    else:
                        log.error(
                            f"Error during paginated request for {args[0]} "
                            f"{pagination_config.page_number_param}: {page_number} "
                            f"{pagination_config.page_size_param}: {page_size} - {err}"
                        )
                        break

                # made it through the try/except no increase the page number and idx
                page_number = pagination_config.page_number_func(idx, page_size, page_number)
                idx += 1

            # return our list of good resources
            return resources

        return wrapper

    async def send_metric(self, metric: str, tags: List[str] = None) -> None:
        if not self.send_metrics:
            return None

        # Skip if using JWT (metrics endpoint doesn't support JWT)
        if not self.metrics_available:
            log.debug(
                f"Skipping metric '{metric}': /api/v2/series endpoint requires API key authentication. "
                "Currently using JWT authentication which is not supported by this endpoint."
            )
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
        # Send metric using API key headers from session
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
        "Content-Type": "application/json",
        "User-Agent": _get_user_agent(),
    }

    # JWT takes precedence over API keys
    if jwt := auth_obj.get("jwtAuth"):
        headers["dd-auth-jwt"] = jwt
        log.info(f"JWT authentication configured - JWT present: {bool(jwt)}, JWT length: {len(jwt) if jwt else 0}")
        # Log first and last 3 chars for debugging without exposing the full token
        if jwt and len(jwt) > 30:
            log.info(f"JWT preview: {jwt[:3]}...{jwt[-3:]}")
        log.info(f"Headers being set: {list(headers.keys())}")
    else:
        headers["DD-API-KEY"] = auth_obj.get("apiKeyAuth", "")
        headers["DD-APPLICATION-KEY"] = auth_obj.get("appKeyAuth", "")
        api_key_present = bool(auth_obj.get("apiKeyAuth"))
        app_key_present = bool(auth_obj.get("appKeyAuth"))
        log.info(f"API Key auth configured - API Key present: {api_key_present}, App Key present: {app_key_present}")

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
