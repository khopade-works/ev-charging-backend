import pickle
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from ev.store import InMemoryStore

# The session timeout frees the lock if a client vanishes mid-transaction (e.g. its node dies without a TCP reset).
OPTIONS = "-c idle_in_transaction_session_timeout=10s"


class PostgresStore(InMemoryStore):
    """InMemoryStore whose state lives in one Postgres row, shared by every replica.

    Each transaction takes a global advisory lock, loads the state, runs the service call, and saves the state.
    ponytail: whole state per call + one lock = O(data) per request and serialised writes.
    Upgrade: a table per aggregate and a conditional claim (UPDATE ... WHERE status='AVAILABLE').
    pickle is safe here: only this app writes the row, and only API pods can reach the database.
    """

    def __init__(self, conninfo: str = "") -> None:  # "": libpq reads PGHOST, PGPASSWORD, ... from the environment
        super().__init__()
        self._conninfo = conninfo

    def ping(self) -> None:
        with psycopg.connect(self._conninfo, connect_timeout=1) as conn:
            conn.execute("SELECT 1")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with psycopg.connect(self._conninfo, options=OPTIONS) as conn:  # commits on success, rolls back on error
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
