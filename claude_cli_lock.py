"""Cross-process lock guarding claude.cmd invocations.

telegram_claude_bridge.py and corvus_hack/master_dispatcher.py are separate
OS processes that both shell out to the same claude.cmd, which reads and
atomically rewrites the shared ~/.claude/.credentials.json OAuth token on
every call. Two invocations landing at the same instant can race that
file's temp-write-then-rename, producing a transient "Not logged in"
failure even though the token on disk is valid. telegram_claude_bridge.py's
_claude_lock only serializes calls *within its own process* — it does
nothing to keep master_dispatcher.py from overlapping with it.

This lock is backed by an OS file lock (msvcrt.locking), not a
lock-file's mere existence, so it is automatically released by Windows if
the holding process dies or is killed — no stale-lock cleanup needed.
"""
from __future__ import annotations

import contextlib
import msvcrt
import os
import time

_LOCK_PATH = r"E:\Corvus_Careebridge\logs\claude_cli.lock"


@contextlib.contextmanager
def claude_cli_lock(timeout: float = 180.0):
    """Block until the cross-process Claude CLI lock is free, then hold it.

    Raises TimeoutError if it can't be acquired within `timeout` seconds.
    """
    os.makedirs(os.path.dirname(_LOCK_PATH), exist_ok=True)
    fh = open(_LOCK_PATH, "a+b")
    try:
        deadline = time.monotonic() + timeout
        while True:
            fh.seek(0)
            try:
                # LK_LOCK blocks internally for ~10s per attempt before raising;
                # we loop that against our own longer deadline.
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Timed out after {timeout}s waiting for Claude CLI lock"
                    )
        try:
            yield
        finally:
            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
    finally:
        fh.close()
