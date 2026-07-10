# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

"""Handler-level tests for the --refresh-destination-state-before-apply wiring.

Isolates the _maybe_refresh_destination_state helper on ResourcesHandler so
regressions in the config flag, the state.reload_destination call, or the
failure-swallowing behaviour surface even if apply_resources is refactored.
"""

from unittest.mock import MagicMock

from datadog_sync.utils.resources_handler import ResourcesHandler


def _make_handler(refresh_enabled: bool, reload_side_effect=None):
    handler = ResourcesHandler.__new__(ResourcesHandler)
    handler.config = MagicMock()
    handler.config.refresh_destination_state_before_apply = refresh_enabled
    handler.config.logger = MagicMock()
    state = MagicMock()
    if reload_side_effect is not None:
        state.reload_destination.side_effect = reload_side_effect
    else:
        state.reload_destination.return_value = {"monitors": 2, "dashboards": 0}
    handler.config.state = state
    return handler


def test_refresh_skipped_when_flag_off():
    handler = _make_handler(refresh_enabled=False)
    handler._maybe_refresh_destination_state({"monitors", "dashboards"})
    handler.config.state.reload_destination.assert_not_called()
    # No refresh log fires when the flag is off.
    for call in handler.config.logger.info.call_args_list:
        assert "reload_destination" not in call.args[0]


def test_refresh_invoked_when_flag_on():
    handler = _make_handler(refresh_enabled=True)
    handler._maybe_refresh_destination_state({"monitors", "dashboards"})
    handler.config.state.reload_destination.assert_called_once()
    # Called with a sorted list so log/order is deterministic.
    args, _ = handler.config.state.reload_destination.call_args
    assert args[0] == ["dashboards", "monitors"]


def test_refresh_summary_log_emitted():
    handler = _make_handler(refresh_enabled=True)
    handler._maybe_refresh_destination_state({"monitors", "dashboards"})
    info_msgs = [c.args[0] for c in handler.config.logger.info.call_args_list]
    assert any("phase=reload_destination" in m for m in info_msgs)


def test_refresh_failure_never_raises_and_logs_warning():
    handler = _make_handler(refresh_enabled=True, reload_side_effect=RuntimeError("boom"))
    # MUST NOT raise — apply proceeds with pre-refresh state.
    handler._maybe_refresh_destination_state({"monitors"})
    # Warning includes exc_info so the traceback isn't lost.
    handler.config.logger.warning.assert_called_once()
    _, kwargs = handler.config.logger.warning.call_args
    assert kwargs.get("exc_info") is True
