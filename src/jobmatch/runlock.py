"""One pipeline run per profile at a time.

A scheduled run, a manual run and the browser app's Run button can all start
the same pipeline. Two copies share one LinkedIn guest quota and one SQLite
file, so the second one makes both slower. The lock is an OS file lock: it is
released when the process exits, even after a crash or a kill, so there is no
stale lock to clean up.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path


class RunLocked(RuntimeError):
    """Another run already holds this profile's lock."""


@contextmanager
def run_lock(directory: Path):
    path = Path(directory) / "run.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")  # noqa: SIM115 -- held for the whole run
    try:
        if not _try_lock(handle):
            raise RunLocked(f"another JobMatch run is already using {path.parent}")
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        yield path
    finally:
        handle.close()  # closing the file releases the lock


def _try_lock(handle) -> bool:
    if os.name == "nt":
        import msvcrt

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False
