"""Every suite runs on InMemoryStore; with EV_TEST_DATABASE_URL set, the same suites run on PostgresStore."""

import os

import psycopg

from ev.pgstore import PostgresStore
from ev.store import InMemoryStore

DATABASE_URL = os.environ.get("EV_TEST_DATABASE_URL")


def new_store(fresh: bool = True) -> InMemoryStore:
    """A store on an empty database, or (fresh=False) one more replica on the same database."""
    if not DATABASE_URL:
        return InMemoryStore()
    if fresh:
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute("DROP TABLE IF EXISTS state")
    return PostgresStore(DATABASE_URL)
