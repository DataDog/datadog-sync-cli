import time
from datadog_api_client.v1 import ApiException as ApiExceptionV1
from datadog_api_client.v2 import ApiException as ApiExceptionV2


def request_with_retry(func):
    def wrapper(*args, **kwargs):
        retry = True
        timeout = time.time() + 60 * 10
        default_backoff = 5
        retry_count = 0

        while retry and timeout > time.time():
            try:
                resp = func(*args, **kwargs)
                retry = False
            except (ApiExceptionV1, ApiExceptionV2) as e:
                if (e.status == 429 and "x-ratelimit-reset" in e.headers) or e.status >= 500:
                    retry_count += 1
                    time.sleep(retry_count * default_backoff)
                    pass
        return resp

    return wrapper
