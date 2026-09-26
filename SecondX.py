"""
SecondX - High-Precision Infrastructure Telemetry Agent
Author: İlhan Koçaslan (Proaiml)

Samples the host every ``interval_seconds`` (default 1 s), ranks the top
resource-consuming processes for CPU, RAM, disk read and disk write, and
exports everything to InfluxDB v2 (or to local JSON-lines files).

Usage:
    python SecondX.py                  run the agent
    python SecondX.py --once           print one sample as a table and exit
    python SecondX.py --check          validate config and InfluxDB connection
    python SecondX.py --dry-run        sample continuously, export nothing
    python SecondX.py --config PATH    use another config file
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import logging.handlers
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

import influx_exporter as ife
import lissozis as ls
from collector import Collector, Sample, hostname

__version__ = "2.0.0"
BASE_DIR = Path(__file__).resolve().parent
log = logging.getLogger("secondx")

DEFAULT_CONFIG: dict[str, Any] = {
    "interval_seconds": 1.0,
    "top_n": 6,
    "exclude_processes": ["System Idle Process", "Idle", "MemCompression", "Memory Compression",
                          "Secure System", "Registry", "System"],
    "host": "",
    "log_dir": "logs",
    "log_retention_days": 14,
    "log_level": "INFO",
    "influx": {
        "enabled": True,
        "url": "http://localhost:8086",
        "token": "",
        "org": "secondx",
        "bucket": "secondx",
        "timeout_ms": 5000,
        "max_buffer_points": 200000,
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
    "SECONDX_INFLUX_ORG": ("influx", "org"),
    "SECONDX_INFLUX_BUCKET": ("influx", "bucket"),
    "SECONDX_HOST": ("host",),
    "SECONDX_INTERVAL": ("interval_seconds",),
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

    try:
        cfg["interval_seconds"] = float(cfg["interval_seconds"])
        cfg["top_n"] = int(cfg["top_n"])
        cfg["log_retention_days"] = int(cfg["log_retention_days"])
        cfg["local_output"]["retention_days"] = int(cfg["local_output"]["retention_days"])
        cfg["influx"]["timeout_ms"] = int(cfg["influx"]["timeout_ms"])
        cfg["influx"]["max_buffer_points"] = int(cfg["influx"]["max_buffer_points"])
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"config: wrong value type ({exc})") from exc
    if not 0.2 <= cfg["interval_seconds"] <= 3600:
        raise ConfigError("config: interval_seconds must be between 0.2 and 3600")
    if not 1 <= cfg["top_n"] <= 100:
        raise ConfigError("config: top_n must be between 1 and 100")
    cfg["host"] = str(cfg.get("host") or hostname())
    return cfg, notes


def influx_configured(cfg: dict[str, Any]) -> bool:
    inf = cfg["influx"]
    return bool(inf.get("enabled")) and str(inf.get("token", "")) not in PLACEHOLDER_TOKENS


# ---------------------------------------------------------------- logging
def setup_logging(cfg: dict[str, Any], base: Path, verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else getattr(logging, str(cfg.get("log_level", "INFO")).upper(), logging.INFO)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)
    log_dir = Path(cfg["log_dir"])
    log_dir = log_dir if log_dir.is_absolute() else base / log_dir
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.TimedRotatingFileHandler(
            log_dir / "secondx.log", when="midnight", backupCount=max(1, cfg["log_retention_days"]),
            encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as exc:
        log.warning("File logging disabled (%s); console logging only.", exc)
    for noisy in ("urllib3", "influxdb_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------- points
def build_points(sample: Sample, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """System point + one point per (metric, top process)."""
    host = cfg["host"]
    points = [{"measurement": "secondx_system", "tags": {"host": host},
               "fields": dict(sample.system), "time_ns": sample.timestamp_ns}]
    for metric, values in sample.processes.items():
        for rank, (name, value) in enumerate(ls.top(values, cfg["top_n"], cfg["exclude_processes"]), 1):
            points.append({"measurement": "secondx_process",
                           "tags": {"host": host, "metric": metric, "process": name, "rank": str(rank)},
                           "fields": {"value": value}, "time_ns": sample.timestamp_ns})
    return points


def make_sink(cfg: dict[str, Any], base: Path):
    if influx_configured(cfg):
        inf = cfg["influx"]
        try:
            return ife.InfluxSink(inf["url"], inf["token"], inf["org"], inf["bucket"], inf["timeout_ms"])
        except ImportError:
            # Never lose data because of a missing package: fall back to local files.
            log.error("InfluxDB is configured but the 'influxdb-client' package is not installed for %s. "
                      "Run: \"%s\" -m pip install -r requirements.txt  - writing to local files meanwhile.",
                      sys.executable, sys.executable)
    local = cfg["local_output"]
    if local.get("enabled") in (True, "auto", "true", "yes", 1):
        directory = Path(local["dir"])
        return ife.LocalSink(directory if directory.is_absolute() else base / directory, local["retention_days"])
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
        ranked = ls.top(values, cfg["top_n"], cfg["exclude_processes"])
        print(f"\nTop {metric} ({UNITS[metric]}):")
        if not ranked:
            print("  (no activity)")
        for rank, (name, value) in enumerate(ranked, 1):
            print(f"  {rank}. {name:<40} {value:10.2f}")
    print()


def run_once(cfg: dict[str, Any]) -> int:
    collector = Collector()
    collector.sample()
    time.sleep(cfg["interval_seconds"])
    print_table(collector.sample(), cfg)
    return 0


def run_check(cfg: dict[str, Any], notes: list[str]) -> int:
    ok = True
    print(f"SecondX {__version__} configuration check")
    print(f"  host tag          : {cfg['host']}")
    print(f"  interval          : {cfg['interval_seconds']} s, top {cfg['top_n']} processes")
    for note in notes:
        print(f"  note              : {note}")
    if influx_configured(cfg):
        inf = cfg["influx"]
        print(f"  influxdb          : {inf['url']} org={inf['org']} bucket={inf['bucket']}")
        try:
            sink = ife.InfluxSink(inf["url"], inf["token"], inf["org"], inf["bucket"], inf["timeout_ms"])
        except ImportError:
            print("  -> influxdb-client package missing: pip install -r requirements.txt")
            return 1
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
    return 0 if ok else 1


def run_agent(cfg: dict[str, Any], notes: list[str], dry_run: bool, base: Path) -> int:
    for note in notes:
        log.warning(note)
    sink = None if dry_run else make_sink(cfg, base)
    exporter = ife.Exporter(sink, max_buffer_points=cfg["influx"]["max_buffer_points"]) if sink else None
    ife.set_default_exporter(exporter)
    if dry_run:
        log.info("Dry run: nothing is exported.")
    elif sink is None:
        log.warning("No output configured (influx disabled and local_output off); running in dry mode.")
    elif isinstance(sink, ife.InfluxSink):
        state = "reachable" if sink.ping() else "NOT reachable yet (points are buffered and retried)"
        log.info("Exporting to InfluxDB %s (org=%s, bucket=%s) - %s", sink.url, sink.org, sink.bucket, state)
    elif influx_configured(cfg):
        log.warning("InfluxDB unavailable (see error above): writing metrics to %s", sink.directory)
    else:
        log.info("InfluxDB token not set: writing metrics to %s", sink.directory)

    stop = threading.Event()

    def _stop(signum, _frame):
        log.info("Signal %s received, shutting down...", signum)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):
                pass

    collector = Collector()
    interval = cfg["interval_seconds"]
    log.info("SecondX %s started on host '%s' (interval %.2fs, top %d).",
             __version__, cfg["host"], interval, cfg["top_n"])
    next_tick = time.monotonic()
    stats = {"samples": 0, "slow": 0, "errors": 0}
    last_report = time.monotonic()
    last_error_log = 0.0
    try:
        while not stop.is_set():
            started = time.monotonic()
            try:
                sample = collector.sample()
                if sample.interval_s is not None:        # first sample only primes counters
                    points = build_points(sample, cfg)
                    if exporter:
                        exporter.submit(points)
                    stats["samples"] += 1
                    log.debug("sample: %d points, cpu %.1f%%, ram %.1f%%", len(points),
                              sample.system.get("cpu_percent", 0), sample.system.get("ram_percent", 0))
            except Exception:  # noqa: BLE001 - one bad cycle must never stop the agent
                stats["errors"] += 1
                if time.monotonic() - last_error_log > 60:
                    log.exception("Sampling cycle failed; the agent keeps running.")
                    last_error_log = time.monotonic()
            took = time.monotonic() - started
            if took > interval:
                stats["slow"] += 1
            if time.monotonic() - last_report >= 300:
                extra = (f", exported {exporter.written}, buffered {exporter.pending}, dropped {exporter.dropped}"
                         if exporter else "")
                log.info("Status: %d samples, %d slow, %d errors in last 5 min%s.",
                         stats["samples"], stats["slow"], stats["errors"], extra)
                stats = {"samples": 0, "slow": 0, "errors": 0}
                last_report = time.monotonic()
            next_tick += interval
            delay = next_tick - time.monotonic()
            if delay < -interval:            # fell behind (system sleep, heavy load): resync
                next_tick = time.monotonic()
                delay = 0.0
            stop.wait(max(0.0, delay))
    finally:
        if exporter:
            exporter.close(timeout=5.0)
        ife.set_default_exporter(None)
        log.info("SecondX stopped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    parser = argparse.ArgumentParser(description="SecondX high-precision infrastructure telemetry agent")
    parser.add_argument("--config", default=os.environ.get("SECONDX_CONFIG", str(BASE_DIR / "config.json")))
    parser.add_argument("--once", action="store_true", help="print one sample as a table and exit")
    parser.add_argument("--check", action="store_true", help="validate config and InfluxDB connectivity")
    parser.add_argument("--dry-run", action="store_true", help="sample but export nothing")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    parser.add_argument("--version", action="version", version=f"SecondX {__version__}")
    args = parser.parse_args(argv)

    config_path = Path(args.config).resolve()
    try:
        cfg, notes = load_config(config_path)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.once:
        return run_once(cfg)
    if args.check:
        return run_check(cfg, notes)
    setup_logging(cfg, config_path.parent, args.verbose)
    try:
        return run_agent(cfg, notes, args.dry_run, config_path.parent)
    except Exception:  # noqa: BLE001 - services have no console: make the reason visible in the log
        log.exception("SecondX stopped because of an unexpected error.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
