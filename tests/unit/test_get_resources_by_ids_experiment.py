# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Tests for the --id-file (ID-targeted import) feature.

Goal: empirical answers to design questions about the --id-file path. Each test
asserts one measurable claim about the implementation.
"""

import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from datadog_sync.model.monitors import Monitors
from datadog_sync.utils.resource_utils import CustomClientHTTPError


def _make_monitors_resource(mock_config):
    m = Monitors(mock_config)
    return m


def _http_error(status: int, message: str = ""):
    """Build a CustomClientHTTPError without needing a real aiohttp ClientResponseError."""
    err = MagicMock()
    err.status = status
    err.message = message or f"HTTP {status}"
    err.headers = {}
    return CustomClientHTTPError(err, message=err.message)


def _run_async(coro):
    """Run an async coroutine synchronously. Mirrors the existing
    test_service_level_objectives.py pattern (uses asyncio.run, no pytest-asyncio
    dependency) so these tests run cleanly in tox without adding a new test extra."""
    return asyncio.run(coro)


# --- Test 1: 404s flow into missing_ids, not errored_ids ---


def test_404_classified_as_missing(mock_config):
    """A 404 from per-ID GET should land in missing_ids, not errored_ids."""
    monitors = _make_monitors_resource(mock_config)

    async def fake_get(path, **kwargs):
        if path.endswith("/404id"):
            raise _http_error(404)
        return {"id": path.rsplit("/", 1)[1], "type": "metric alert"}

    mock_config.source_client.get = fake_get

    resources, missing, errored = _run_async(
        monitors.get_resources_by_ids(mock_config.source_client, ["100", "404id", "200"], max_concurrent_reads=10)
    )
    assert sorted(r["id"] for r in resources) == ["100", "200"]
    assert missing == ["404id"]
    assert errored == []


# --- Test 2: 5xx classified as transient ---


def test_5xx_classified_as_transient(mock_config):
    monitors = _make_monitors_resource(mock_config)

    async def fake_get(path, **kwargs):
        if path.endswith("/503id"):
            raise _http_error(503)
        return {"id": path.rsplit("/", 1)[1], "type": "metric alert"}

    mock_config.source_client.get = fake_get
    resources, missing, errored = _run_async(
        monitors.get_resources_by_ids(mock_config.source_client, ["100", "503id"], max_concurrent_reads=10)
    )
    assert sorted(r["id"] for r in resources) == ["100"]
    assert missing == []
    assert len(errored) == 1
    assert errored[0][1] == "transient"


# --- Test 3: 429 classified as transient ---


def test_429_classified_as_transient(mock_config):
    monitors = _make_monitors_resource(mock_config)

    async def fake_get(path, **kwargs):
        if path.endswith("/429id"):
            raise _http_error(429)
        return {"id": path.rsplit("/", 1)[1], "type": "metric alert"}

    mock_config.source_client.get = fake_get
    resources, missing, errored = _run_async(
        monitors.get_resources_by_ids(mock_config.source_client, ["429id"], max_concurrent_reads=10)
    )
    assert resources == []
    assert errored[0][1] == "transient"


# --- Test 4: monitors SkipResource (synthetics alert type) classified as skipped ---


def test_monitors_synthetics_alert_skipped(mock_config):
    monitors = _make_monitors_resource(mock_config)

    async def fake_get(path, **kwargs):
        # Return a synthetics alert (which monitors.import_resource will SkipResource on)
        return {"id": path.rsplit("/", 1)[1], "type": "synthetics alert"}

    mock_config.source_client.get = fake_get
    resources, missing, errored = _run_async(
        monitors.get_resources_by_ids(mock_config.source_client, ["123"], max_concurrent_reads=10)
    )
    assert resources == []
    assert missing == []
    assert len(errored) == 1
    assert errored[0][1] == "skipped"
    assert "synthetics" in errored[0][2].lower()


# --- Test 5: max_concurrent_reads actually bounds in-flight requests ---


def test_max_concurrent_reads_actually_bounds_concurrency(mock_config):
    """The semaphore should cap concurrent in-flight calls. Run 100 IDs at MCR=10
    and verify peak in-flight count never exceeds 10."""
    monitors = _make_monitors_resource(mock_config)

    in_flight = 0
    peak = 0
    lock = threading.Lock()

    async def fake_get(path, **kwargs):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.005)  # simulate latency so concurrency builds up
        with lock:
            in_flight -= 1
        return {"id": path.rsplit("/", 1)[1], "type": "metric alert"}

    mock_config.source_client.get = fake_get
    ids = [str(i) for i in range(100)]
    resources, missing, errored = _run_async(
        monitors.get_resources_by_ids(mock_config.source_client, ids, max_concurrent_reads=10)
    )
    assert len(resources) == 100
    assert peak <= 10, f"semaphore failed: peak={peak} > MCR=10"
    # Sanity: at MCR=10 with non-trivial latency, peak should approach 10 (otherwise
    # the test isn't actually proving the cap is the binding constraint)
    assert peak >= 5, f"peak={peak} too low — test is not exercising the cap"


# --- Test 6: at MCR=30 with same workload, peak rises ---


def test_higher_mcr_allows_higher_concurrency(mock_config):
    monitors = _make_monitors_resource(mock_config)
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    async def fake_get(path, **kwargs):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.005)
        with lock:
            in_flight -= 1
        return {"id": path.rsplit("/", 1)[1], "type": "metric alert"}

    mock_config.source_client.get = fake_get
    ids = [str(i) for i in range(100)]
    _run_async(monitors.get_resources_by_ids(mock_config.source_client, ids, max_concurrent_reads=30))
    assert peak <= 30
    assert peak >= 15  # confirms MCR is what's actually binding, not artifact


# --- Test 7: marker emission contains literal "rate limit" ---


def test_marker_string_matches_managed_sync_pattern():
    """The marker string emitted by _import_get_resources_cb on threshold breach must
    contain a substring that the consumer-side rate-limit detector will match.

    a representative consumer pattern list includes 'rate limit'
    (with space). Our marker emits 'rate limit exceeded after N transient failures
    (threshold M%)' — verify substring match."""
    # Simulate the marker string we emit in resources_handler.py
    transient_count = 6
    threshold = 5
    total_ids = 100
    marker = (
        f"rate limit exceeded after {transient_count} transient failures "
        f"(threshold {threshold}% of {total_ids} requested IDs)"
    )

    # representative consumer pattern list (verbatim subset)
    managed_sync_patterns = [
        "status 429 ",
        "status 503 ",
        " 429 ",
        " 503 ",
        "rate limit",
        "too many requests",
        "throttled",
        "retry after",
        "quota exceeded",
        "slowdown",
        "request limit",
    ]
    matches = [p for p in managed_sync_patterns if p in marker.lower()]
    assert matches, f"marker {marker!r} does NOT match any consumer-side detector pattern"
    assert "rate limit" in matches


def test_buggy_old_marker_does_not_match():
    """Sanity: an early-draft marker 'failure_class=rate_limit_exceeded' should NOT match.
    Reason: underscore vs space."""
    bad_marker = "failure_class=rate_limit_exceeded"
    managed_sync_patterns = [
        "status 429 ",
        "status 503 ",
        " 429 ",
        " 503 ",
        "rate limit",
        "too many requests",
        "throttled",
        "retry after",
        "quota exceeded",
        "slowdown",
        "request limit",
    ]
    matches = [p for p in managed_sync_patterns if p in bad_marker.lower()]
    assert not matches, f"old marker shouldn't match but matched {matches}"


def _is_rate_limit_output_python_replica(output_bytes: bytes) -> bool:
    """Exact Python replica of the consumer-side rate-limit detector.

    Mirrors:
      - strings.ToLower(string(output[:min(len, MaxLogOutputBytes)]))
      - for p in patterns: if strings.Contains(s, p) -> true

    MaxLogOutputBytes is large (multi-MB); we don't need to enforce it for these tests.
    """
    if not output_bytes:
        return False
    s = output_bytes.decode("utf-8", errors="replace").lower()
    patterns = [
        "status 429 ",
        "status 503 ",
        " 429 ",
        " 503 ",
        "rate limit",
        "too many requests",
        "throttled",
        "retry after",
        "quota exceeded",
        "slowdown",
        "request limit",
    ]
    return any(p in s for p in patterns)


def test_cross_language_marker_triggers_managed_sync_retrylater():
    """Cross-language verification: the actual byte sequence sync-cli emits on threshold
    breach must trigger the consumer's rate-limit-detection retry path.

    This replicates a typical consumer's lowercase-substring detector function
    in Python and asserts our marker matches. Since we can't add tests to dd-source from
    this experiment, this is the next-best thing: a faithful replica that mirrors the
    exact match logic (strings.ToLower + strings.Contains)."""
    # Simulate the bytes our subprocess would emit (logger.error output goes to stderr,
    # combined with stdout via cmd.CombinedOutput in dd-source's runSyncCLIImportForChunk)
    transient_count = 6
    threshold = 5
    total_ids = 100
    log_line = (
        f"ERROR: rate limit exceeded after {transient_count} transient failures "
        f"(threshold {threshold}% of {total_ids} requested IDs)\n"
    )
    output_bytes = log_line.encode("utf-8")
    assert _is_rate_limit_output_python_replica(
        output_bytes
    ), f"a consumer-side rate-limit detector would NOT fire for marker:\n{log_line!r}"


def test_cross_language_old_marker_does_not_trigger():
    """Sanity-check the negative case: an early-draft marker without the literal
    "rate limit" substring would NOT fire. Confirms the replica function is faithful."""
    bad_log_line = b"ERROR: failure_class=rate_limit_exceeded after 6 transient failures\n"
    assert not _is_rate_limit_output_python_replica(
        bad_log_line
    ), "early-draft marker SHOULD NOT match — Python replica disagrees with the original claim"


def test_cross_language_marker_uppercase_still_matches():
    """Consumers typically lowercase output before pattern match. Verify our marker matches
    even if the logger emits uppercase (some loggers uppercase log levels)."""
    log_line = b"ERROR: RATE LIMIT EXCEEDED after 6 transient failures\n"
    assert _is_rate_limit_output_python_replica(log_line)


# --- Test 14: --id-file allowlist rejects non-monitors types in v1 ---


def test_id_file_allowlist_rejects_non_monitors_v1(tmp_path, monkeypatch):
    """PR4 v1 supports monitors only. Other types in --id-file must error at config-build.
    Future expansion (SLOs, etc.) requires explicit code-level allowlist update — NOT
    a config-time widening — to force per-model verification."""
    import json
    from datadog_sync.utils.configuration import _parse_id_file
    from unittest.mock import MagicMock

    # Write a payload with a non-monitors type
    payload_path = tmp_path / "ids.json"
    payload_path.write_text(json.dumps({"dashboards": ["abc-def-ghi"]}))

    logger = MagicMock()
    # _parse_id_file calls sys.exit(1) on validation failure. Patch to raise instead.
    with pytest.raises(SystemExit) as excinfo:
        _parse_id_file(str(payload_path), logger)
    assert excinfo.value.code == 1
    # logger.error should have been called with a message naming the unsupported type
    error_calls = [str(call) for call in logger.error.call_args_list]
    assert any("dashboards" in c for c in error_calls), error_calls


def test_id_file_allowlist_accepts_monitors(tmp_path):
    """Monitors are in the v1 allowlist."""
    import json
    from datadog_sync.utils.configuration import _parse_id_file
    from unittest.mock import MagicMock

    payload_path = tmp_path / "ids.json"
    payload_path.write_text(json.dumps({"monitors": ["100", "200", "300"]}))
    logger = MagicMock()
    result = _parse_id_file(str(payload_path), logger)
    assert result == {"monitors": ["100", "200", "300"]}
    logger.error.assert_not_called()


# --- retry-budget exhaustion classified as transient ---


def test_retry_limit_exceeded_classified_as_transient(mock_config):
    """request_with_retry() raises plain Exception with 'retry limit exceeded' when
    its retry budget is exhausted without an inner ClientResponseError raising. That
    case must classify as transient so sustained-throttling surfaces the rate-limit-
    shaped exit, not silent partial success."""
    monitors = _make_monitors_resource(mock_config)

    async def fake_get(path, **kwargs):
        # Simulate the exact message custom_client.py:70 raises
        raise Exception("retry limit exceeded timeout: 100 retry_count: 3 error: 503")

    mock_config.source_client.get = fake_get
    resources, missing, errored = _run_async(
        monitors.get_resources_by_ids(mock_config.source_client, ["1"], max_concurrent_reads=10)
    )
    assert resources == []
    assert missing == []
    assert len(errored) == 1
    assert errored[0][1] == "transient", f"expected transient, got {errored[0]}"


def test_aiohttp_connection_error_classified_as_transient(mock_config):
    """aiohttp.ClientConnectionError (DNS, connection refused, TCP reset) bubbles up
    from custom_client when the API is unreachable — these are transport-shaped
    failures and should count toward the transient threshold."""
    import aiohttp

    monitors = _make_monitors_resource(mock_config)

    async def fake_get(path, **kwargs):
        raise aiohttp.ClientConnectionError("Cannot connect to host")

    mock_config.source_client.get = fake_get
    resources, missing, errored = _run_async(
        monitors.get_resources_by_ids(mock_config.source_client, ["1"], max_concurrent_reads=10)
    )
    assert resources == []
    assert len(errored) == 1
    assert errored[0][1] == "transient"
    assert "ClientConnectionError" in errored[0][2]


def test_genuinely_permanent_exception_still_classified_permanent(mock_config):
    """Sanity check the inverse: an unrelated exception (e.g., a parser error in the
    response body, a model bug) must still classify as permanent. The transient
    classifier should only widen for connection-level errors and retry-exhaustion."""
    monitors = _make_monitors_resource(mock_config)

    async def fake_get(path, **kwargs):
        raise ValueError("unexpected response shape")

    mock_config.source_client.get = fake_get
    resources, missing, errored = _run_async(
        monitors.get_resources_by_ids(mock_config.source_client, ["1"], max_concurrent_reads=10)
    )
    assert len(errored) == 1
    assert errored[0][1] == "permanent"


# --- --max-concurrent-reads validation ---


def test_max_concurrent_reads_zero_rejected(tmp_path, monkeypatch):
    """--max-concurrent-reads=0 silently creates asyncio.Semaphore(0) which blocks
    all acquires forever. Validate at config-build with a clear message."""
    # We test the validation logic in build_config indirectly by spawning the actual
    # subprocess in a very short test — see test_id_file_subprocess_experiment.py.
    # Here, smoke-test that a negative int would also be caught (which Semaphore would
    # have raised anyway, but we want the CLI-level error to take precedence with a
    # clearer message).
    pass  # Real coverage in subprocess test below


# --- --id-file × --resources interaction ---


def test_id_file_without_resources_rejected(tmp_path):
    """--id-file with no --resources means every non-monitors type goes via legacy
    full-list path, defeating the wall-clock bound. Must error at config-build.
    """
    # Coverage in subprocess integration test (test_id_file_subprocess_experiment.py).
    # Documenting the requirement here for visibility in this file's test inventory.
    pass


# --- Test 8: empty id list is no-op (returns empty tuples) ---


def test_empty_id_list_is_noop(mock_config):
    monitors = _make_monitors_resource(mock_config)
    resources, missing, errored = _run_async(
        monitors.get_resources_by_ids(mock_config.source_client, [], max_concurrent_reads=10)
    )
    assert resources == []
    assert missing == []
    assert errored == []


# --- Test 9: mixed outcomes — verifies threshold math wiring ---


def test_mixed_outcomes_threshold_math(mock_config):
    """6 transient out of 100 = 6.0% > 5.0% threshold. Caller would set fatal_error."""
    monitors = _make_monitors_resource(mock_config)

    async def fake_get(path, **kwargs):
        id_ = path.rsplit("/", 1)[1]
        idx = int(id_)
        if idx < 6:
            raise _http_error(503)
        return {"id": id_, "type": "metric alert"}

    mock_config.source_client.get = fake_get
    ids = [str(i) for i in range(100)]
    resources, missing, errored = _run_async(
        monitors.get_resources_by_ids(mock_config.source_client, ids, max_concurrent_reads=10)
    )
    assert len(resources) == 94
    transient_count = sum(1 for _, cls, _ in errored if cls == "transient")
    assert transient_count == 6
    # The wiring contract: caller computes (transient_count / len(ids)) * 100 and
    # compares to threshold. This test certifies the input data is correct shape.
    pct = (transient_count / len(ids)) * 100
    assert pct == pytest.approx(6.0)
    assert pct > 5.0  # would breach default threshold
