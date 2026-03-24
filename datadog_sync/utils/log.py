# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations

import logging

from datadog_sync.constants import LOGGER_NAME
from datadog_sync.utils.ndjson import write_ndjson_line


def _configure_logging(verbose: bool) -> None:
    # Set logging level and format
    _format = "%(asctime)s - %(levelname)s - %(message)s"
    if verbose:
        logging.basicConfig(format=_format, level=logging.DEBUG)
    else:
        logging.basicConfig(format=_format, level=logging.INFO)


class _NdjsonHandler(logging.Handler):
    """Logging handler that formats records as NDJSON log events on stdout.

    Catches messages from modules that use ``logging.getLogger(LOGGER_NAME)``
    directly (e.g. custom_client.py, storage backends) so they honour the
    ``--json`` NDJSON contract instead of leaking bare text.
    """

    def emit(self, record: logging.LogRecord) -> None:
        event = {"type": "log", "level": record.levelname.lower(), "message": record.getMessage()}
        write_ndjson_line(event)


class Log:
    def __init__(self, verbose: bool, emit_json: bool = False) -> None:
        self._emit_json = emit_json
        self._verbose = verbose
        self._log_level = logging.DEBUG if verbose else logging.INFO
        self.exception_logged = False

        self.logger = logging.getLogger(LOGGER_NAME)
        self.logger.handlers.clear()

        if emit_json:
            # In JSON mode: silence stderr, emit NDJSON to stdout.
            # The handler catches stdlib logger calls from modules that
            # bypass the Log class (e.g. custom_client.py, storage backends).
            self.logger.propagate = False
            self.logger.setLevel(self._log_level)
            self.logger.addHandler(_NdjsonHandler())
        else:
            _configure_logging(verbose)
            self.logger.propagate = True

    def _emit_log_json(self, level: str, msg: str, resource_type: str = "", _id: str = "") -> None:
        """Write a single NDJSON log event to stdout."""
        event = {"type": "log", "level": level, "message": msg}
        if resource_type:
            event["resource_type"] = resource_type
        if _id:
            event["id"] = _id
        write_ndjson_line(event)

    def debug(self, msg, *arg, _id: str = "", resource_type: str = ""):
        if self._emit_json:
            if self._log_level <= logging.DEBUG:
                self._emit_log_json("debug", msg, resource_type=resource_type, _id=_id)
            return
        if resource_type or _id:
            msg = f"[{resource_type} - {_id}] - {msg}"
        self.logger.debug(msg, *arg)

    def exception(self, msg, *arg, _id: str = "", resource_type: str = ""):
        if self._emit_json:
            self._emit_log_json("error", msg, resource_type=resource_type, _id=_id)
            self._exception_logged()
            return
        if resource_type or _id:
            msg = f"[{resource_type} - {_id}] - {msg}"
        self.logger.exception(msg, *arg)
        self._exception_logged()

    def error(self, msg, *arg, _id: str = "", resource_type: str = ""):
        if self._emit_json:
            self._emit_log_json("error", msg, resource_type=resource_type, _id=_id)
            self._exception_logged()
            return
        if resource_type or _id:
            msg = f"[{resource_type} - {_id}] - {msg}"
        self.logger.error(msg, *arg)
        self._exception_logged()

    def info(self, msg: str, *arg, _id: str = "", resource_type: str = "") -> None:
        if self._emit_json:
            self._emit_log_json("info", msg, resource_type=resource_type, _id=_id)
            return
        if resource_type or _id:
            msg = f"[{resource_type} - {_id}] - {msg}"
        self.logger.info(msg, *arg)

    def warning(self, msg, *arg, _id: str = "", resource_type: str = ""):
        if self._emit_json:
            self._emit_log_json("warning", msg, resource_type=resource_type, _id=_id)
            return
        if resource_type or _id:
            msg = f"[{resource_type} - {_id}] - {msg}"
        self.logger.warning(msg, *arg)

    def _exception_logged(self):
        self.exception_logged = True
