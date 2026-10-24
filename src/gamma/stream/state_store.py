"""Transaction adapter for the legacy stream SQL schema."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from sqlalchemy.pool import NullPool
from sqlmodel import create_engine

from ..memory.state_authority import StateAuthority, StateConflict


class StreamStateStore:
    def _initialize_authority(self):
        self._engine = create_engine(f"sqlite:///{self.path}", poolclass=NullPool)
        self._authority = StateAuthority(self._engine)

    @contextmanager
    def _write_connect(self, session=None):
        if session is None:
            with self._authority.transaction(operator=True) as owned:
                with self._write_connect(owned) as conn:
                    yield conn
                owned.commit()
            return
        StateAuthority.require_transaction(session)
        if Path(session.get_bind().url.database).resolve() != self.path.resolve():
            raise StateConflict("stream_state_database_mismatch")
        conn = session.connection().connection.driver_connection
        old_factory = conn.row_factory
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.row_factory = old_factory
