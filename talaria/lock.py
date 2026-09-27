from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager

from talaria.state import ensure_dir


class Busy(Exception):
    pass


@contextmanager
def op_lock(paths):
    ensure_dir(paths.state_dir)
    fd = os.open(paths.lock_file, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Busy() from None
        yield
    finally:
        os.close(fd)
