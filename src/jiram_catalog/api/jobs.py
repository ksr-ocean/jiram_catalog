"""The in-process job pool behind every button that takes minutes.

Rendering a movie, building a stack and exporting a goflow dataset are
all too slow for a request, so each is submitted to a two-worker thread
pool and the browser polls ``/api/jobs``.  The records live in memory and
are mirrored as JSON under ``<mirror>/gui_cache/jobs/`` -- not as a queue,
which would need a broker, but so that a restarted server can still tell
the user what the last run produced and where it put it.

Two workers, not more: a stack build and a movie render are both bound by
the mirror's I/O, and a third concurrent job on a login node buys queue
depth rather than throughput.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)

#: Worker threads in the pool.
DEFAULT_WORKERS = 2

#: Statuses a job passes through, in order.
STATUSES = ("queued", "running", "done", "failed", "cancelled")

#: A job function: ``fn(progress, **params) -> result``.
JobFunction = Callable[..., Any]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobManager:
    """A registry of job kinds, a thread pool, and the records they make."""

    def __init__(self, cache_dir: Path | str, *, workers: int = DEFAULT_WORKERS) -> None:
        self.cache_dir = Path(cache_dir)
        self._pool = ThreadPoolExecutor(max_workers=int(workers), thread_name_prefix="jiram-job")
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, Future] = {}
        self._kinds: dict[str, JobFunction] = {}
        self._cancelled: set[str] = set()
        self._load_mirror()

    # ------------------------------------------------------------------ kinds
    def register(self, kind: str, function: JobFunction) -> None:
        """Bind a name to the callable ``submit`` will run in a worker."""
        self._kinds[str(kind)] = function

    def kinds(self) -> list[str]:
        return sorted(self._kinds)

    # ------------------------------------------------------------- submission
    def submit(self, kind: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Queue one job of ``kind`` and return its record immediately."""
        if kind not in self._kinds:
            raise KeyError(f"unknown job kind: {kind!r}")
        identifier = f"{kind}-{uuid.uuid4().hex[:8]}"
        record = {
            "id": identifier,
            "kind": str(kind),
            "status": "queued",
            "progress": 0.0,
            "message": "queued",
            "started": None,
            "finished": None,
            "result": None,
            "params": dict(params or {}),
        }
        with self._lock:
            self._records[identifier] = record
        self._write_mirror(identifier)
        future = self._pool.submit(self._run, identifier, self._kinds[kind], dict(params or {}))
        with self._lock:
            self._futures[identifier] = future
        return self.get(identifier)

    def _run(self, identifier: str, function: JobFunction, params: dict[str, Any]) -> None:
        with self._lock:
            if identifier in self._cancelled:
                self._records[identifier].update(
                    status="cancelled", message="cancelled before it started", finished=_now()
                )
                cancelled = True
            else:
                self._records[identifier].update(status="running", message="running", started=_now())
                cancelled = False
        self._write_mirror(identifier)
        if cancelled:
            return

        def progress(fraction: float, message: str | None = None) -> None:
            with self._lock:
                record = self._records[identifier]
                record["progress"] = float(min(max(fraction, 0.0), 1.0))
                if message is not None:
                    record["message"] = str(message)

        try:
            result = function(progress, **params)
        except Exception as exc:  # a failed job is a record, never a crashed server
            LOGGER.exception("job %s failed", identifier)
            with self._lock:
                self._records[identifier].update(
                    status="failed",
                    message=" ".join(str(exc).split())[:500] or exc.__class__.__name__,
                    finished=_now(),
                )
        else:
            with self._lock:
                self._records[identifier].update(
                    status="done",
                    progress=1.0,
                    message="done",
                    finished=_now(),
                    result=_jsonable(result),
                )
        self._write_mirror(identifier)

    # ---------------------------------------------------------------- reading
    def get(self, identifier: str) -> dict[str, Any]:
        with self._lock:
            record = self._records.get(identifier)
            if record is None:
                raise KeyError(identifier)
            return dict(record)

    def list(self) -> list[dict[str, Any]]:
        """Newest first, so the browser's first row is the running job."""
        with self._lock:
            records = [dict(record) for record in self._records.values()]
        return sorted(records, key=lambda record: (record["started"] or "", record["id"]), reverse=True)

    def cancel(self, identifier: str) -> dict[str, Any]:
        """Cancel a queued job; a running one is left to finish."""
        with self._lock:
            record = self._records.get(identifier)
            if record is None:
                raise KeyError(identifier)
            future = self._futures.get(identifier)
            self._cancelled.add(identifier)
            if record["status"] == "queued":
                if future is not None:
                    future.cancel()
                record.update(status="cancelled", message="cancelled", finished=_now())
        self._write_mirror(identifier)
        return self.get(identifier)

    def wait(self, identifier: str, timeout: float = 60.0) -> dict[str, Any]:
        """Block until the job leaves ``queued``/``running`` (tests, mostly)."""
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            record = self.get(identifier)
            if record["status"] not in ("queued", "running"):
                return record
            time.sleep(0.02)
        return self.get(identifier)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ----------------------------------------------------------- disk mirror
    def _path(self, identifier: str) -> Path:
        return self.cache_dir / f"{identifier}.json"

    def _write_mirror(self, identifier: str) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            record = self.get(identifier)
            self._path(identifier).write_text(json.dumps(record, indent=2), encoding="utf-8")
        except (OSError, KeyError, TypeError) as exc:
            LOGGER.warning("cannot mirror job %s: %s", identifier, exc)

    def _load_mirror(self) -> None:
        """Read back finished jobs from an earlier run of the server."""
        if not self.cache_dir.is_dir():
            return
        for path in sorted(self.cache_dir.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                LOGGER.warning("unreadable job record %s: %s", path, exc)
                continue
            if not isinstance(record, dict) or "id" not in record:
                continue
            if record.get("status") in ("queued", "running"):
                # Nothing is running in this process; a record that says so
                # was interrupted by the previous server's exit.
                record["status"] = "failed"
                record["message"] = "interrupted by a server restart"
            self._records[str(record["id"])] = record


def _jsonable(value: Any) -> Any:
    """Paths to strings, numpy scalars to Python, everything else as is."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "item") and getattr(value, "shape", None) == ():
        return value.item()
    return value
