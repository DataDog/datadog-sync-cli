# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Subprocess integration tests for the --id-file (ID-targeted import) feature.

Invokes datadog-sync as a real subprocess via subprocess.run with --id-file=- and
stdin payload, against an aiohttp test server returning mocked monitor responses.
This is the only test that exercises the CLI parser → Configuration →
_import_get_resources_cb → exit code path end-to-end. Earlier unit tests run
get_resources_by_ids directly via mock; this test runs the actual binary.

What we measure:
  1. CLI flag plumbing works (--id-file=- accepted, max-concurrent-reads honored)
  2. Stdin payload correctly parsed (no buffering/EOF issues)
  3. On 100% successful fetches, subprocess exits 0 and state files written
  4. On above-threshold transient failures, subprocess exits 1
  5. On above-threshold failures, state files for SUCCEEDED IDs ARE written
     (state.dump_state runs before sys.exit on threshold breach)
  6. On above-threshold failures, output bytes contain literal "rate limit"
     substring matching the consumer-side rate-limit detector pattern list
"""

import json
import os
import socket
import subprocess
import threading
import time
from typing import Set

import pytest
from aiohttp import web

# Reuse the Python replica of a consumer-side rate-limit detector from the
# mock-based test file. Mirrors the typical consumer-side detector pattern
# (lowercase + substring scan against a fixed pattern list).
from tests.unit.test_get_resources_by_ids_experiment import (
    _is_rate_limit_output_python_replica,
)


def _free_port():
    """Allocate an OS-chosen free port, then close the socket so subprocess can bind."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class MockMonitorServer:
    """aiohttp-based mock for /api/v1/monitor/{id} that injects 5xx for designated IDs."""

    def __init__(self, fail_ids: Set[str], port: int):
        self.fail_ids = fail_ids
        self.port = port
        self.requests = []  # records (method, path, time)
        self._thread = None
        self._loop = None
        self._runner = None
        self._site = None

    async def _handle_get_monitor(self, request: web.Request):
        monitor_id = request.match_info["id"]
        self.requests.append(("GET", request.path, time.monotonic()))
        if monitor_id in self.fail_ids:
            return web.Response(status=503, text=f"mock 503 for {monitor_id}")
        body = {
            "id": monitor_id,
            "type": "metric alert",
            "name": f"mock-monitor-{monitor_id}",
            "query": "avg(last_5m):mock > 1",
            "message": "",
            "tags": [],
            "options": {},
            "multi": False,
        }
        return web.json_response(body)

    async def _handle_validate(self, request: web.Request):
        # /api/v1/validate is hit by sync-cli's validate step (we set --validate=false
        # so it shouldn't fire, but include for robustness)
        self.requests.append(("GET", request.path, time.monotonic()))
        return web.json_response({"valid": True})

    async def _handle_other(self, request: web.Request):
        # Catchall for anything else (e.g. /api/v1/monitor list endpoint if validate fails)
        self.requests.append(("GET", request.path, time.monotonic()))
        return web.json_response([])

    async def _start(self):
        import asyncio

        self._loop = asyncio.get_event_loop()
        app = web.Application()
        app.router.add_get("/api/v1/monitor/{id}", self._handle_get_monitor)
        app.router.add_get("/api/v1/validate", self._handle_validate)
        app.router.add_get("/{tail:.*}", self._handle_other)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        await self._site.start()

    def start_in_thread(self):
        import asyncio

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            loop.run_until_complete(self._start())
            loop.run_forever()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        # Wait for port to actually be listening
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError(f"mock server did not bind to {self.port}")

    def stop(self):
        if self._loop and self._site:
            import asyncio

            async def shutdown():
                await self._site.stop()
                await self._runner.cleanup()

            try:
                future = asyncio.run_coroutine_threadsafe(shutdown(), self._loop)
                future.result(timeout=2)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)


def _run_sync_cli(
    stdin_payload: str,
    source_url: str,
    source_resources_path: str,
    max_concurrent_reads: int = 30,
    threshold: int = 5,
    resources: str = "monitors",
    extra_flags: list = None,
):
    """Invoke datadog-sync as a subprocess with --id-file=- and stdin payload.

    Returns (exit_code, stdout_bytes, stderr_bytes).

    `resources=None` omits the --resources flag entirely (used to verify the
    validation that --id-file requires --resources).
    """
    cmd = [
        "datadog-sync",
        "import",
        "--source-api-url",
        source_url,
        "--source-api-key",
        "test-api-key",
        "--source-app-key",
        "test-app-key",
        "--destination-api-url",
        source_url,  # unused for import but required
        "--destination-api-key",
        "test",
        "--destination-app-key",
        "test",
        "--validate=false",
        "--verify-ddr-status=false",
        "--send-metrics=False",
        "--source-resources-path",
        source_resources_path,
        "--id-file=-",
        f"--max-concurrent-reads={max_concurrent_reads}",
        f"--transient-failure-threshold-pct={threshold}",
    ]
    if resources is not None:
        cmd.extend(["--resources", resources])
    if extra_flags:
        cmd.extend(extra_flags)
    proc = subprocess.run(
        cmd,
        input=stdin_payload.encode("utf-8") if isinstance(stdin_payload, str) else stdin_payload,
        capture_output=True,
        text=False,  # bytes; we want exact byte sequence for cross-language checks
        timeout=60,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    return proc.returncode, proc.stdout, proc.stderr


# --- Test 1: clean run (no failures) — exit 0, all state files written ---


@pytest.mark.experiment_subprocess
def test_subprocess_clean_run_writes_state(tmp_path):
    """100 monitor IDs, zero failures → exit 0, state file per monitor written."""
    port = _free_port()
    server = MockMonitorServer(fail_ids=set(), port=port)
    server.start_in_thread()
    try:
        ids = [str(i) for i in range(100, 110)]  # 10 ids for speed
        payload = json.dumps({"monitors": ids})
        source_dir = tmp_path / "source"

        rc, stdout, stderr = _run_sync_cli(
            payload,
            f"http://127.0.0.1:{port}",
            str(source_dir),
            max_concurrent_reads=10,
        )
        assert rc == 0, (
            f"expected exit 0, got {rc}\n"
            f"STDOUT:\n{stdout.decode(errors='replace')}\n"
            f"STDERR:\n{stderr.decode(errors='replace')}"
        )

        # State files: source_dir/monitors/{id}.json (resource-per-file mode is on by
        # default per the import command). Actually the default is many-resources-per-file
        # so check both shapes.
        monitors_dir = source_dir / "monitors"
        if monitors_dir.is_dir():
            files = list(monitors_dir.glob("*.json"))
            # resource_per_file off → one big file
            assert files, f"no state files in {monitors_dir}; ls: {list(source_dir.iterdir())}"
        else:
            # many-per-file shape
            big_file = source_dir / "monitors.json"
            assert big_file.exists(), f"expected {big_file} or {monitors_dir}; ls: {list(source_dir.iterdir())}"
            content = json.loads(big_file.read_text())
            # The shape is {id: resource}
            assert len(content) == 10, f"expected 10 monitors, got {len(content)}: keys={list(content)[:5]}"
    finally:
        server.stop()


# --- Test 2: above-threshold transient failures — exit 1, partial state preserved,
#     output contains "rate limit" marker ---


@pytest.mark.experiment_subprocess
def test_subprocess_threshold_breach_preserves_partial_state(tmp_path):
    """100 monitor IDs, 6 returning 503 (= 6% > 5% threshold) → exit 1, but state
    files for 94 successful IDs ARE written (the partial-state preservation fix:
    state.dump_state runs before sys.exit). Output contains literal 'rate limit'
    substring so the consumer-side rate-limit detector fires RetryLater."""
    port = _free_port()
    fail_ids = {str(i) for i in range(100, 106)}  # 6 of 100 fail
    server = MockMonitorServer(fail_ids=fail_ids, port=port)
    server.start_in_thread()
    try:
        ids = [str(i) for i in range(100, 200)]
        payload = json.dumps({"monitors": ids})
        source_dir = tmp_path / "source"

        rc, stdout, stderr = _run_sync_cli(
            payload,
            f"http://127.0.0.1:{port}",
            str(source_dir),
            max_concurrent_reads=10,
            threshold=5,
        )
        # Expect exit 1 due to threshold breach (6% > 5%)
        assert rc == 1, (
            f"expected exit 1, got {rc}\n"
            f"STDOUT:\n{stdout.decode(errors='replace')[:2000]}\n"
            f"STDERR:\n{stderr.decode(errors='replace')[:2000]}"
        )

        # Output must contain literal "rate limit" substring (case-insensitive scan
        # in a typical consumer-side rate-limit detector)
        combined = (stdout + stderr).lower()
        assert b"rate limit" in combined, (
            f"missing literal 'rate limit' marker in output:\n"
            f"STDOUT:\n{stdout.decode(errors='replace')[:2000]}\n"
            f"STDERR:\n{stderr.decode(errors='replace')[:2000]}"
        )

        # Cross-language verification (M3): pass the EXACT byte sequence the
        # subprocess emitted through a Python replica of a typical consumer's Go
        # isRateLimitOutput function. This is the strongest pre-merge assertion
        # that the cross-process contract works — the same predicate a consumer
        # uses on real CombinedOutput() bytes.
        assert _is_rate_limit_output_python_replica(stdout + stderr), (
            "the consumer-side rate-limit detector would NOT fire for "
            "this subprocess's exact output bytes. Cross-language contract broken."
        )

        # State files for the 94 successful IDs ARE written despite the fatal exit.
        # This is the partial-state preservation fix.
        monitors_path = source_dir / "monitors.json"
        monitors_dir = source_dir / "monitors"
        if monitors_path.exists():
            content = json.loads(monitors_path.read_text())
            success_count = len(content)
        elif monitors_dir.is_dir():
            success_count = len(list(monitors_dir.glob("*.json")))
        else:
            pytest.fail(
                f"NO state written despite the partial-state preservation fix: "
                f"source_dir contents: {list(source_dir.iterdir()) if source_dir.exists() else 'missing'}"
            )

        # We requested 100 monitors, 6 failed. The 94 successes (the ones with IDs
        # 106..199) should be persisted.
        assert success_count == 94, (
            f"expected 94 successful state entries (partial-state preservation: "
            f"state.dump_state runs before sys.exit), got {success_count}"
        )
    finally:
        server.stop()


# --- Test 3: below-threshold failures — exit 0 even with some 503s ---


@pytest.mark.experiment_subprocess
def test_subprocess_below_threshold_failures_exit_zero(tmp_path):
    """100 monitor IDs, 3 returning 503 (= 3% < 5% threshold) → exit 0, no fatal."""
    port = _free_port()
    fail_ids = {str(i) for i in range(100, 103)}  # 3 of 100 = 3%, below 5% threshold
    server = MockMonitorServer(fail_ids=fail_ids, port=port)
    server.start_in_thread()
    try:
        ids = [str(i) for i in range(100, 200)]
        payload = json.dumps({"monitors": ids})
        source_dir = tmp_path / "source"

        rc, stdout, stderr = _run_sync_cli(
            payload,
            f"http://127.0.0.1:{port}",
            str(source_dir),
            max_concurrent_reads=10,
            threshold=5,
        )
        assert rc == 0, (
            f"expected exit 0 (3% < 5% threshold), got {rc}\n" f"STDERR:\n{stderr.decode(errors='replace')[:2000]}"
        )
    finally:
        server.stop()


# --- Test 4: stdin payload reading works (no buffering/EOF issues) ---


@pytest.mark.experiment_subprocess
def test_subprocess_stdin_payload_parsed(tmp_path):
    """Verify stdin reading works for our payload size (~10 KB simulating 1000 IDs)."""
    port = _free_port()
    server = MockMonitorServer(fail_ids=set(), port=port)
    server.start_in_thread()
    try:
        # 1000 IDs of length 10 chars each = ~10 KB JSON payload
        ids = [str(1000000 + i) for i in range(1000)]
        payload = json.dumps({"monitors": ids})
        assert len(payload.encode()) > 10_000, f"payload too small for the test: {len(payload)} bytes"

        source_dir = tmp_path / "source"
        rc, stdout, stderr = _run_sync_cli(
            payload,
            f"http://127.0.0.1:{port}",
            str(source_dir),
            max_concurrent_reads=30,  # mimic prod default
        )
        assert rc == 0, (
            f"expected exit 0 with 1000 ids, got {rc}\n"
            f"STDERR (last 2KB):\n{stderr.decode(errors='replace')[-2000:]}"
        )

        # Verify all 1000 ended up in state
        monitors_path = source_dir / "monitors.json"
        if monitors_path.exists():
            content = json.loads(monitors_path.read_text())
            assert len(content) == 1000, f"expected 1000, got {len(content)}"
    finally:
        server.stop()


# --- Test 5: invalid id-file payload (unsupported type) errors at config-build ---


@pytest.mark.experiment_subprocess
def test_subprocess_unsupported_type_in_id_file(tmp_path):
    """--id-file with non-monitors type errors at config-build. PR4 v1 monitors-only."""
    payload = json.dumps({"dashboards": ["abc-def-ghi"]})
    source_dir = tmp_path / "source"
    # Use any URL — the subprocess shouldn't even reach the network.
    rc, stdout, stderr = _run_sync_cli(
        payload,
        "http://127.0.0.1:1",
        str(source_dir),  # port 1 — unreachable
        max_concurrent_reads=10,
    )
    assert rc == 1, f"expected exit 1, got {rc}\nSTDERR:\n{stderr.decode(errors='replace')}"
    combined = (stdout + stderr).decode(errors="replace")
    assert (
        "dashboards" in combined and "not supported" in combined.lower()
    ), f"expected error message mentioning 'dashboards' and 'not supported', got:\n{combined}"


# --- --id-file requires --resources ---


@pytest.mark.experiment_subprocess
def test_subprocess_id_file_without_resources_errors(tmp_path):
    """--id-file with no --resources would import every non-monitors type
    via legacy full-list path. Must error at config-build."""
    payload = json.dumps({"monitors": ["100"]})
    source_dir = tmp_path / "source"
    rc, stdout, stderr = _run_sync_cli(
        payload,
        "http://127.0.0.1:1",
        str(source_dir),
        resources=None,  # explicitly omit --resources
    )
    assert rc == 1, f"expected exit 1, got {rc}"
    combined = (stdout + stderr).decode(errors="replace")
    assert "--id-file requires --resources" in combined, f"missing expected error message in:\n{combined}"


@pytest.mark.experiment_subprocess
def test_subprocess_id_file_type_missing_from_resources_errors(tmp_path):
    """id-payload includes 'monitors' but --resources only has 'users'.
    The id-targeted path would never engage; fail loudly."""
    payload = json.dumps({"monitors": ["100"]})
    source_dir = tmp_path / "source"
    rc, stdout, stderr = _run_sync_cli(
        payload,
        "http://127.0.0.1:1",
        str(source_dir),
        resources="users",  # mismatched
    )
    assert rc == 1, f"expected exit 1, got {rc}"
    combined = (stdout + stderr).decode(errors="replace")
    assert "not present in --resources" in combined, f"missing expected error in:\n{combined}"


@pytest.mark.experiment_subprocess
def test_subprocess_id_file_with_resources_dependency_types_ok(tmp_path):
    """Sanity: --resources=monitors,users,roles + id-payload={monitors:[...]} is
    valid. monitors id-targeted; users/roles legacy. (Common shape: id-targeted
    target type with legacy dependency types in --resources.)"""
    port = _free_port()
    server = MockMonitorServer(fail_ids=set(), port=port)
    server.start_in_thread()
    try:
        ids = [str(i) for i in range(100, 103)]
        payload = json.dumps({"monitors": ids})
        source_dir = tmp_path / "source"
        rc, _, stderr = _run_sync_cli(
            payload,
            f"http://127.0.0.1:{port}",
            str(source_dir),
            max_concurrent_reads=10,
            resources="monitors,users,roles",
        )
        # Note: users/roles will go via legacy path and may fail because we only
        # mock /api/v1/monitor/{id}. The exit code semantics: we accept either 0 (if
        # users/roles list endpoints' errors are tolerated) or 1 (if they fail loudly)
        # as long as the FATAL exit is NOT due to our --id-file × --resources rule.
        combined = stderr.decode(errors="replace")
        assert (
            "requires --resources" not in combined
        ), f"validation incorrectly fired for valid input:\n{combined[:2000]}"
        assert (
            "not present in --resources" not in combined
        ), f"validation incorrectly fired for valid input:\n{combined[:2000]}"
    finally:
        server.stop()


# --- --max-concurrent-reads must be positive ---


@pytest.mark.experiment_subprocess
def test_subprocess_max_concurrent_reads_zero_errors(tmp_path):
    """asyncio.Semaphore(0) blocks acquires forever. Validate at
    config-build before reaching the semaphore."""
    payload = json.dumps({"monitors": ["100"]})
    source_dir = tmp_path / "source"
    rc, stdout, stderr = _run_sync_cli(
        payload,
        "http://127.0.0.1:1",
        str(source_dir),
        max_concurrent_reads=0,
    )
    assert rc == 1, f"expected exit 1, got {rc}"
    combined = (stdout + stderr).decode(errors="replace")
    assert "--max-concurrent-reads must be a positive integer" in combined, f"missing expected error in:\n{combined}"


@pytest.mark.experiment_subprocess
def test_subprocess_threshold_pct_out_of_range_errors(tmp_path):
    """threshold must be in [0, 100]."""
    payload = json.dumps({"monitors": ["100"]})
    source_dir = tmp_path / "source"
    rc, stdout, stderr = _run_sync_cli(
        payload,
        "http://127.0.0.1:1",
        str(source_dir),
        threshold=150,
    )
    assert rc == 1, f"expected exit 1, got {rc}"
    combined = (stdout + stderr).decode(errors="replace")
    assert "must be in range [0, 100]" in combined, f"missing expected error in:\n{combined}"
