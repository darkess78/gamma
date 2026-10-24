"""Bounded cleanup of unreferenced inference image snapshots."""
import math
from pathlib import Path
import stat
import time

from ..delivery.scan import ArtifactScan


class ImageMaintenance:
    def __init__(self, root, admission):
        self.root = Path(root)
        self.admission = admission
        self.scan = ArtifactScan(name_pattern=r"[0-9a-f]{32}\.bin")

    def sweep(self, *, now=None, retention_seconds=86400, batch_size=64):
        if type(batch_size) is not int or not 1 <= batch_size <= 256:
            raise ValueError("invalid image maintenance batch")
        if not math.isfinite(retention_seconds) or retention_seconds < 3600:
            raise ValueError("invalid image retention")
        now = time.time() if now is None else now
        if not math.isfinite(now):
            raise ValueError("invalid image maintenance time")
        counts = dict(artifacts=0, bytes=0, scanned=0, errors=0, deferred=0)
        if self.root.is_symlink():
            raise ValueError("image snapshot root must not be a symlink")
        with self.admission.idle_guard("brain-inference") as idle:
            if not idle:
                counts["deferred"] = 1
                return counts
            # Producers acquire this same admission before creating snapshots.
            # Active and uncertain leases protect all images, regardless of age.
            paths, counts["scanned"] = self.scan.next_batch(self.root, limit=batch_size)
            for path in paths:
                try:
                    if self.root.is_symlink() or path.is_symlink() or path.resolve().parent != self.root.resolve():
                        continue
                    info = path.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_mtime >= now - retention_seconds:
                        continue
                    path.unlink()
                    counts["artifacts"] += 1
                    counts["bytes"] += info.st_size
                except OSError:
                    counts["errors"] += 1
        return counts

    def close(self):
        self.scan.close()
