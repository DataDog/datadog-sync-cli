# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

import time
import logging

import requests

from datadog_sync.constants import LOGGER_NAME


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
                        raise e
                    time.sleep(sleep_duration)
                    continue
                elif status_code >= 500 or status_code == 429:
                    sleep_duration = retry_count * default_backoff
                    if (sleep_duration + time.time()) > timeout:
                        log.debug("retry timeout has or will exceed timeout duration")
                        raise e
                    time.sleep(retry_count * default_backoff)
                    retry_count += 1
                    continue
                raise e
        return resp

    return wrapper


class CustomClient:
    def __init__(self, host, auth, retry_timeout):
        self.host = host
        self.timeout = 30
        self.session = requests.Session()
        self.retry_timeout = retry_timeout
        self.session.headers.update(build_default_headers(auth))

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


def build_default_headers(auth_obj):
    headers = {
        "DD-API-KEY": auth_obj["apiKeyAuth"],
        "DD-APPLICATION-KEY": auth_obj["appKeyAuth"],
        "Content-Type": "application/json",
    }
    return headers


def paginated_request(func):
    def wrapper(*args, **kwargs):
        page_size = 100
        page_number = 0
        remaining = 1
        resources = []
        kwargs["params"] = kwargs.get("params", {}) or {}

        while remaining > 0:
            params = {"page[size]": page_size, "page[number]": page_number}
            kwargs["params"].update(params)

            resp = func(*args, **kwargs)
            resp.raise_for_status()

            resp_json = resp.json()
            resources.extend(resp_json["data"])
            remaining = int(resp_json["meta"]["page"]["total_count"]) - (page_size * (page_number + 1))
            page_number += 1
        return resources

    return wrapper
