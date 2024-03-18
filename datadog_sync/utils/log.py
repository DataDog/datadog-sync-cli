# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from __future__ import annotations
import logging

from progressbar import streams

from datadog_sync.constants import LOGGER_NAME


def _configure_logging(verbose: bool) -> None:
    streams.wrap_stderr()
    streams.wrap_stdout()

    # Set logging level and format
    _format = "%(asctime)s - %(levelname)s - %(message)s"
    if verbose:
        logging.basicConfig(format=_format, level=logging.DEBUG)
    else:
        logging.basicConfig(format=_format, level=logging.INFO)


class Log:
    def __init__(self, verbose: bool) -> None:
        _configure_logging(verbose)

        self.exception_logged = False
        self.logger = logging.getLogger(LOGGER_NAME)
        self.logger.propagate = True

    def debug(self, msg, *arg):
        self.logger.debug(msg, *arg)

    def exception(self, msg, *arg):
        self._exception_logged()
        self.logger.exception(msg, *arg)

    def error(self, msg, *arg):
        self._exception_logged()
        self.logger.error(msg, *arg)

    def info(self, msg: str, *arg) -> None:
        self.logger.info(msg, *arg)

    def warning(self, msg, *arg):
        self.logger.warning(msg, *arg)

    def _exception_logged(self):
        self.exception_logged = True
