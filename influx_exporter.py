"""
Influx Exporter Module for SecondX Infrastructure Telemetry Agent
Author: İlhan Koçaslan (Proaiml)

Streams second-level infrastructure metrics to an InfluxDB v2 time-series
database without ever slowing down the sampling loop:

* Every sample is handed to a background thread (non-blocking).
* Points are written in batches using the line protocol with the original
  sample timestamps (re-sending a point is idempotent in InfluxDB).
* If InfluxDB is unreachable, points wait in a small RAM buffer; a separate
  spool thread moves them to disk in blocks so RAM stays bounded. Blocks are
  exported oldest-first when the connection returns. Nothing is dropped:
  when a safety limit is reached (outage too long, spool too big, disk too
  full) the exporter reports it and the agent stops.
* Without an InfluxDB token the agent runs in local mode and writes the same
  points to daily JSON-lines files (``data/metrics-YYYY-MM-DD.jsonl``) with
  automatic retention.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import shutil
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
    """Non-blocking exporter with a bounded RAM buffer and a disk spool.

    Threads
    -------
    * ``secondx-sender``  writes to the sink: first any spooled blocks (oldest
      first), then the in-memory buffer, in batches. On failure it backs off
      (1 s ... 60 s) and never drops data. While catching up on a backlog it
      paces itself by its own measured CPU time (``backlog_cpu_percent`` of
      one core, averaged over the whole catch-up), so the agent stays well
      below its own CPU limit even after hours of outage.
    * ``secondx-spooler`` (only when ``spool_dir`` is set) moves the oldest
      in-memory points to disk in fixed-size blocks while the sink is failing,
      so RAM stays bounded during long outages. Disk I/O never runs in the
      sampling thread and never blocks the sender. The list of blocks and
      their total size are kept in memory - the directory is scanned only once
      at start-up, so cost does not grow with the length of the outage.

    Safety limits (any of them sets ``fatal_reason``; the agent then stops):
    * the sink has been failing for longer than ``max_outage_seconds``
    * the spool grew beyond ``max_spool_bytes``
    * free disk space fell below ``min_free_disk_bytes``
    * the RAM buffer is full (``max_memory_points``) - disk too slow or no spool
    """

    DISK_CHECK_SECONDS = 10.0

    def __init__(self, sink: Sink, max_memory_points: int = 30_000, max_batch_points: int = 5_000,
                 spool_dir: Path | None = None, spool_block_points: int = 1_500,
                 max_outage_seconds: float = 6 * 3600, max_spool_bytes: int = 1 << 30,
                 min_free_disk_bytes: int = 1 << 30, backlog_cpu_percent: float = 10.0,
                 backlog_min_pause_seconds: float = 0.02) -> None:
        self.sink = sink
        self.max_memory_points = max(1_000, int(max_memory_points))
        self.max_batch_points = max(100, int(max_batch_points))
        self.spool_dir = Path(spool_dir) if spool_dir else None
        self.spool_block_points = max(100, int(spool_block_points))
        self.max_outage_seconds = float(max_outage_seconds)
        self.max_spool_bytes = int(max_spool_bytes)
        self.min_free_disk_bytes = int(min_free_disk_bytes)
        self.backlog_cpu_share = min(1.0, max(0.01, float(backlog_cpu_percent) / 100.0))
        self.backlog_min_pause = max(0.0, float(backlog_min_pause_seconds))
        self._mem: deque[Point] = deque()
        self._blocks: deque[tuple[Path, int]] = deque()      # (path, bytes), oldest first
        self._spool_size = 0
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._seq = 0
        self._last_disk_check = 0.0
        self.written = 0
        self.spooled = 0
        self.failures = 0
        self.last_error = ""
        self.fatal_reason: str | None = None
        self.failing_since: float | None = None
        if self.spool_dir:
            self.spool_dir.mkdir(parents=True, exist_ok=True)
            for tmp in self.spool_dir.glob("block-*.tmp"):    # interrupted writes: incomplete, discard
                try:
                    tmp.unlink()
                except OSError:
                    pass
            for path in sorted(self.spool_dir.glob("block-*.jsonl")):
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                self._blocks.append((path, size))
                self._spool_size += size
            if self._blocks:
                log.info("%d spooled block(s) from a previous run will be exported first.", len(self._blocks))
        self._sender = threading.Thread(target=self._send_loop, name="secondx-sender", daemon=True)
        self._sender.start()
        self._spooler = None
        if self.spool_dir:
            self._spooler = threading.Thread(target=self._spool_loop, name="secondx-spooler", daemon=True)
            self._spooler.start()

    # ---------------------------------------------------------------- state
    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._mem)

    @property
    def spooled_blocks(self) -> int:
        with self._lock:
            return len(self._blocks)

    def spool_bytes(self) -> int:
        with self._lock:
            return self._spool_size

    def _fatal(self, reason: str) -> None:
        if self.fatal_reason is None:
            self.fatal_reason = reason
            log.error("Export safety limit reached: %s", reason)

    def _mark_failure(self, exc: Exception) -> None:
        self.failures += 1
        self.last_error = f"{type(exc).__name__}: {exc}"[:300]
        if self.failing_since is None:
            self.failing_since = time.monotonic()
            log.warning("Export to %s failed (%s); data is kept and retried.", self.sink.name, self.last_error)
        self._check_limits()

    def _mark_success(self) -> None:
        if self.failing_since is not None:
            log.info("Export to %s recovered after %s; backlog is being flushed.",
                     self.sink.name, self._duration(time.monotonic() - self.failing_since))
        self.failing_since = None

    @staticmethod
    def _duration(seconds: float) -> str:
        if seconds < 120:
            return f"{seconds:.0f} s"
        if seconds < 7200:
            return f"{seconds / 60:.0f} min"
        return f"{seconds / 3600:.1f} h"

    def _check_limits(self) -> None:
        if self.failing_since is not None:
            down = time.monotonic() - self.failing_since
            if down > self.max_outage_seconds:
                self._fatal(f"{self.sink.name} unreachable for {self._duration(down)} "
                            f"(limit {self._duration(self.max_outage_seconds)})")
        if not self.spool_dir:
            return
        size = self.spool_bytes()
        if size > self.max_spool_bytes:
            self._fatal(f"spool size {size / 2**20:.1f} MB exceeds limit {self.max_spool_bytes / 2**20:.1f} MB")
        now = time.monotonic()
        if now - self._last_disk_check >= self.DISK_CHECK_SECONDS:
            self._last_disk_check = now
            try:
                free = shutil.disk_usage(self.spool_dir).free
            except OSError:
                return
            if free < self.min_free_disk_bytes:
                self._fatal(f"free disk space {free / 2**20:.0f} MB below limit "
                            f"{self.min_free_disk_bytes / 2**20:.0f} MB")

    # ---------------------------------------------------------------- producer side (sampling thread)
    def submit(self, points: list[Point]) -> None:
        """O(1), never blocks on I/O."""
        if not points:
            return
        with self._lock:
            self._mem.extend(points)
            full = len(self._mem) > self.max_memory_points
        if full:
            self._fatal(f"RAM buffer full ({self.max_memory_points} points): "
                        + ("disk spool cannot keep up" if self.spool_dir else "no disk spool configured"))
        self._wake.set()

    # ---------------------------------------------------------------- spool
    def _spool_files(self) -> list[Path]:
        with self._lock:
            return [path for path, _ in self._blocks]

    def _write_block(self, block: list[Point]) -> None:
        self._seq += 1
        final = self.spool_dir / f"block-{int(block[0]['time_ns']):020d}-{self._seq:06d}.jsonl"
        tmp = final.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write("".join(json.dumps(p, ensure_ascii=False, separators=(",", ":")) + "\n" for p in block))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
        size = final.stat().st_size
        with self._lock:
            self._blocks.append((final, size))
            self._spool_size += size
        self.spooled += len(block)

    @staticmethod
    def _read_block(path: Path) -> list[Point]:
        with path.open("r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def _above_high_water(self) -> bool:
        with self._lock:
            return len(self._mem) > self.max_memory_points // 2

    def _spool_loop(self) -> None:
        while not self._stop.wait(0.5):
            self._check_limits()
            while self.failing_since is not None or self._above_high_water():
                with self._lock:
                    if len(self._mem) < self.spool_block_points:
                        break
                    block = [self._mem.popleft() for _ in range(self.spool_block_points)]
                try:
                    self._write_block(block)
                except OSError as exc:
                    with self._lock:
                        self._mem.extendleft(reversed(block))
                    self._fatal(f"cannot write spool block: {exc}")
                    break

    # ---------------------------------------------------------------- sender
    def _send_one(self) -> int:
        """Sends one spooled block or one RAM batch. Returns points sent (0 = nothing to do)."""
        with self._lock:
            head = self._blocks[0] if self._blocks else None
        if head is not None:
            path, size = head
            points = self._read_block(path)
            for i in range(0, len(points), self.max_batch_points):
                self.sink.write(points[i:i + self.max_batch_points])
            path.unlink()
            with self._lock:
                self._blocks.popleft()
                self._spool_size -= size
            return len(points)
        with self._lock:
            n = min(len(self._mem), self.max_batch_points)
            batch = [self._mem.popleft() for _ in range(n)]
        if not batch:
            return 0
        try:
            self.sink.write(batch)
        except Exception:
            with self._lock:
                self._mem.extendleft(reversed(batch))
            raise
        return len(batch)

    def _has_backlog(self) -> bool:
        with self._lock:
            return bool(self._blocks) or len(self._mem) >= self.max_batch_points

    def _send_loop(self) -> None:
        backoff = 1.0
        session: tuple[float, float] | None = None       # (thread cpu, wall) at start of a catch-up
        while not self._stop.is_set():
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            while not self._stop.is_set():
                backlog = self._has_backlog()
                if backlog and session is None:
                    session = (time.thread_time(), time.monotonic())
                try:
                    sent = self._send_one()
                except Exception as exc:  # noqa: BLE001 - keep data, retry later
                    self._mark_failure(exc)
                    session = None
                    if self._stop.wait(backoff):
                        return
                    backoff = min(backoff * 2, 60.0)
                    continue
                if sent:
                    self.written += sent
                    self._mark_success()
                    backoff = 1.0
                if not backlog or not sent:
                    session = None
                    if not sent:
                        break
                    continue
                # Catching up: keep the average CPU of this thread at backlog_cpu_share.
                cpu = time.thread_time() - session[0]
                wall = time.monotonic() - session[1]
                if self._stop.wait(max(self.backlog_min_pause, cpu / self.backlog_cpu_share - wall)):
                    return

    # ---------------------------------------------------------------- shutdown
    def close(self, timeout: float = 5.0) -> None:
        """Flushes the RAM buffer within ``timeout`` if the sink is healthy; whatever
        is left goes to the disk spool (exported on next start)."""
        end = time.monotonic() + timeout
        self._wake.set()
        while self.pending and self.failing_since is None and time.monotonic() < end and self._sender.is_alive():
            time.sleep(0.05)
        self._stop.set()
        self._wake.set()
        self._sender.join(timeout=max(0.2, end - time.monotonic()))
        if self._spooler:
            self._spooler.join(timeout=2.0)
        with self._lock:
            rest = list(self._mem)
            self._mem.clear()
        if rest and self.spool_dir:
            try:
                for i in range(0, len(rest), self.spool_block_points):
                    self._write_block(rest[i:i + self.spool_block_points])
                log.info("%d unsent points saved to the spool; they will be exported on next start.", len(rest))
                rest = []
            except OSError as exc:
                log.error("Could not spool unsent points: %s", exc)
        if rest:
            log.warning("%d points could not be exported before shutdown.", len(rest))
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
