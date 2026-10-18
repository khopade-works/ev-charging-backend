import pickle
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from ev.store import InMemoryStore


class PostgresStore(InMemoryStore):
    """InMemoryStore whose state is one Postgres row, shared by all replicas: each call locks, loads, runs, saves.

    ponytail: O(data) per call and serialised writes; upgrade to a table per aggregate + conditional claim UPDATE.
    pickle is safe here: only this app writes the row, and only API pods can reach the database (NetworkPolicy).
    """

    def __init__(self, conninfo: str = "") -> None:  # "": libpq reads PGHOST, PGPASSWORD, ... from the environment
        super().__init__()
        self._conninfo = conninfo

    def ping(self) -> None:
        with psycopg.connect(self._conninfo, connect_timeout=1) as conn:
            conn.execute("SELECT 1")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with psycopg.connect(self._conninfo, connect_timeout=5,  # commits on success, rolls back on error
                             options="-c idle_in_transaction_session_timeout=10s") as conn:  # frees the lock if a client dies
            conn.execute("SELECT pg_advisory_xact_lock(1)")
            conn.execute("CREATE TABLE IF NOT EXISTS state (id int PRIMARY KEY, data bytea NOT NULL)")
            row = conn.execute("SELECT data FROM state WHERE id = 1").fetchone()
            super().__init__()  # empty state, unless one was saved
            if row:
                vars(self).update(pickle.loads(row[0]))
            yield
            state = {k: v for k, v in vars(self).items() if k != "_conninfo"}
            conn.execute("INSERT INTO state VALUES (1, %s) ON CONFLICT (id) DO UPDATE SET data = excluded.data",
                         (pickle.dumps(state),))
