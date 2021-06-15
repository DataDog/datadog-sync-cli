import logging

from datadog_sync.constants import LOGGER_NAME


def _configure_logging(verbose):
    # Set logging level and format
    _format = "%(asctime)s - %(levelname)s - %(message)s"
    if verbose:
        logging.basicConfig(format=_format, level=logging.DEBUG)
    else:
        logging.basicConfig(format=_format, level=logging.INFO)


class Log:
    def __init__(self, verbose):
        _configure_logging(verbose)

        self.exception_logged = False
        self.logger = logging.getLogger(LOGGER_NAME)

    def debug(self, msg, *arg):
        self.logger.debug(msg, *arg)

    def exception(self, msg, *arg):
        self._exception_logged()
        self.logger.exception(msg, *arg)

    def error(self, msg, *arg):
        self._exception_logged()
        self.logger.error(msg, *arg)

    def info(self, msg, *arg):
        self.logger.info(msg, *arg)

    def _exception_logged(self):
        self.exception_logged = True
