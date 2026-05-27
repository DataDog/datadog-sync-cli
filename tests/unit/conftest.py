# Unless explicitly stated otherwise all files in this repository are licensed
# under the 3-clause BSD style license (see LICENSE).
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2019 Datadog, Inc.

from collections import defaultdict
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_config():
    """Lightweight mock config for unit tests — no network/file I/O.

    Diverges intentionally from the heavy integration ``config`` fixture in
    tests/conftest.py which requires real CustomClient + State objects.
    """
    config = MagicMock()
    config.destination_client = MagicMock()
    config.source_client = MagicMock()
    config.state = MagicMock()
    config.state.source = defaultdict(dict)
    config.state.destination = defaultdict(dict)
    config.logger = MagicMock()
    return config
