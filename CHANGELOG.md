# Changelog

## 2.1.0 - 2026-09-26

Strict mode: the agent either runs with second-level precision without disturbing the host, or stops immediately. Built for critical servers.

### Added
- **Strict mode** (`strict_mode`, default on) with explicit exit codes: 0 stopped, 1 error, 2 config, 3 own CPU/RAM limit, 4 export safety limit, 5 precision lost.
- **Own resource limits** (`limits`): RSS checked every sample and stops on the first breach; CPU averaged over a short window (`cpu_window_seconds`) after a 5 s start-up warm-up.
- **Precision guard** (`precision`): stops after `max_missed_slots` consecutive late samples; clock jumps (> 30 s) resync instead of stopping.
- **Disk spool for InfluxDB outages:** a separate thread writes the oldest points to `spool/` in fixed-size JSONL blocks (fsync + atomic rename), so RAM stays bounded during long outages. Spooled blocks survive restarts and are exported first, with their original timestamps.
- **Export safety limits:** `max_outage_hours`, `max_spool_mb`, `min_free_disk_mb`; a full RAM buffer now stops the agent instead of dropping data.
- **Backlog pacing** (`backlog_cpu_percent`): catch-up after an outage is paced by the sender thread's own CPU time.
- **Supervisor** (`--supervise`, used by the Windows task): restarts on codes 1 and 5 at most `restart.max_attempts` times, `delay_seconds` apart; never restarts on 0, 2, 3, 4. The agent exits by itself if the supervisor is killed.
- systemd unit: `Restart=on-failure`, `RestartSec=300`, start limit, `RestartPreventExitStatus=2 3 4`, and kernel backstops `MemoryMax=400M`, `CPUQuota=50%`.

### Changed
- `influx.max_buffer_points` is replaced by `influx.max_memory_points` (old key still accepted). Points are never silently dropped any more.
- Status line reports missed slots and spooled points.

## 2.0.0 - 2026-09-26

Complete overhaul for reliable second-level monitoring and easy enterprise deployment.

### Fixed
- **Per-process values were overwritten** when several processes shared a name (e.g. 30 × `chrome.exe`): only the last one was reported. Processes are now aggregated by name.
- **"Network" per-process metrics were disk counters** (`io_counters()[2]/[3]` = read/write bytes). psutil has no per-process network counters; network is now measured system-wide with real `net_io_counters`.
- **Disk rates were divided by 1024 twice** (labelled KB/s, actually MB/s, then truncated to 0 by `int()`), and the elapsed time was measured inside the loop instead of between samples. Rates are now KB/s over the real interval between samples; PID reuse and counter resets can no longer create negative values or spikes.
- **Sampling was not per second:** `psutil.cpu_percent(interval=1)` blocked 1 s every cycle and the per-process scan took 1-2 s on Windows. CPU is now measured non-blocking, and on Windows all processes are read with a single `NtQuerySystemInformation` call (~5 ms instead of ~2 s).
- **One unexpected file stopped the agent:** any `*.json` in the working directory without a date name crashed `logcontr()` and the whole agent exited. Log retention now uses rotating log files and only touches its own files.
- **InfluxDB outages froze the loop:** up to 26 synchronous requests per second, each waiting for a timeout. Export is now batched, non-blocking and retried with backoff; points are buffered and written later with their original timestamps.
- `log_retention_days` in `config.json` was ignored (14 was hard-coded).
- Every access-denied process produced a log line every second; logs and journald were flooded by debug `print`s.
- Values below 1 were truncated to 0 (`int()`); values are now floats.
- The agent depended on the current working directory; files are now resolved relative to the config file.
- A missing `influxdb-client` package crashed the agent without a log entry; it now logs a clear error and falls back to local files.

### Added
- `--once`, `--check`, `--dry-run`, `--verbose`, `--config` command-line options.
- `host` tag on every point for multi-server setups; `rank` tag for top-N ordering.
- Local JSON-lines output (`data/metrics-YYYY-MM-DD.jsonl`) when no InfluxDB token is configured.
- Environment-variable configuration (`SECONDX_INFLUX_TOKEN`, ...) to keep secrets out of files.
- Windows service installer (`install_windows.ps1`, Scheduled Task as SYSTEM) and uninstaller.
- Linux installer (`install_linux.sh`) with a non-root user, private venv and a hardened systemd unit.
- Docker Compose stack with secrets in `.env`, pinned Grafana version, health checks and a pre-provisioned Grafana datasource and dashboard.
- Unit tests (`tests/`), graceful shutdown on SIGTERM / Ctrl+C / Ctrl+Break, periodic status line.

### Changed
- New data schema: measurements `secondx_system` and `secondx_process` replace `Custom_scripts` / `EX134*` fields.
- `wait_time` is now `interval_seconds` (old key still accepted); `token.json` is deprecated (still read) and no longer committed.
- Minimum Python version: 3.9.
