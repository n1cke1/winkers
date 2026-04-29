"""Hook invocation logger — append-only JSONL per Claude session.

Each hook wraps its `run()` body in `log_hook(...)`. On exit (success,
SystemExit from `sys.exit(N)`, or unexpected exception) the context
manager appends one line to `.winkers/sessions/<session_id>/hooks.log`.

Writing the log line is best-effort: if the filesystem is read-only or
the directory can't be created, the hook still completes normally.
Logging must never break the hook.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from winkers.session.session_dir import get_session_dir

HOOKS_LOG_NAME = "hooks.log"


@contextmanager
def log_hook(
    root: Path,
    session_id: str,
    event: str,
    hook: str,
) -> Iterator[MutableMapping[str, object]]:
    """Yield a mutable record dict; append it as JSONL on exit.

    The hook can populate fields on the yielded dict (file path,
    warnings emitted, decision, …) — those land in the log line.

    `outcome` defaults to "ok"; it is overwritten when:
      * the hook calls `sys.exit(N)` — outcome becomes "exit_N"
      * an unhandled exception bubbles up — outcome becomes "error: …"
    """
    record: dict[str, object] = {
        "ts": datetime.now(UTC).isoformat(),
        "session_id": session_id or "",
        "event": event,
        "hook": hook,
        "outcome": "ok",
    }
    started = time.monotonic()
    try:
        yield record
    except SystemExit as exc:
        code = exc.code if exc.code is not None else 0
        if code == 0:
            record["outcome"] = "ok"
        else:
            record["outcome"] = f"exit_{code}"
        raise
    except BaseException as exc:
        record["outcome"] = f"error: {type(exc).__name__}: {exc}"
        raise
    finally:
        record["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
        try:
            _write_record(root, session_id, record)
        except Exception:
            # Logging must never break the hook — defense-in-depth in case
            # _write_record itself fails (and isn't already wrapped by tests).
            pass


def _write_record(
    root: Path, session_id: str, record: MutableMapping[str, object]
) -> None:
    try:
        sess_dir = get_session_dir(root, session_id)
        log_path = sess_dir / HOOKS_LOG_NAME
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        return
