"""
SecondX - High-Precision Infrastructure Telemetry Agent
Author: İlhan Koçaslan (Proaiml)

Samples the host every ``interval_seconds`` (default 1 s), ranks the top
resource-consuming processes for CPU, RAM, disk read and disk write, and
exports everything to InfluxDB v2 (or to local JSON-lines files).

Design principle: on a critical server the monitoring agent must never become
the problem. It either runs flawlessly at second-level precision, or it stops
immediately and says why (strict mode, on by default).

Usage:
    python SecondX.py                  run the agent
    python SecondX.py --supervise      run under the built-in restart policy (services)
    SecondX.exe --service install ...  Windows: register / remove the service (used by the installer)
    python SecondX.py --once           print one sample as a table and exit
    python SecondX.py --check          validate config and InfluxDB connection
    python SecondX.py --dry-run        sample continuously, export nothing
    python SecondX.py --config PATH    use another config file

Exit codes:
    0 stopped on request            3 own CPU/RAM limit exceeded   (never restarted)
    1 unexpected error (restartable) 4 export safety limit reached (never restarted)
    2 configuration error           5 second-level precision lost (restartable)
"""
from __future__ import annotations

import argparse
import collections
import copy
import json
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import psutil

import influx_exporter as ife
import lissozis as ls
from collector import Collector, Sample, hostname

__version__ = "2.3.1"
FROZEN = bool(getattr(sys, "frozen", False))          # SecondX.exe (PyInstaller), no Python needed
BASE_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
log = logging.getLogger("secondx")

EXIT_OK, EXIT_ERROR, EXIT_CONFIG, EXIT_RESOURCE, EXIT_EXPORT, EXIT_PRECISION = 0, 1, 2, 3, 4, 5
NO_RESTART_CODES = {EXIT_OK, EXIT_CONFIG, EXIT_RESOURCE, EXIT_EXPORT}
EXIT_MEANING = {
    EXIT_OK: "stopped on request", EXIT_ERROR: "unexpected error", EXIT_CONFIG: "configuration error",
    EXIT_RESOURCE: "own CPU/RAM limit exceeded", EXIT_EXPORT: "export safety limit reached",
    EXIT_PRECISION: "second-level precision lost",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "interval_seconds": 1.0,
    "top_n": 6,
    "exclude_processes": ["System Idle Process", "Idle", "MemCompression", "Memory Compression",
                          "Secure System", "Registry", "System"],
    "host": "",
    "log_dir": "logs",
    "log_retention_days": 14,
    "log_level": "INFO",
    "strict_mode": True,
    "limits": {
        "max_cpu_percent": 25.0,
        "max_memory_mb": 200,
        "cpu_window_seconds": 3,
    },
    "precision": {
        "max_missed_slots": 3,
        "slot_tolerance_ms": 500,
    },
    "restart": {
        "max_attempts": 5,
        "delay_seconds": 300,
        "reset_after_seconds": 3600,
    },
    "influx": {
        "enabled": True,
        "url": "http://localhost:8086",
        "token": "",
        "token_file": "",
        "org": "secondx",
        "bucket": "secondx",
        "timeout_ms": 5000,
        "max_memory_points": 30000,
        "spool_dir": "spool",
        "spool_block_points": 1500,
        "max_outage_hours": 6,
        "max_spool_mb": 1024,
        "min_free_disk_mb": 1024,
        "backlog_cpu_percent": 10,
        "backlog_max_workers": 4,
    },
    "local_output": {
        "enabled": "auto",
        "dir": "data",
        "retention_days": 14,
    },
}
PLACEHOLDER_TOKENS = {"", "YOUR_INFLUXDB_TOKEN", "YOUR_INFLUXDB_API_TOKEN", "CHANGE_ME"}
ENV_OVERRIDES = {
    "SECONDX_INFLUX_URL": ("influx", "url"),
    "SECONDX_INFLUX_TOKEN": ("influx", "token"),
    "SECONDX_INFLUX_TOKEN_FILE": ("influx", "token_file"),
    "SECONDX_INFLUX_ORG": ("influx", "org"),
    "SECONDX_INFLUX_BUCKET": ("influx", "bucket"),
    "SECONDX_HOST": ("host",),
    "SECONDX_INTERVAL": ("interval_seconds",),
}
NUMERIC = {
    ("interval_seconds",): float, ("top_n",): int, ("log_retention_days",): int,
    ("limits", "max_cpu_percent"): float, ("limits", "max_memory_mb"): float,
    ("limits", "cpu_window_seconds"): float,
    ("precision", "max_missed_slots"): int, ("precision", "slot_tolerance_ms"): float,
    ("restart", "max_attempts"): int, ("restart", "delay_seconds"): float,
    ("restart", "reset_after_seconds"): float,
    ("influx", "timeout_ms"): int, ("influx", "max_memory_points"): int,
    ("influx", "spool_block_points"): int, ("influx", "max_outage_hours"): float,
    ("influx", "max_spool_mb"): float, ("influx", "min_free_disk_mb"): float,
    ("influx", "backlog_cpu_percent"): float,
    ("influx", "backlog_max_workers"): int,
    ("local_output", "retention_days"): int,
}


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------- configuration
def _merge(base: dict, extra: dict) -> dict:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Returns (config, notes). Supports SecondX 1.x files (wait_time, token.json)."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    notes: list[str] = []
    path = Path(path)
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            raise ConfigError(f"{path.name}: invalid JSON at line {getattr(exc, 'lineno', '?')}: {exc}") from exc
        if not isinstance(user, dict):
            raise ConfigError(f"{path.name}: top level must be a JSON object")
        if "wait_time" in user and "interval_seconds" not in user:
            user["interval_seconds"] = user.pop("wait_time")
            notes.append("config: 'wait_time' is deprecated, use 'interval_seconds'")
        user.pop("wait_time", None)
        user.pop("description", None)
        influx_user = user.get("influx")
        if isinstance(influx_user, dict) and "max_buffer_points" in influx_user:
            influx_user.setdefault("max_memory_points", influx_user.pop("max_buffer_points"))
            notes.append("config: influx.max_buffer_points is now influx.max_memory_points")
        unknown = sorted(set(user) - set(DEFAULT_CONFIG))
        if unknown:
            notes.append(f"config: unknown keys ignored: {', '.join(unknown)}")
        _merge(cfg, {k: v for k, v in user.items() if k in DEFAULT_CONFIG})
    else:
        notes.append(f"config: {path.name} not found, using defaults")

    legacy = path.parent / "token.json"
    if legacy.exists():
        try:
            tok = json.loads(legacy.read_text(encoding="utf-8-sig"))
            # token.json only fills values that config.json left at their defaults.
            for key in ("url", "token", "org", "bucket"):
                value = tok.get(key)
                if not value or (key == "token" and value in PLACEHOLDER_TOKENS):
                    continue
                if cfg["influx"].get(key) in ("", DEFAULT_CONFIG["influx"][key]):
                    cfg["influx"][key] = value
            notes.append("config: token.json is deprecated, move its values into config.json 'influx' or env vars")
        except (OSError, ValueError) as exc:
            notes.append(f"config: token.json ignored ({exc})")

    for env, keys in ENV_OVERRIDES.items():
        if os.environ.get(env):
            target = cfg
            for k in keys[:-1]:
                target = target[k]
            target[keys[-1]] = os.environ[env]

    for keys, kind in NUMERIC.items():
        target = cfg
        for k in keys[:-1]:
            target = target[k]
        try:
            target[keys[-1]] = kind(target[keys[-1]])
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"config: {'.'.join(keys)} must be a number ({exc})") from exc
    checks = [
        (0.2 <= cfg["interval_seconds"] <= 3600, "interval_seconds must be between 0.2 and 3600"),
        (1 <= cfg["top_n"] <= 100, "top_n must be between 1 and 100"),
        (cfg["limits"]["max_cpu_percent"] > 0, "limits.max_cpu_percent must be > 0"),
        (cfg["limits"]["max_memory_mb"] >= 50, "limits.max_memory_mb must be >= 50"),
        (cfg["precision"]["max_missed_slots"] >= 1, "precision.max_missed_slots must be >= 1"),
        (cfg["restart"]["max_attempts"] >= 0, "restart.max_attempts must be >= 0"),
        (cfg["influx"]["max_outage_hours"] > 0, "influx.max_outage_hours must be > 0"),
        (0 < cfg["influx"]["backlog_cpu_percent"] <= cfg["limits"]["max_cpu_percent"] / 2,
         "influx.backlog_cpu_percent must be > 0 and at most half of limits.max_cpu_percent"),
        (1 <= cfg["influx"]["backlog_max_workers"] <= 16, "influx.backlog_max_workers must be 1-16"),
    ]
    for ok, message in checks:
        if not ok:
            raise ConfigError(f"config: {message}")
    token_file = str(cfg["influx"].get("token_file") or "")
    if token_file and str(cfg["influx"].get("token", "")) in PLACEHOLDER_TOKENS:
        # A file only SYSTEM/Administrators (or root) can read keeps the secret out of the
        # config and out of machine-wide environment variables that every user can read.
        try:
            cfg["influx"]["token"] = _resolve(path.parent, token_file).read_text(encoding="utf-8-sig").strip()
        except OSError as exc:
            raise ConfigError(f"config: influx.token_file cannot be read ({exc})") from exc
    cfg["strict_mode"] = bool(cfg.get("strict_mode", True))
    cfg["host"] = str(cfg.get("host") or hostname())
    return cfg, notes


def influx_configured(cfg: dict[str, Any]) -> bool:
    inf = cfg["influx"]
    return bool(inf.get("enabled")) and str(inf.get("token", "")) not in PLACEHOLDER_TOKENS


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


class InstanceLock:
    """Only one agent per config: a second copy (e.g. started by hand next to the
    service) would double every point and share the spool. The OS releases the
    lock automatically when the process ends, even after a crash."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._file = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
        self._file = f
        return True

    def release(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


# ---------------------------------------------------------------- logging
def setup_logging(cfg: dict[str, Any], base: Path, verbose: bool = False, filename: str = "secondx.log") -> None:
    level = logging.DEBUG if verbose else getattr(logging, str(cfg.get("log_level", "INFO")).upper(), logging.INFO)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    if sys.stdout is not None:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        root.addHandler(console)
    try:
        log_dir = _resolve(base, cfg["log_dir"])
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.TimedRotatingFileHandler(
            log_dir / filename, when="midnight", backupCount=max(1, cfg["log_retention_days"]), encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as exc:
        log.warning("File logging disabled (%s); console logging only.", exc)
    for noisy in ("urllib3", "influxdb_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------- guards
class StopAgent(Exception):
    """Raised by a guard: the agent must stop with ``code``."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


class ResourceGuard:
    """Stops the agent at once if its OWN process exceeds its CPU or RAM budget.

    * RAM (resident set size) is checked on every sample from the very first
      one - a single breach stops the agent.
    * CPU is the agent's own CPU time over the last ``cpu_window_seconds``
      (default 3 s) in % of ONE core. The first ``warmup`` seconds (module
      imports, first full process scan) are one-off start-up work and are not
      counted against the steady-state budget.
    """

    def __init__(self, limits: dict[str, Any], interval: float, process: Any | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max_cpu = float(limits["max_cpu_percent"])
        self.max_rss = float(limits["max_memory_mb"]) * 1024 * 1024
        self.window = max(float(limits["cpu_window_seconds"]), interval)
        self.warmup = max(5.0, self.window)
        self.proc = process or psutil.Process()
        self.clock = clock
        self.started = clock()
        self.samples: collections.deque[tuple[float, float]] = collections.deque()

    def _cpu_seconds(self) -> float:
        t = self.proc.cpu_times()
        return float(t.user) + float(t.system)

    def check(self) -> None:
        rss = float(self.proc.memory_info().rss)
        if rss > self.max_rss:
            raise StopAgent(EXIT_RESOURCE, f"own memory {rss / 2**20:.0f} MB exceeds limit "
                                           f"{self.max_rss / 2**20:.0f} MB")
        now, cpu = self.clock(), self._cpu_seconds()
        self.samples.append((now, cpu))
        while len(self.samples) > 2 and now - self.samples[1][0] >= self.window:
            self.samples.popleft()
        if now - self.started < self.warmup:
            # Start-up work must not leak into the first steady-state window.
            self.samples = collections.deque([(now, cpu)])
            return
        t0, c0 = self.samples[0]
        elapsed = now - t0
        if elapsed >= self.window * 0.9:
            pct = max(0.0, cpu - c0) * 100.0 / elapsed
            if pct > self.max_cpu:
                raise StopAgent(EXIT_RESOURCE, f"own CPU {pct:.1f}% of one core over the last {elapsed:.1f} s "
                                               f"exceeds limit {self.max_cpu:.1f}%")


class PrecisionGuard:
    """Stops the agent when it can no longer sample on its second-level schedule.

    A slot is missed when a sample starts later than ``slot_tolerance_ms`` after
    its scheduled time or takes longer than the interval. ``max_missed_slots``
    consecutive misses stop the agent, so coarse data is never labelled as
    second-level data. A single jump of more than 30 s (VM pause, host
    suspend, clock step) is logged and the schedule is re-synchronised instead.
    """

    JUMP_SECONDS = 30.0

    def __init__(self, precision: dict[str, Any], interval: float) -> None:
        self.interval = interval
        self.tolerance = float(precision["slot_tolerance_ms"]) / 1000.0
        self.max_missed = int(precision["max_missed_slots"])
        self.missed = 0
        self.total_missed = 0

    def check(self, lateness: float, duration: float) -> bool:
        """Returns True if the schedule should be re-synchronised."""
        if lateness > self.JUMP_SECONDS:
            log.warning("Clock jump / pause of %.1f s detected; schedule re-synchronised.", lateness)
            self.missed = 0
            return True
        if lateness > self.tolerance or duration > self.interval:
            self.missed += 1
            self.total_missed += 1
            if self.missed >= self.max_missed:
                raise StopAgent(EXIT_PRECISION, f"{self.missed} consecutive samples missed their "
                                                f"{self.interval:.2f} s slot (last: {lateness * 1000:.0f} ms late, "
                                                f"took {duration * 1000:.0f} ms)")
        else:
            self.missed = 0
        return False


# ---------------------------------------------------------------- points & output
def build_points(sample: Sample, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """System point + one point per (metric, top process)."""
    host = cfg["host"]
    points = [{"measurement": "secondx_system", "tags": {"host": host},
               "fields": dict(sample.system), "time_ns": sample.timestamp_ns}]
    for metric, values in sample.processes.items():
        for rank, name, value in ls.ranked(values, cfg["top_n"], cfg["exclude_processes"]):
            points.append({"measurement": "secondx_process",
                           "tags": {"host": host, "metric": metric, "process": name, "rank": str(rank)},
                           "fields": {"value": value}, "time_ns": sample.timestamp_ns})
    return points


def make_exporter(cfg: dict[str, Any], base: Path) -> ife.Exporter | None:
    inf = cfg["influx"]
    if influx_configured(cfg):
        try:
            sink = ife.InfluxSink(inf["url"], inf["token"], inf["org"], inf["bucket"], inf["timeout_ms"],
                                  connections=inf["backlog_max_workers"] + 2)
            state = "reachable" if sink.ping() else "NOT reachable yet (data is kept and retried)"
            log.info("Exporting to InfluxDB %s (org=%s, bucket=%s) - %s", sink.url, sink.org, sink.bucket, state)
            return ife.Exporter(
                sink, max_memory_points=inf["max_memory_points"], spool_dir=_resolve(base, inf["spool_dir"]),
                spool_block_points=inf["spool_block_points"], max_outage_seconds=inf["max_outage_hours"] * 3600,
                max_spool_bytes=int(inf["max_spool_mb"] * 2**20), min_free_disk_bytes=int(inf["min_free_disk_mb"] * 2**20),
                backlog_cpu_percent=inf["backlog_cpu_percent"], backlog_max_workers=inf["backlog_max_workers"],
                stall_seconds=max(30.0, 3 * inf["timeout_ms"] / 1000))   # a write is bounded by timeout_ms
        except ImportError:
            log.error("InfluxDB is configured but the 'influxdb-client' package is not installed for %s. "
                      "Run: \"%s\" -m pip install -r requirements.txt  - writing to local files meanwhile.",
                      sys.executable, sys.executable)
    local = cfg["local_output"]
    if local.get("enabled") in (True, "auto", "true", "yes", 1):
        sink = ife.LocalSink(_resolve(base, local["dir"]), local["retention_days"])
        log.info("Writing metrics to %s (InfluxDB %s).", sink.directory,
                 "unavailable, see error above" if influx_configured(cfg) else "token not set")
        return ife.Exporter(sink, max_memory_points=inf["max_memory_points"])
    log.warning("No output configured (influx disabled and local_output off); running in dry mode.")
    return None


# ---------------------------------------------------------------- commands
UNITS = {"cpu": "%", "ram": "%", "disk_read": "KB/s", "disk_write": "KB/s"}


def print_table(sample: Sample, cfg: dict[str, Any]) -> None:
    s = sample.system
    print(f"\nSecondX {__version__} | host {cfg['host']} | interval {sample.interval_s or 0:.2f}s")
    print(f"CPU {s.get('cpu_percent', 0):5.1f}%   RAM {s.get('ram_percent', 0):5.1f}% "
          f"({s.get('ram_used_gb', 0):.1f} GB)   Disk R/W {s.get('disk_read_kbps', 0):8.1f} / "
          f"{s.get('disk_write_kbps', 0):8.1f} KB/s   Net out/in {s.get('net_sent_kbps', 0):8.1f} / "
          f"{s.get('net_recv_kbps', 0):8.1f} KB/s")
    for metric, values in sample.processes.items():
        ranked = ls.ranked(values, cfg["top_n"], cfg["exclude_processes"])
        print(f"\nTop {metric} ({UNITS[metric]}):")
        if not ranked:
            print("  (no activity)")
        for rank, name, value in ranked:
            print(f"  {rank}. {name:<40} {value:10.2f}")
    print()


def run_once(cfg: dict[str, Any]) -> int:
    collector = Collector()
    collector.sample()
    time.sleep(cfg["interval_seconds"])
    print_table(collector.sample(), cfg)
    return EXIT_OK


def run_check(cfg: dict[str, Any], notes: list[str]) -> int:
    ok = True
    print(f"SecondX {__version__} configuration check")
    print(f"  host tag          : {cfg['host']}")
    print(f"  interval          : {cfg['interval_seconds']} s, top {cfg['top_n']} processes")
    print(f"  strict mode       : {'ON' if cfg['strict_mode'] else 'off'} | own limits: "
          f"{cfg['limits']['max_cpu_percent']}% of one core, {cfg['limits']['max_memory_mb']:.0f} MB RAM")
    for note in notes:
        print(f"  note              : {note}")
    if influx_configured(cfg):
        inf = cfg["influx"]
        print(f"  influxdb          : {inf['url']} org={inf['org']} bucket={inf['bucket']}")
        try:
            sink = ife.InfluxSink(inf["url"], inf["token"], inf["org"], inf["bucket"], inf["timeout_ms"])
        except ImportError:
            print("  -> influxdb-client package missing: pip install -r requirements.txt")
            return EXIT_CONFIG
        if not sink.ping():
            print("  -> NOT reachable (check url / firewall / container)")
            ok = False
        else:
            try:
                sink.write([{"measurement": "secondx_check", "tags": {"host": cfg["host"]},
                             "fields": {"ok": 1.0}, "time_ns": time.time_ns()}])
                print("  -> reachable, test write OK")
            except Exception as exc:  # noqa: BLE001
                print(f"  -> reachable but write failed: {exc}")
                print("     check token permissions, org and bucket names")
                ok = False
        sink.close()
    else:
        print("  influxdb          : not configured -> local JSON-lines output in "
              f"'{cfg['local_output']['dir']}/'")
    return EXIT_OK if ok else EXIT_ERROR


def _install_signal_handlers(stop: threading.Event) -> None:
    def _stop(signum, _frame):
        log.info("Signal %s received, shutting down...", signum)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):
                pass


def run_agent(cfg: dict[str, Any], notes: list[str], dry_run: bool, base: Path, *,
              collector: Collector | None = None, stop: threading.Event | None = None,
              resource_guard: ResourceGuard | None = None, max_cycles: int | None = None) -> int:
    for note in notes:
        log.warning(note)
    stop = stop or threading.Event()
    _install_signal_handlers(stop)
    strict = cfg["strict_mode"]
    interval = cfg["interval_seconds"]
    exporter = None if dry_run else make_exporter(cfg, base)
    if dry_run:
        log.info("Dry run: nothing is exported.")
    ife.set_default_exporter(exporter)
    parent_pid = int(os.environ.get("SECONDX_PARENT_PID", "0") or 0)

    collector = collector or Collector()
    guard = resource_guard or ResourceGuard(cfg["limits"], interval)
    precision = PrecisionGuard(cfg["precision"], interval)
    log.info("SecondX %s started on host '%s' (interval %.2fs, top %d, strict mode %s, own limits %.0f%% CPU "
             "of one core / %.0f MB RAM).", __version__, cfg["host"], interval, cfg["top_n"],
             "ON" if strict else "off", cfg["limits"]["max_cpu_percent"], cfg["limits"]["max_memory_mb"])
    exit_code = EXIT_OK
    stats = {"samples": 0, "errors": 0}
    last_report = time.monotonic()
    last_error_log = 0.0
    next_tick = time.monotonic()
    cycles = 0
    try:
        while not stop.is_set():
            started = time.monotonic()
            lateness = started - next_tick
            try:
                sample = collector.sample()
                if sample.interval_s is not None:          # first sample only primes the counters
                    points = build_points(sample, cfg)
                    if exporter:
                        exporter.submit(points)
                    stats["samples"] += 1
                    log.debug("sample: %d points, cpu %.1f%%, ram %.1f%%, late %.0f ms", len(points),
                              sample.system.get("cpu_percent", 0), sample.system.get("ram_percent", 0),
                              lateness * 1000)
                if exporter:                               # data-safety checks apply in every mode
                    if exporter.fatal_reason:
                        raise StopAgent(EXIT_EXPORT, exporter.fatal_reason)
                    problem = exporter.health()            # sender alive and not hanging?
                    if problem:
                        raise StopAgent(EXIT_ERROR, problem)
                if strict:
                    guard.check()
                    if cycles > 1 and precision.check(lateness, time.monotonic() - started):
                        next_tick = time.monotonic()
                if parent_pid and not psutil.pid_exists(parent_pid):
                    log.warning("Supervisor process %d is gone; stopping.", parent_pid)
                    break
            except StopAgent as exc:
                log.critical("STOPPING: %s (exit code %d = %s).", exc.reason, exc.code, EXIT_MEANING[exc.code])
                exit_code = exc.code
                break
            except Exception:  # noqa: BLE001
                stats["errors"] += 1
                if strict:
                    log.critical("STOPPING: sampling cycle failed (strict mode).", exc_info=True)
                    exit_code = EXIT_ERROR
                    break
                if time.monotonic() - last_error_log > 60:
                    log.exception("Sampling cycle failed; continuing (strict mode off).")
                    last_error_log = time.monotonic()
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            if time.monotonic() - last_report >= 300:
                extra = (f", exported {exporter.written} (max delay {exporter.take_max_delay_ms():.0f} ms), "
                         f"in RAM {exporter.pending}, spooled {exporter.spooled}" if exporter else "")
                log.info("Status: %d samples, %d missed slots, %d errors in last 5 min%s.",
                         stats["samples"], precision.total_missed, stats["errors"], extra)
                stats = {"samples": 0, "errors": 0}
                precision.total_missed = 0
                last_report = time.monotonic()
            next_tick += interval
            delay = next_tick - time.monotonic()
            if delay < -interval and not strict:    # non-strict: silently resync after falling behind
                next_tick = time.monotonic()
                delay = 0.0
            stop.wait(max(0.0, delay))
    finally:
        if exporter:
            exporter.close(timeout=5.0)
        ife.set_default_exporter(None)
        log.info("SecondX stopped (exit code %d = %s).", exit_code, EXIT_MEANING.get(exit_code, "?"))
    return exit_code


# ---------------------------------------------------------------- supervisor
def supervise(command: list[str], policy: dict[str, Any], stop: threading.Event | None = None,
              sleep: Callable[[float], bool] | None = None) -> int:
    """Runs ``command`` and applies the restart policy.

    * exit codes in NO_RESTART_CODES (0, config error, own CPU/RAM limit,
      export safety limit) end supervision immediately;
    * other failures are restarted after ``delay_seconds``, at most
      ``max_attempts`` times per incident; a run longer than
      ``reset_after_seconds`` starts a new incident.
    """
    stop = stop or threading.Event()
    sleep = sleep or (lambda s: stop.wait(s))
    attempts = 0
    env = dict(os.environ, SECONDX_PARENT_PID=str(os.getpid()))
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    while True:
        started = time.monotonic()
        log.info("Supervisor: starting agent (%s).", "first run" if attempts == 0 else f"restart {attempts}")
        child = subprocess.Popen(command, env=env, creationflags=flags)
        while child.poll() is None:
            if stop.wait(0.5):
                log.info("Supervisor: stop requested, stopping agent...")
                try:
                    if os.name == "nt":
                        child.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        child.terminate()
                    child.wait(timeout=15)
                except (subprocess.TimeoutExpired, OSError):
                    child.kill()
                return EXIT_OK
        code = int(child.returncode)
        meaning = EXIT_MEANING.get(code, "crash")
        if code in NO_RESTART_CODES:
            log.log(logging.INFO if code == EXIT_OK else logging.CRITICAL,
                    "Supervisor: agent exited with code %d (%s); not restarting.", code, meaning)
            return code
        if time.monotonic() - started >= float(policy["reset_after_seconds"]):
            attempts = 0
        if attempts >= int(policy["max_attempts"]):
            log.critical("Supervisor: agent failed %d times in a row (last: code %d, %s); giving up. "
                         "Check the log, fix the cause and start the service again.",
                         attempts + 1, code, meaning)
            return code
        attempts += 1
        delay = float(policy["delay_seconds"])
        log.error("Supervisor: agent exited with code %d (%s); restart %d/%d in %.0f s.",
                  code, meaning, attempts, int(policy["max_attempts"]), delay)
        if sleep(delay):
            return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["--service"]:                         # Windows service install / uninstall
        import winservice
        return winservice.main(argv[1:], BASE_DIR, DEFAULT_CONFIG)
    parser = argparse.ArgumentParser(description="SecondX high-precision infrastructure telemetry agent")
    parser.add_argument("--config", default=os.environ.get("SECONDX_CONFIG", str(BASE_DIR / "config.json")))
    parser.add_argument("--once", action="store_true", help="print one sample as a table and exit")
    parser.add_argument("--check", action="store_true", help="validate config and InfluxDB connectivity")
    parser.add_argument("--dry-run", action="store_true", help="sample but export nothing")
    parser.add_argument("--supervise", action="store_true", help="run the agent under the restart policy")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    parser.add_argument("--version", action="version", version=f"SecondX {__version__}")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    try:
        cfg, notes = load_config(config_path)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    if args.once:
        return run_once(cfg)
    if args.check:
        return run_check(cfg, notes)
    if args.supervise:
        setup_logging(cfg, config_path.parent, args.verbose, filename="secondx-supervisor.log")
        stop = threading.Event()
        _install_signal_handlers(stop)
        child = ([sys.executable] if FROZEN else [sys.executable, str(Path(__file__).resolve())]) + \
            ["--config", str(config_path)]
        if args.dry_run:
            child.append("--dry-run")
        if args.verbose:
            child.append("--verbose")
        return supervise(child, cfg["restart"], stop)
    setup_logging(cfg, config_path.parent, args.verbose)
    lock = InstanceLock(config_path.parent / "secondx.lock")
    if not lock.acquire():
        log.critical("STOPPING: another SecondX agent is already running with %s (exit code %d = %s).",
                     config_path, EXIT_CONFIG, EXIT_MEANING[EXIT_CONFIG])
        return EXIT_CONFIG
    try:
        return run_agent(cfg, notes, args.dry_run, config_path.parent)
    except Exception:  # noqa: BLE001 - services have no console: make the reason visible in the log
        log.exception("SecondX stopped because of an unexpected error.")
        return EXIT_ERROR
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
