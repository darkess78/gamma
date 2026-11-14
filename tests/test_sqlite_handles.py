"""The small SQLite stores close every connection, so a long stream doesn't run out of file handles."""
import gc
import os

from gamma.integrations.twitch.trust import ViewerTrustStore
from gamma.stream.temp_memory import StreamTempMemoryStore


def _open_files() -> int:
    return len(os.listdir("/proc/self/fd"))


def test_stores_do_not_leak_connections(tmp_path):
    url = "sqlite:///" + str(tmp_path / "s.db")
    trust, temp = ViewerTrustStore(database_url=url), StreamTempMemoryStore(database_url=url)
    gc.disable()  # without the fix, connections were only freed by the garbage collector
    try:
        before = _open_files()
        for n in range(200):
            trust.trust_level_for(platform="twitch", platform_user_id=f"v{n}", default="new_viewer")
            temp.upsert(bucket="event_history", key=f"k{n % 5}", value="x")
            temp.list_records(limit=5)
        assert _open_files() - before < 5
    finally:
        gc.enable()
