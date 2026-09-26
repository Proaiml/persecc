"""
Influx Exporter Module for SecondX Infrastructure Telemetry Agent
Author: İlhan Koçaslan (Proaiml)

Streams second-level infrastructure metrics to an InfluxDB v2 time-series
database without ever slowing down the sampling loop:

* Every sample is handed to a background thread (non-blocking); live data is
  written immediately and is never queued behind old data.
* Points are written in batches using the line protocol with the original
  sample timestamps (re-sending a point is idempotent in InfluxDB).
* If InfluxDB is unreachable, points wait in a small RAM buffer; a separate
  spool thread moves them to disk in blocks (already in line protocol) so RAM
  stays bounded. When the connection returns, parallel upload workers start
  as needed and upload the blocks within a fixed CPU budget. Nothing is
  dropped: when a safety limit is reached (outage too long, spool too big,
  disk too full) the exporter reports it and the agent stops.
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
class Rejected(Exception):
    """The sink refused the data itself (e.g. HTTP 400): retrying cannot succeed."""


class Sink(Protocol):
    name: str

    def write(self, points: list[Point]) -> None: ...

    def close(self) -> None: ...


class InfluxSink:
    """Writes batches to InfluxDB v2 (``influxdb-client`` package)."""

    name = "influxdb"

    def __init__(self, url: str, token: str, org: str, bucket: str, timeout_ms: int = 5000,
                 connections: int = 8) -> None:
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS

        self.url, self.org, self.bucket = url, org, bucket
        # Keep-alive connection pool (one per live sender / upload worker): no TCP/TLS set-up per write.
        # No hidden client-side retries: every write is bounded by timeout_ms, the exporter retries.
        self._client = InfluxDBClient(url=url, token=token, org=org, timeout=int(timeout_ms),
                                      connection_pool_maxsize=max(2, int(connections)))
        self._write = self._client.write_api(write_options=SYNCHRONOUS)

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:  # noqa: BLE001 - any transport error means "not reachable"
            return False

    def write(self, points: list[Point]) -> None:
        self.write_lines("\n".join(to_line(p) for p in points))

    def write_lines(self, text: str) -> None:
        """Writes ready line protocol (spooled blocks are uploaded as-is, no conversion)."""
        from influxdb_client import WritePrecision
        from influxdb_client.rest import ApiException

        try:
            self._write.write(bucket=self.bucket, org=self.org, record=text, write_precision=WritePrecision.NS)
        except ApiException as exc:
            if exc.status in (400, 422):                       # malformed / refused data: permanent
                raise Rejected(f"HTTP {exc.status}: {str(exc.message or exc.reason)[:200]}") from exc
            raise

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
    """Non-blocking exporter: bounded RAM buffer, disk spool, parallel catch-up.

    The sampling thread only calls ``submit()`` (a lock and a list extend) and
    ``health()``; it never waits for the network or the disk.

    Threads
    -------
    * ``secondx-live`` writes the in-memory buffer to the sink as soon as a
      sample arrives. It is never queued behind old data, so the live view
      stays current even while hours of backlog are being uploaded. On
      failure it backs off (1 s ... 60 s) and never drops data.
    * ``secondx-spooler`` (only with ``spool_dir``) moves the oldest points
      from RAM to disk while the sink is failing (or RAM is half full), in
      fixed-size blocks that are already in InfluxDB line protocol - so
      uploading a block later costs almost no CPU (read file, send bytes).
    * ``secondx-backlog`` (only with ``spool_dir``) starts ``secondx-upload-N``
      workers when blocks are waiting. Uploading is dominated by waiting for
      the server, during which Python releases the GIL, so while writes are
      mostly waiting (measured per block) it adds workers - one per second, up
      to ``backlog_max_workers`` - and they exit when the backlog is gone. All
      workers share one CPU budget (``backlog_cpu_percent`` of one core,
      measured with their own thread CPU time), so a catch-up never competes
      with sampling. While the sink is down a single worker probes with
      back-off.

    Safety limits (any of them sets ``fatal_reason``; the agent then stops):
    * the sink has been failing for longer than ``max_outage_seconds``
    * the spool grew beyond ``max_spool_bytes``
    * free disk space fell below ``min_free_disk_bytes``
    * the RAM buffer is full (``max_memory_points``) - disk too slow or no spool

    Liveness (``health()``; the agent checks it every second and stops if it
    returns a reason): a background thread crashed or exited, or a single sink
    write has been running longer than ``stall_seconds``.

    Data the sink explicitly rejects (``Rejected``, e.g. HTTP 400) cannot
    succeed on retry: such a block is renamed ``*.rejected`` and kept for
    inspection instead of blocking the queue forever; unreadable blocks are
    renamed ``*.bad``.
    """

    DISK_CHECK_SECONDS = 10.0
    SCALE_EVERY_SECONDS = 1.0

    def __init__(self, sink: Sink, max_memory_points: int = 30_000, max_batch_points: int = 5_000,
                 spool_dir: Path | None = None, spool_block_points: int = 1_500,
                 max_outage_seconds: float = 6 * 3600, max_spool_bytes: int = 1 << 30,
                 min_free_disk_bytes: int = 1 << 30, backlog_cpu_percent: float = 10.0,
                 backlog_max_workers: int = 4, backlog_min_pause_seconds: float = 0.0,
                 stall_seconds: float = 30.0) -> None:
        if spool_dir and not hasattr(sink, "write_lines"):
            raise TypeError(f"sink {sink.name!r} cannot upload spooled line-protocol blocks (no write_lines)")
        self.sink = sink
        self.max_memory_points = max(1_000, int(max_memory_points))
        self.max_batch_points = max(100, int(max_batch_points))
        self.spool_dir = Path(spool_dir) if spool_dir else None
        self.spool_block_points = max(100, int(spool_block_points))
        self.max_outage_seconds = float(max_outage_seconds)
        self.max_spool_bytes = int(max_spool_bytes)
        self.min_free_disk_bytes = int(min_free_disk_bytes)
        self.backlog_cpu_share = min(1.0, max(0.01, float(backlog_cpu_percent) / 100.0))
        self.backlog_max_workers = max(1, int(backlog_max_workers))
        self.backlog_min_pause = max(0.0, float(backlog_min_pause_seconds))
        self.stall_seconds = max(1.0, float(stall_seconds))
        self._mem: deque[Point] = deque()
        self._inflight: list[Point] = []                     # live batch currently being written
        self._blocks: deque[tuple[Path, int]] = deque()      # waiting blocks (path, bytes), oldest first
        self._spool_size = 0                                 # bytes on disk incl. blocks being uploaded
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._seq = 0
        self._last_disk_check = 0.0
        self._busy: dict[int, float] = {}                    # thread id -> start of the running write
        self._workers: list[threading.Thread] = []
        self._worker_seq = 0
        self._session: list[float] | None = None             # [cpu used, start] of the current catch-up
        self._wait_share = 1.0                               # share of block upload time spent waiting
        self._max_delay_ms = 0.0                             # sample time -> written, worst in window
        self.written = 0
        self.spooled = 0
        self.failures = 0
        self.last_error = ""
        self.fatal_reason: str | None = None
        self.failing_since: float | None = None
        self.thread_error: str | None = None
        if self.spool_dir:
            self._load_spool()
        self._live = self._thread(self._live_loop, "secondx-live")
        self._spooler = self._thread(self._spool_loop, "secondx-spooler") if self.spool_dir else None
        self._backlog = self._thread(self._backlog_loop, "secondx-backlog") if self.spool_dir else None

    def _load_spool(self) -> None:
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        for tmp in self.spool_dir.glob("block-*.tmp"):        # interrupted writes: incomplete, discard
            try:
                tmp.unlink()
            except OSError:
                pass
        found = [p for p in self.spool_dir.glob("block-*") if p.suffix in (".lp", ".jsonl")]
        for path in sorted(found, key=lambda p: p.stem):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            self._blocks.append((path, size))
            self._spool_size += size
        if self._blocks:
            log.info("%d spooled block(s) from a previous run will be uploaded.", len(self._blocks))

    def _thread(self, loop, name: str) -> threading.Thread:
        thread = threading.Thread(target=self._guarded, args=(loop,), name=name, daemon=True)
        thread.start()
        return thread

    def _guarded(self, loop) -> None:
        try:
            loop()
        except BaseException as exc:  # noqa: BLE001 - a dead thread must never go unnoticed
            self.thread_error = f"{threading.current_thread().name} thread crashed: {type(exc).__name__}: {exc}"[:300]
            log.critical("%s", self.thread_error, exc_info=True)

    # ---------------------------------------------------------------- state
    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._mem)

    @property
    def spooled_blocks(self) -> int:
        with self._lock:
            return len(self._blocks)

    @property
    def backlog_workers(self) -> int:
        return sum(1 for w in self._workers if w.is_alive())

    def spool_bytes(self) -> int:
        with self._lock:
            return self._spool_size

    def health(self) -> str | None:
        """None while the export threads are alive and responsive, else the reason."""
        if self.thread_error:
            return self.thread_error
        if not self._stop.is_set():
            for thread in (self._live, self._spooler, self._backlog):
                if thread is not None and not thread.is_alive():
                    return f"{thread.name} thread stopped unexpectedly"
        started = min(self._busy.values(), default=None)
        if started is not None and time.monotonic() - started > self.stall_seconds:
            return (f"write to {self.sink.name} has been hanging for {time.monotonic() - started:.0f} s "
                    f"(limit {self.stall_seconds:.0f} s)")
        return None

    def take_max_delay_ms(self) -> float:
        """Worst delay between taking a sample and writing it, since the last call."""
        worst, self._max_delay_ms = self._max_delay_ms, 0.0
        return worst

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
        since = self.failing_since
        if since is not None:
            self.failing_since = None
            log.info("Export to %s recovered after %s; backlog is being uploaded.",
                     self.sink.name, self._duration(time.monotonic() - since))

    @staticmethod
    def _duration(seconds: float) -> str:
        if seconds < 120:
            return f"{seconds:.0f} s"
        if seconds < 7200:
            return f"{seconds / 60:.0f} min"
        return f"{seconds / 3600:.1f} h"

    def _check_limits(self) -> None:
        since = self.failing_since
        if since is not None:
            down = time.monotonic() - since
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

    def _timed(self, write, data) -> None:
        """Runs one sink write and registers it for stall detection."""
        key = threading.get_ident()
        self._busy[key] = time.monotonic()
        try:
            write(data)
        finally:
            self._busy.pop(key, None)

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

    # ---------------------------------------------------------------- live sender
    def _live_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            while not self._stop.is_set():
                try:
                    sent = self._send_live()
                except Exception as exc:  # noqa: BLE001 - keep data, retry later
                    self._mark_failure(exc)
                    if self._stop.wait(backoff):
                        return
                    backoff = min(backoff * 2, 60.0)
                    continue
                if not sent:
                    break
                self._mark_success()
                backoff = 1.0

    def _send_live(self) -> int:
        with self._lock:
            n = min(len(self._mem), self.max_batch_points)
            batch = [self._mem.popleft() for _ in range(n)]
            self._inflight = batch
        if not batch:
            return 0
        try:
            self._timed(self.sink.write, batch)
        except Rejected as exc:
            with self._lock:
                self._inflight = []
            self._set_aside(batch, exc)
            return 0
        except Exception:
            with self._lock:
                self._mem.extendleft(reversed(batch))
                self._inflight = []
            raise
        delay_ms = (time.time_ns() - int(batch[0]["time_ns"])) / 1e6
        with self._lock:
            self._inflight = []
            self.written += len(batch)
            if delay_ms > self._max_delay_ms:
                self._max_delay_ms = delay_ms
        return len(batch)

    def _set_aside(self, batch: list[Point], exc: Exception) -> None:
        """Data the sink refuses would be refused forever: keep it on disk for inspection."""
        if self.spool_dir:
            path = self.spool_dir / f"rejected-{int(batch[0]['time_ns']):020d}.lp"
            try:
                path.write_text("\n".join(to_line(p) for p in batch) + "\n", encoding="utf-8")
                log.error("%s rejected %d points (%s); saved to %s.", self.sink.name, len(batch), exc, path.name)
                return
            except OSError:
                pass
        log.error("%s rejected %d points (%s); they were not stored.", self.sink.name, len(batch), exc)

    # ---------------------------------------------------------------- spool (disk)
    def _write_block(self, block: list[Point]) -> None:
        self._seq += 1
        final = self.spool_dir / f"block-{int(block[0]['time_ns']):020d}-{self._seq:06d}.lp"
        tmp = final.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(to_line(p) for p in block) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
        size = final.stat().st_size
        with self._lock:
            self._blocks.append((final, size))
            self._spool_size += size
        self.spooled += len(block)

    @staticmethod
    def _read_block(path: Path) -> tuple[str, int]:
        """Returns the block as line protocol and its point count (``.jsonl`` = SecondX 2.1.0 format)."""
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            text = "\n".join(to_line(json.loads(line)) for line in text.splitlines() if line.strip()) + "\n"
        count = sum(1 for line in text.splitlines() if line.strip())
        return text, count

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

    # ---------------------------------------------------------------- backlog upload (parallel)
    def _backlog_loop(self) -> None:
        backoff, next_probe, last_start = 1.0, 0.0, 0.0
        while not self._stop.wait(0.25):
            self._workers = [w for w in self._workers if w.is_alive()]
            with self._lock:
                waiting = len(self._blocks)
                if not waiting and not self._workers:
                    self._session = None
            if not waiting:
                continue
            now = time.monotonic()
            if self.failing_since is not None:           # sink down: one probing worker, with back-off
                if not self._workers and now >= next_probe:
                    self._start_worker()
                    next_probe, backoff = now + backoff, min(backoff * 2, 60.0)
                continue
            backoff = 1.0
            # Scale out while uploads mostly wait on the server (GIL released): more workers = more speed
            # at the same CPU budget. CPU-bound uploads would only compete with sampling, so stay at one.
            if not self._workers or (len(self._workers) < self.backlog_max_workers and waiting > len(self._workers)
                                     and self._wait_share >= 0.5 and now - last_start >= self.SCALE_EVERY_SECONDS):
                self._start_worker()
                last_start = now

    def _start_worker(self) -> None:
        self._worker_seq += 1
        self._workers.append(self._thread(self._upload_worker, f"secondx-upload-{self._worker_seq}"))

    def _upload_worker(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                if not self._blocks:
                    return
                path, size = self._blocks.popleft()
                if self._session is None:
                    self._session = [0.0, time.monotonic()]
            cpu0, wall0 = time.thread_time(), time.monotonic()
            try:
                text, count = self._read_block(path)
            except FileNotFoundError:
                log.warning("Spool block %s disappeared; skipped.", path.name)
                self._forget(size)
                continue
            except (OSError, ValueError) as exc:          # corrupt / undecodable block
                self._set_aside_block(path, size, ".bad", exc)
                continue
            try:
                self._timed(self.sink.write_lines, text)
            except Rejected as exc:
                self._set_aside_block(path, size, ".rejected", exc)
                continue
            except Exception as exc:  # noqa: BLE001 - block stays queued; the coordinator retries
                with self._lock:
                    self._blocks.appendleft((path, size))
                self._mark_failure(exc)
                return
            try:
                path.unlink()
            except OSError:
                pass
            cpu, wall = time.thread_time() - cpu0, time.monotonic() - wall0
            with self._lock:
                self._spool_size -= size
                self.written += count
                self._wait_share = 0.7 * self._wait_share + 0.3 * (max(0.0, 1 - cpu / wall) if wall > 0 else 0.0)
                self._session[0] += cpu
                used, start = self._session
            self._mark_success()
            # Shared budget: all workers together use at most backlog_cpu_share of one core on average.
            pause = max(self.backlog_min_pause, used / self.backlog_cpu_share - (time.monotonic() - start))
            if pause and self._stop.wait(pause):
                return

    def _forget(self, size: int) -> None:
        with self._lock:
            self._spool_size -= size

    def _set_aside_block(self, path: Path, size: int, suffix: str, exc: Exception) -> None:
        """A block that can never be uploaded must not block the queue: keep it aside, continue."""
        target = path.with_suffix(suffix)
        try:
            os.replace(path, target)
            log.error("Spool block %s cannot be uploaded (%s); moved to %s, continuing.", path.name, exc, target.name)
        except OSError:
            log.error("Spool block %s cannot be uploaded (%s) and was skipped.", path.name, exc)
        self._forget(size)

    # ---------------------------------------------------------------- shutdown
    def close(self, timeout: float = 5.0) -> None:
        """Flushes the RAM buffer within ``timeout`` if the sink is healthy; whatever
        is left goes to the disk spool (uploaded on next start). Blocks being uploaded
        are only deleted after success, so an interrupted upload is simply repeated."""
        end = time.monotonic() + timeout
        self._wake.set()
        while self.pending and self.failing_since is None and time.monotonic() < end and self._live.is_alive():
            time.sleep(0.05)
        self._stop.set()
        self._wake.set()
        self._live.join(timeout=max(0.2, end - time.monotonic()))
        for thread in [self._spooler, self._backlog, *self._workers]:
            if thread is not None:
                thread.join(timeout=max(0.2, min(2.0, end - time.monotonic())))
        with self._lock:
            # A write still hanging here may or may not reach the sink: keep its batch as well
            # (re-sending a point is idempotent in InfluxDB, losing it is not acceptable).
            rest = (list(self._inflight) if self._live.is_alive() else []) + list(self._mem)
            self._inflight = []
            self._mem.clear()
        if rest and self.spool_dir:
            try:
                for i in range(0, len(rest), self.spool_block_points):
                    self._write_block(rest[i:i + self.spool_block_points])
                log.info("%d unsent points saved to the spool; they will be uploaded on next start.", len(rest))
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
