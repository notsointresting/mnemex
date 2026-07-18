"""Lazy loader for the optional sqlite-vec native accelerator.

This is the ONLY module permitted to import ``sqlite_vec``. Core mode must
never attempt the import: ``MNEMEX_NO_VEC=1`` short-circuits before
``importlib`` is called, so endpoint protection never sees mnemex touch the
native payload. Statuses are stable, non-secret strings; no exception text or
library path is ever returned.
"""

from __future__ import annotations

import importlib
import os
from types import ModuleType


def load_module() -> tuple[ModuleType | None, str]:
    """Return ``(sqlite_vec module or None, status)``. Never raises.

    Statuses: ``available``, ``disabled-by-environment``,
    ``package-not-installed``, ``extension-load-failed``.
    (``extension-loading-unsupported`` is decided later, at connection time.)
    """
    if os.environ.get("MNEMEX_NO_VEC"):
        return None, "disabled-by-environment"
    try:
        return importlib.import_module("sqlite_vec"), "available"
    except ImportError:
        return None, "package-not-installed"
    except OSError:
        return None, "extension-load-failed"
