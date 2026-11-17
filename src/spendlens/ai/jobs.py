"""Live Steampipe queries as background jobs, so a slow query makes the assistant wait instead of failing.

`submit()` starts the query on its own read-only Steampipe connection (at most `max_workers` at once).
`wait(job_id, seconds)` blocks up to `seconds`: it returns the rows, or {"status": "running"} so the model can call
aws_query_result to keep waiting. A query running past `timeout_s` is cancelled (and Steampipe's own
statement_timeout stops it server-side too). AWS throttling is retried once after a short pause.
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

THROTTLE = re.compile(r"Throttl|Rate exceeded|TooManyRequests|RequestLimitExceeded", re.IGNORECASE)


@dataclass
class Job:
    id: str
    sql: str
    started: float
    future: Future
    client: Any = None
    cancelled: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started


class QueryJobs:
    def __init__(self, make_client: Callable[[], Any], *, max_rows: int, timeout_s: int, max_workers: int = 2,
                 retry_pause_s: float = 3.0):
        self.make_client, self.max_rows, self.timeout_s = make_client, max_rows, timeout_s
        self.retry_pause_s = retry_pause_s
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="spendlens-query")
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._n = 0

    def submit(self, sql: str, **meta: Any) -> Job:
        with self._lock:
            self._n += 1
            job_id = f"q{self._n}"
        job = Job(job_id, sql, time.monotonic(), Future(), meta=meta)
        job.future = self.pool.submit(self._run, job)
        self.jobs[job_id] = job
        return job

    def _run(self, job: Job) -> list[dict[str, Any]]:
        from ..steampipe import SteampipeError

        for attempt in (1, 2):
            client = self.make_client()
            job.client = client
            try:
                return client.query(job.sql, limit=self.max_rows + 1)
            except SteampipeError as e:
                if attempt == 1 and THROTTLE.search(str(e)) and not job.cancelled:
                    time.sleep(self.retry_pause_s)
                    continue
                raise
            finally:
                client.close()
        return []  # unreachable

    def wait(self, job_id: str, wait_s: float) -> dict[str, Any]:
        """{"status": "done", "rows": [...]} | {"status": "running", ...} | {"status": "error", "error": ...}."""
        job = self.jobs.get(job_id)
        if job is None:
            return {"status": "error", "error": f"unknown job_id {job_id}"}
        remaining = self.timeout_s - job.elapsed
        try:
            rows = job.future.result(timeout=max(0.0, min(wait_s, remaining)))
            return {"status": "done", "rows": rows, "elapsed_s": round(job.elapsed, 1)}
        except TimeoutError:
            if job.elapsed >= self.timeout_s:
                self.cancel(job)
                return {"status": "error", "elapsed_s": round(job.elapsed, 1),
                        "error": f"the query ran longer than {self.timeout_s}s (llm.query_timeout_s) and was cancelled"}
            return {"status": "running", "job_id": job.id, "elapsed_s": round(job.elapsed, 1)}
        except Exception as e:  # noqa: BLE001 - reported to the model, never raised
            return {"status": "error", "error": str(e).strip()[:2000], "elapsed_s": round(job.elapsed, 1)}

    def cancel(self, job: Job) -> None:
        job.cancelled = True
        job.future.cancel()
        conn = getattr(job.client, "_conn", None)
        fn = next((getattr(conn, n) for n in ("cancel_safe", "cancel") if conn is not None and hasattr(conn, n)), None)
        if fn is not None:
            with contextlib.suppress(Exception):  # best effort; Steampipe's statement_timeout stops it anyway
                fn()

    def running(self) -> list[Job]:
        return [j for j in self.jobs.values() if not j.future.done()]

    def close(self) -> None:
        for j in self.running():
            self.cancel(j)
        self.pool.shutdown(wait=False, cancel_futures=True)
