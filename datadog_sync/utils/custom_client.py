import time

import requests


class CustomClient:
    def __init__(self, host, auth, ctx):
        self.host = host
        self.ctx = ctx
        self.headers = build_default_headers(auth)

    def get(self, path, params=None):
        url = self.host + path
        response = request_with_retry(requests.get)(url, headers=self.headers, params=params)
        response.close()
        return response

    def post(self, path, body, params=None):
        url = self.host + path
        response = request_with_retry(requests.post)(url, json=body, headers=self.headers, params=params)
        response.close()
        return response

    def put(self, path, body, params=None):
        url = self.host + path
        response = request_with_retry(requests.put)(url, json=body, headers=self.headers, params=params)
        response.close()
        return response

    def patch(self, path, body, params=None):
        url = self.host + path
        response = request_with_retry(requests.patch)(url, json=body, headers=self.headers, params=params)
        response.close()
        return response



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
                retry = False
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code
                if (status_code == 429 and "x-ratelimit-reset" in e.response.headers) or status_code >= 500:
                    retry_count += 1
                    time.sleep(retry_count * default_backoff)
                    pass
                return e
        return resp

    return wrapper
