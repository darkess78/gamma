import os
import sqlite3
import tempfile
from pathlib import Path


def verified_backup(path: Path) -> Path | None:
    """Create a verified, unique backup of the SQLite database at ``path``.

    The source database is opened read-only; missing or empty sources yield
    ``None`` without creating anything.  The backup is written to a new file
    inside ``<parent>/backups/`` using SQLite's online backup API so that
    WAL changes are included.  After the backup is written, its integrity is
    checked and the file is fsynced before returning.  On any failure the
    partially created backup is removed and the error is raised. Migration must
    never continue without a verified backup of an existing database.
    """
    path = Path(path).resolve()
    if not path.exists() or path.stat().st_size == 0:
        return None

    backup_dir = path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)

    descriptor, name = tempfile.mkstemp(prefix=f"{path.stem}-", suffix=".db", dir=backup_dir)
    os.close(descriptor)
    dest_path = Path(name)

    src_conn = None
    dest_conn = None
    try:
        src_uri = path.as_uri() + "?mode=ro"
        src_conn = sqlite3.connect(src_uri, uri=True, isolation_level=None)
        dest_conn = sqlite3.connect(str(dest_path))
        src_conn.backup(dest_conn)
        dest_conn.commit()
        dest_conn.close()
        dest_conn = None

        # Verify the backup integrity.
        check_conn = sqlite3.connect(dest_path.as_uri() + "?mode=ro", uri=True)
        try:
            result = check_conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            check_conn.close()
        if result[0] != "ok":
            raise sqlite3.DatabaseError("Backup integrity verification failed")

        os.chmod(dest_path, 0o600)
        with open(dest_path, "rb") as f:
            os.fsync(f.fileno())
        return dest_path

    except Exception:
        if dest_conn is not None:
            dest_conn.close()
            dest_conn = None
        if dest_path.exists():
            try:
                dest_path.unlink()
            except OSError:
                pass
        raise
    finally:
        if src_conn:
            src_conn.close()
        if dest_conn:
            dest_conn.close()
