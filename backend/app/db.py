from contextlib import contextmanager
from typing import Iterator

import psycopg

from app.config import get_settings


@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    """Open a short-lived connection. The host has little memory, so no pool."""
    url = get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    with psycopg.connect(url) as conn:
        yield conn
