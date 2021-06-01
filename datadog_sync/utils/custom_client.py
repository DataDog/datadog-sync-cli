import time

import requests


class CustomClient:
    def __init__(self, host, auth, ctx):
        self.host = host
        self.ctx = ctx
        self.headers = build_default_headers(auth)

    def get(self, path, params=None):
        url = self.host + path
        try:
            response = request_with_retry(requests.get)(url, headers=self.headers, params=params)
            response.close()
            return response
        except requests.exceptions.HTTPError as e:
            raise e

    def post(self, path, body, params=None):
        url = self.host + path
        try:
            response = request_with_retry(requests.post)(url, json=body, headers=self.headers, params=params)
            response.close()
            return response
        except requests.exceptions.HTTPError as e:
            raise e

    def put(self, path, body, params=None):
        url = self.host + path
        try:
            response = request_with_retry(requests.put)(url, json=body, headers=self.headers, params=params)
            response.close()
            return response
        except requests.exceptions.HTTPError as e:
            raise e

    def patch(self, path, body, params=None):
        url = self.host + path
        try:
            response = request_with_retry(requests.patch)(url, json=body, headers=self.headers, params=params)
            response.close()
            return response
        except requests.exceptions.HTTPError as e:
            raise e

    def delete(self, path, body, params=None):
        url = self.host + path
        try:
            response = request_with_retry(requests.delete)(url, json=body, headers=self.headers, params=params)
            response.close()
            return response
        except requests.exceptions.HTTPError as e:
            raise e


def build_default_headers(auth_obj):
    headers = {
        "DD-API-KEY": auth_obj["apiKeyAuth"],
        "DD-APPLICATION-KEY": auth_obj["appKeyAuth"],
        "Content-Type": "application/json",
    }
    return headers


def request_with_retry(func):
    def wrapper(*args, **kwargs):
        retry = True
        timeout = time.time() + 60 * 10
        default_backoff = 5
        retry_count = 0
        resp = None

        while retry and timeout > time.time():
            try:
                resp = func(*args, **kwargs)
                resp.raise_for_status()
                retry = False
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code
                if (status_code == 429 and "x-ratelimit-reset" in e.response.headers) or status_code >= 500:
                    retry_count += 1
                    try:
                        backoff = int(e.response.headers["x-ratelimit-reset"])
                    except ValueError:
                        backoff = default_backoff
                    time.sleep(retry_count * backoff)
                    continue
                raise e
        return resp

    return wrapper


def paginated_request(func):
    def wrapper(*args, **kwargs):
        page_size = 100
        page_number = 0
        remaining = 1
        resources = []
        while remaining > 0:
            try:
                params = {"page[size]": page_size, "page[number]": page_number}
                if "params" in kwargs:
                    kwargs["params"].update(params)
                else:
                    kwargs["params"] = params
                resp = func(*args, **kwargs)
                resp.raise_for_status()

                resp_json = resp.json()
                resources.extend(resp_json["data"])
                remaining = int(resp_json["meta"]["page"]["total_count"]) - (page_size * (page_number + 1))
                page_number += 1
            except requests.exceptions.HTTPError as e:
                raise e
        return resources

    return wrapper
