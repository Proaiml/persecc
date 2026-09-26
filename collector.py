"""
Collector module for SecondX.

Takes one high-resolution sample of the host: system-wide CPU / RAM / disk /
network rates and per-process CPU, RAM, disk-read and disk-write figures.

Design notes
------------
* Processes are aggregated by *name* (e.g. all ``chrome.exe`` instances are
  summed) so that a program split into many processes is ranked correctly.
* Disk rates are computed per PID from monotonic counters and divided by the
  real time elapsed between two samples. A PID seen for the first time, or a
  PID whose name changed (PID reuse), contributes no rate until its second
  sample - this avoids huge fake spikes.
* On Windows all processes are read with ONE ``NtQuerySystemInformation``
  call (see ``win_snapshot.py``): ~5 ms instead of 1-2 s with per-process
  psutil calls. CPU % is derived from CPU-time deltas. Other platforms use
  ``psutil.process_iter`` (fast there), whose cached ``Process`` objects
  measure ``cpu_percent`` since the previous sample without blocking.
* psutil does not expose per-process network traffic on any platform, so
  network is reported system-wide only.
"""
from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass, field

import psutil

import win_snapshot

KB = 1024.0


@dataclass
class Sample:
    timestamp_ns: int
    interval_s: float | None
    system: dict[str, float] = field(default_factory=dict)
    processes: dict[str, dict[str, float]] = field(default_factory=dict)  # metric -> {name: value}


class Collector:
    PROCESS_METRICS = ("cpu", "ram", "disk_read", "disk_write")

    def __init__(self, use_fast_windows_path: bool = True) -> None:
        self.cpu_count = psutil.cpu_count() or 1
        self.total_memory = float(psutil.virtual_memory().total) or 1.0
        self.fast = bool(use_fast_windows_path and win_snapshot.AVAILABLE)
        if self.fast:
            try:
                win_snapshot.snapshot()
            except OSError:
                self.fast = False
        self._prev_time: float | None = None
        self._prev_proc_io: dict[int, tuple[str, int, int]] = {}
        self._prev_cpu_time: dict[int, tuple[str, float]] = {}
        self._prev_disk: tuple[int, int] | None = None
        self._prev_net: tuple[int, int] | None = None
        # Prime the non-blocking CPU counters so the first real sample is valid.
        psutil.cpu_percent(interval=None)
        for proc in psutil.process_iter():
            try:
                proc.cpu_percent(interval=None)
            except (psutil.Error, OSError):
                pass

    @staticmethod
    def _rate(new: int, old: int, seconds: float) -> float:
        # Counters can wrap or reset (driver reload, PID reuse): never negative.
        return max(0, new - old) / KB / seconds

    def sample(self) -> Sample:
        now = time.monotonic()
        interval = None if self._prev_time is None else max(1e-6, now - self._prev_time)
        self._prev_time = now
        result = Sample(timestamp_ns=time.time_ns(), interval_s=interval,
                        processes={m: {} for m in self.PROCESS_METRICS})

        seen_io: dict[int, tuple[str, int, int]] = {}
        if self.fast:
            seen_io = self._sample_windows(result, interval)
        for proc in (() if self.fast else psutil.process_iter(
                ["pid", "name", "cpu_percent", "memory_percent", "io_counters"], ad_value=None)):
            info = proc.info
            pid = int(info.get("pid") or 0)
            name = str(info.get("name") or f"pid-{pid}")
            cpu = info.get("cpu_percent")
            if cpu is not None:
                result.processes["cpu"][name] = result.processes["cpu"].get(name, 0.0) + cpu / self.cpu_count
            ram = info.get("memory_percent")
            if ram is not None:
                result.processes["ram"][name] = result.processes["ram"].get(name, 0.0) + ram
            io = info.get("io_counters")
            if io is not None:
                read_b, write_b = int(io.read_bytes), int(io.write_bytes)
                seen_io[pid] = (name, read_b, write_b)
                prev = self._prev_proc_io.get(pid)
                if interval and prev is not None and prev[0] == name:
                    dr = result.processes["disk_read"]
                    dw = result.processes["disk_write"]
                    dr[name] = dr.get(name, 0.0) + self._rate(read_b, prev[1], interval)
                    dw[name] = dw.get(name, 0.0) + self._rate(write_b, prev[2], interval)
        self._prev_proc_io = seen_io

        vm = psutil.virtual_memory()
        self.total_memory = float(vm.total) or self.total_memory
        result.system["cpu_percent"] = float(psutil.cpu_percent(interval=None))
        result.system["ram_percent"] = float(vm.percent)
        result.system["ram_used_gb"] = float(vm.used) / 1e9
        result.system["process_count"] = float(len(seen_io) or len(result.processes["ram"]))

        disk = self._disk_counters()
        if disk is not None:
            if interval and self._prev_disk is not None:
                result.system["disk_read_kbps"] = self._rate(disk[0], self._prev_disk[0], interval)
                result.system["disk_write_kbps"] = self._rate(disk[1], self._prev_disk[1], interval)
            self._prev_disk = disk

        net = self._net_counters()
        if net is not None:
            if interval and self._prev_net is not None:
                result.system["net_sent_kbps"] = self._rate(net[0], self._prev_net[0], interval)
                result.system["net_recv_kbps"] = self._rate(net[1], self._prev_net[1], interval)
            self._prev_net = net
        return result

    def _sample_windows(self, result: Sample, interval: float | None) -> dict[int, tuple[str, int, int]]:
        """Per-process figures from one NtQuerySystemInformation snapshot."""
        snap = win_snapshot.snapshot()
        cpu, ram = result.processes["cpu"], result.processes["ram"]
        dr, dw = result.processes["disk_read"], result.processes["disk_write"]
        seen_io: dict[int, tuple[str, int, int]] = {}
        seen_cpu: dict[int, tuple[str, float]] = {}
        for pid, (name, cpu_s, rss, read_b, write_b) in snap.items():
            ram[name] = ram.get(name, 0.0) + rss * 100.0 / self.total_memory
            seen_io[pid] = (name, read_b, write_b)
            seen_cpu[pid] = (name, cpu_s)
            if not interval:
                continue
            prev_cpu = self._prev_cpu_time.get(pid)
            if prev_cpu is not None and prev_cpu[0] == name:
                pct = max(0.0, cpu_s - prev_cpu[1]) * 100.0 / interval / self.cpu_count
                cpu[name] = cpu.get(name, 0.0) + min(pct, 100.0)
            prev = self._prev_proc_io.get(pid)
            if prev is not None and prev[0] == name:
                dr[name] = dr.get(name, 0.0) + self._rate(read_b, prev[1], interval)
                dw[name] = dw.get(name, 0.0) + self._rate(write_b, prev[2], interval)
        self._prev_cpu_time = seen_cpu
        return seen_io

    @staticmethod
    def _disk_counters() -> tuple[int, int] | None:
        try:
            d = psutil.disk_io_counters()
        except (OSError, RuntimeError):
            return None
        return None if d is None else (int(d.read_bytes), int(d.write_bytes))

    @staticmethod
    def _net_counters() -> tuple[int, int] | None:
        try:
            n = psutil.net_io_counters()
        except (OSError, RuntimeError):
            return None
        return None if n is None else (int(n.bytes_sent), int(n.bytes_recv))


def hostname() -> str:
    """Host tag used to separate servers in a shared InfluxDB bucket."""
    return socket.gethostname() or os.environ.get("COMPUTERNAME", "host")
