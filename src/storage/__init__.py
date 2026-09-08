from __future__ import annotations

import os
from functools import lru_cache

from src.storage.base import ApplicationStore


@lru_cache
def get_store() -> ApplicationStore:
    """Factory that picks the storage backend based on STORAGE_BACKEND
    (see .env.example). Everything else in the app (the dashboard, tests)
    talks only to the ApplicationStore interface, so switching from local
    JSON to real AWS is a one-line env var change, not a code change.
    """
    backend = os.environ.get("STORAGE_BACKEND", "local").lower()
    if backend == "aws":
        from src.storage.dynamo_store import DynamoStore

        return DynamoStore()
    from src.storage.local_store import LocalJsonStore

    return LocalJsonStore()
