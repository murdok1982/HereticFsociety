# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixture plugin that attempts a TCP connection to a loopback port.

The security tests bind a listener on the host and pass its port in the
payload. When a network namespace is applied the connection must fail; when the
operator explicitly grants network access it must succeed. No third-party host
is ever contacted.
"""

from __future__ import annotations

import socket
from typing import Any

from fsociety import PLUGIN_API_VERSION
from fsociety.plugins.api import Scorer


class NetworkProbeScorer(Scorer):
    name = "fixture_network_probe"
    version = "1.0.0"
    api_version = PLUGIN_API_VERSION
    kind = "scorer"
    description = "Attempts a loopback TCP connection to the supplied port."
    author = "FSOCIETY AI tests"
    license = "AGPL-3.0-or-later"
    dependencies: list[str] = []
    permissions: list[str] = []

    def score(self, item: dict[str, Any]) -> Any:
        port = int(item["port"])
        try:
            connection = socket.create_connection(("127.0.0.1", port), timeout=1.5)
        except OSError as exc:
            return f"{type(exc).__name__}: {exc}"
        connection.close()
        return True
