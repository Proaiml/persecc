# Changelog

## 2.3.0 - 2026-09-26

Easy, trustworthy installation.

### Added
- **`SecondX-Setup.exe`** (GitHub Releases): one-file Windows installer, no Python needed. Shows what it will do, asks for the InfluxDB settings, tests the connection, installs to `Program Files` / `ProgramData`, registers the Scheduled Task and an "Apps & features" entry, then verifies from the log that the agent runs. Upgrades keep the settings; `--quiet` for mass deployment (GPO, SCCM, Intune); `--uninstall` removes everything. Built reproducibly from source with `packaging/windows/build.ps1` in a clean virtual environment; `SHA256SUMS.txt` is published with it.
- `influx.token_file` / `SECONDX_INFLUX_TOKEN_FILE`: read the token from a file that only SYSTEM and Administrators (or root) can open.
- Single-instance lock (`secondx.lock`): a second agent with the same config exits immediately (code 2) instead of doubling every point.
- The agent runs as a frozen `SecondX.exe`, including the supervisor.
- README: security summary and an event / risk table with estimated probabilities, data-retention percentages and an expected-completeness calculation.

### Changed
- `install_windows.ps1` stores the token in a protected `secondx.token` file instead of the machine-wide `SECONDX_INFLUX_TOKEN` variable that every local user can read (the old variable is removed).

## 2.2.0 - 2026-09-26

Faster, safer export: live data first, parallel catch-up, and a sender that is watched every second.

### Added
- **Live data is never queued behind a backlog.** A dedicated live sender writes new samples immediately; spooled blocks are uploaded by separate workers.
- **Parallel catch-up:** `secondx-upload-N` workers start when blocks are waiting and scale out (one per second, up to `influx.backlog_max_workers`, default 4) while uploads mostly wait on the server; they exit when the backlog is gone. All workers share one CPU budget (`backlog_cpu_percent`). 24 h of backlog (2.16 M points): 46.5 s with 1 worker, 33.2 s with 4 against a local InfluxDB.
- **Sender liveness:** the agent checks every second that the export threads are alive and that no single write hangs longer than `max(30 s, 3 × timeout_ms)`; otherwise it stops with code 1 (restartable).
- **Delivery delay** (sample → written) is reported in the 5-minute status line.
- Blocks InfluxDB refuses (HTTP 400/422) are kept as `*.rejected`, unreadable blocks as `*.bad`; neither blocks the queue.

### Changed
- Spool blocks are stored as InfluxDB line protocol (`block-*.lp`), so uploading needs no conversion: 6.6 ms → ~0 ms CPU per 1500-point block; 6 h of backlog uploads in 6.4 s instead of 110 s. `block-*.jsonl` blocks from 2.1.0 are still read.
- The InfluxDB client keeps a keep-alive connection pool sized for the live sender and the upload workers.
- On shutdown, a batch whose write is still in progress is also saved to the spool (a duplicate write is harmless in InfluxDB, a lost one is not).

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

### Fixed
- **Processes with equal values were ranked arbitrarily.** Windows counts CPU time in clock ticks, so small processes often measure exactly the same value (on a test machine the 6th and 7th CPU consumers were tied in 11 of 30 samples). Which one made the top N, and which got the better rank, depended on the order the OS listed processes and changed every second. Now equal values share a rank (1, 2, 2, 4), everything tied with the N-th entry is included (at most 2N), and ties are ordered alphabetically (`lissozis.ranked`).
- In non-strict mode the export safety limits were not checked, so the RAM buffer could grow without bound; they now apply in every mode.

### Changed
- `influx.max_buffer_points` is replaced by `influx.max_memory_points` (old key still accepted). Points are never silently dropped any more.
- Status line reports missed slots and spooled points.

## 2.0.0 - 2026-09-26

Complete overhaul for reliable second-level monitoring and easy deployment.

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
