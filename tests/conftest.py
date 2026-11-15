"""Suite-wide test isolation.

Many tests build services (ConversationService, IdentityResolver, Discord
runtime) that open the default memory database. Point that default at a
throwaway file so the suite never reads or writes the checkout's real
``data/memory/gamma.db`` and does not depend on that directory existing.
This runs at conftest import, before test modules import anything that might
open the database at module level. Tests that need their own database still
override ``settings.database_url`` locally.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from gamma.config import settings

_ISOLATED_DB_DIR = Path(tempfile.mkdtemp(prefix="gamma-tests-db-"))
settings.database_url = f"sqlite:///{_ISOLATED_DB_DIR / 'gamma.db'}"
# The opinion extractor makes a background model call after trusted turns; tests that
# count model calls must not see it. tests/test_own_opinions.py turns it on.
settings.memory_own_opinions = "off"


def pytest_sessionfinish(session, exitstatus) -> None:
    shutil.rmtree(_ISOLATED_DB_DIR, ignore_errors=True)
