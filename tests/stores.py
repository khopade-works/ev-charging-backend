"""Every suite runs against InMemoryStore; with EV_TEST_DATABASE_URL set, the same suites run against Postgres."""

import os

from ev.store import InMemoryStore, Store

DATABASE_URL = os.environ.get("EV_TEST_DATABASE_URL")
opened = []  # closed after each test by the autouse fixture in conftest.py


def new_store(fresh: bool = True) -> Store:
    """A store on an empty database (fresh) or another 'replica' on the same database (fresh=False)."""
    if not DATABASE_URL:
        assert fresh, "replicas only exist with Postgres"
        return InMemoryStore()
    from ev.pgstore import PostgresStore

    store = PostgresStore(DATABASE_URL)
    opened.append(store)
    if fresh:
        store.ping()  # creates the schema on a brand-new database
        with store._pool.connection() as conn:
            conn.execute("TRUNCATE drivers, stations, sessions, promos")
            for kind in ("driver", "station", "session"):
                conn.execute(f"ALTER SEQUENCE {kind}_id_seq RESTART")
    return store
