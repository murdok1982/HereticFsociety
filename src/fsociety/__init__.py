"""FSOCIETY AI — Advanced Model Engineering Platform.

The platform is a superset of the Heretic engine: it never replaces the
``heretic`` package, it orchestrates it. Importing :mod:`fsociety` must stay
cheap so that API/dashboard/CLI deployments work without PyTorch installed.
"""

from __future__ import annotations

__version__ = "3.0.0"
__platform_name__ = "FSOCIETY AI"

#: Corresponding-source location for AGPL-3.0 section 13 compliance.
SOURCE_URL = "https://github.com/murdok1982/HereticFsociety"

#: Version of the public plugin contract. Bump only on breaking changes.
PLUGIN_API_VERSION = 1

__all__ = ["PLUGIN_API_VERSION", "SOURCE_URL", "__platform_name__", "__version__"]
