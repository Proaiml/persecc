"""
Influx Exporter Module for SecondX Infrastructure Telemetry Agent
Author: İlhan Koçaslan (Proaiml)

Streams second-level infrastructure metrics to an InfluxDB v2 time-series
database without ever slowing down the sampling loop:

* Every sample is handed to a background thread (non-blocking).
* All points of a sample (and any backlog) are written in ONE batched request
  using the line protocol with the original sample timestamps.
* If InfluxDB is unreachable the points are kept in a bounded in-memory
  buffer and retried with exponential backoff (1 s ... 60 s). Nothing is lost
  unless the buffer limit is exceeded, in which case the oldest points are
  dropped and a warning is logged.
* Without an InfluxDB token the agent runs in local mode and writes the same
  points to daily JSON-lines files (``data/metrics-YYYY-MM-DD.jsonl``) with
  automatic retention.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("secondx.exporter")

Point = dict[str, Any]  # {"measurement", "tags", "fields", "time_ns"}


# ---------------------------------------------------------------- line protocol
def _escape_key(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace(",", r"\,").replace("=", r"\=").replace(" ", r"\ ")


def _escape_measurement(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace(",", r"\,").replace(" ", r"\ ")


def to_line(point: Point) -> str:
    """Converts a point dict to InfluxDB line protocol (nanosecond precision)."""
    tags = "".join(f",{_escape_key(k)}={_escape_key(v)}"
                   for k, v in sorted(point.get("tags", {}).items()) if str(v) != "")
    fields = ",".join(f"{_escape_key(k)}={float(v)!r}" for k, v in point["fields"].items())
    return f"{_escape_measurement(point['measurement'])}{tags} {fields} {int(point['time_ns'])}"


# ---------------------------------------------------------------- sinks
class Sink(Protocol):
    name: str

    def write(self, points: list[Point]) -> None: ...

    def close(self) -> None: ...


class InfluxSink:
    """Writes batches to InfluxDB v2 (``influxdb-client`` package)."""

    name = "influxdb"

    def __init__(self, url: str, token: str, org: str, bucket: str, timeout_ms: int = 5000) -> None:
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS

        self.url, self.org, self.bucket = url, org, bucket
        self._client = InfluxDBClient(url=url, token=token, org=org, timeout=int(timeout_ms))
        self._write = self._client.write_api(write_options=SYNCHRONOUS)

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:  # noqa: BLE001 - any transport error means "not reachable"
            return False

    def write(self, points: list[Point]) -> None:
        from influxdb_client import WritePrecision

        self._write.write(bucket=self.bucket, org=self.org, record=[to_line(p) for p in points],
                          write_precision=WritePrecision.NS)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass


class LocalSink:
    """Daily JSON-lines files, e.g. ``data/metrics-2026-09-26.jsonl``."""

    name = "local"
    _PATTERN = re.compile(r"^metrics-(\d{4}-\d{2}-\d{2})\.jsonl$")

    def __init__(self, directory: Path, retention_days: int = 14) -> None:
        self.directory = Path(directory)
        self.retention_days = max(1, int(retention_days))
        self.directory.mkdir(parents=True, exist_ok=True)
        self._last_cleanup = ""

    def write(self, points: list[Point]) -> None:
        by_day: dict[str, list[str]] = {}
        for p in points:
            ts = dt.datetime.fromtimestamp(int(p["time_ns"]) / 1e9)
            by_day.setdefault(ts.strftime("%Y-%m-%d"), []).append(json.dumps({
                "time": ts.isoformat(timespec="milliseconds"),
                "measurement": p["measurement"], "tags": p.get("tags", {}),
                "fields": {k: round(float(v), 4) for k, v in p["fields"].items()},
            }, ensure_ascii=False))
        for day, lines in by_day.items():
            with (self.directory / f"metrics-{day}.jsonl").open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        self.cleanup()

    def cleanup(self) -> None:
        today = dt.date.today()
        if self._last_cleanup == today.isoformat():
            return
        self._last_cleanup = today.isoformat()
        limit = today - dt.timedelta(days=self.retention_days)
        for path in self.directory.iterdir():
            m = self._PATTERN.match(path.name)
            if not m:
                continue  # never touch unknown files
            try:
                if dt.date.fromisoformat(m.group(1)) < limit:
                    path.unlink()
            except (ValueError, OSError):
                continue

    def close(self) -> None:
        pass


# ---------------------------------------------------------------- async exporter
class Exporter:
    """Non-blocking, batching, retrying exporter running in a daemon thread."""

    def __init__(self, sink: Sink, max_buffer_points: int = 200_000, max_batch_points: int = 5_000) -> None:
        self.sink = sink
        self.max_buffer_points = max(1_000, int(max_buffer_points))
        self.max_batch_points = max(100, int(max_batch_points))
        self._buffer: deque[Point] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.written = 0
        self.dropped = 0
        self.failures = 0
        self.last_error = ""
        self._thread = threading.Thread(target=self._run, name="secondx-exporter", daemon=True)
        self._thread.start()

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._buffer)

    def submit(self, points: list[Point]) -> None:
        if not points:
            return
        with self._lock:
            self._buffer.extend(points)
            overflow = len(self._buffer) - self.max_buffer_points
            if overflow > 0:
                for _ in range(overflow):
                    self._buffer.popleft()
                self.dropped += overflow
        self._wake.set()

    def _take(self) -> list[Point]:
        with self._lock:
            return [self._buffer[i] for i in range(min(len(self._buffer), self.max_batch_points))]

    def _commit(self, count: int) -> None:
        with self._lock:
            for _ in range(min(count, len(self._buffer))):
                self._buffer.popleft()

    def _run(self) -> None:
        backoff = 1.0
        last_warning = 0.0
        while True:
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            while True:
                batch = self._take()
                if not batch:
                    break
                try:
                    self.sink.write(batch)
                except Exception as exc:  # noqa: BLE001 - keep data, retry later
                    self.failures += 1
                    self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                    now = time.monotonic()
                    if now - last_warning > 60:
                        log.warning("Export to %s failed (%s). %d points buffered, retrying in %.0fs.",
                                    self.sink.name, self.last_error, self.pending, backoff)
                        last_warning = now
                    if self._stop.wait(backoff):
                        return
                    backoff = min(backoff * 2, 60.0)
                    continue
                if self.failures and backoff > 1.0:
                    log.info("Export to %s recovered; backlog is being flushed.", self.sink.name)
                backoff = 1.0
                self._commit(len(batch))
                self.written += len(batch)
            if self._stop.is_set():
                return

    def close(self, timeout: float = 5.0) -> None:
        """Flushes the buffer (best effort within ``timeout``) and stops the thread."""
        end = time.monotonic() + timeout
        self._wake.set()
        while self.pending and time.monotonic() < end and self._thread.is_alive():
            time.sleep(0.05)
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=max(0.1, end - time.monotonic()))
        if self.pending:
            log.warning("%d points could not be exported before shutdown.", self.pending)
        self.sink.close()


# ---------------------------------------------------------------- backwards compatibility
def influx_creator(org, bucket, tablen, field_name, tag0, tag1, value, repeatedly=2, timezi=1):
    """Deprecated single-point API kept for scripts written against SecondX 1.x.

    Points go through the same non-blocking exporter as the agent when one is
    active (see :func:`set_default_exporter`); otherwise the call is a no-op.
    """
    if _default_exporter is None:
        return False
    _default_exporter.submit([{
        "measurement": str(tablen), "tags": {"process_or_metric": str(tag0), "host_tag": str(tag1)},
        "fields": {str(field_name): float(value)}, "time_ns": time.time_ns(),
    }])
    return True


_default_exporter: Exporter | None = None


def set_default_exporter(exporter: Exporter | None) -> None:
    global _default_exporter
    _default_exporter = exporter
