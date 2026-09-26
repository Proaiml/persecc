"""
Fast Windows process snapshot for SecondX.

On Windows, psutil opens every process separately and, for protected
processes, falls back to enumerating *all* processes again - with a few hundred
processes a full per-process scan takes 1-2 seconds, which defeats
second-level sampling.

``NtQuerySystemInformation(SystemProcessInformation)`` returns the name, CPU
times, working set and I/O transfer counters of every process in ONE system
call (a few milliseconds). The values are the same ones psutil reports:

* cpu time  = UserTime + KernelTime          (psutil ``cpu_times``)
* rss       = WorkingSetSize                  (psutil ``memory_info().rss``)
* read/write bytes = Read/WriteTransferCount  (psutil ``io_counters``)
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

AVAILABLE = sys.platform == "win32" and ctypes.sizeof(ctypes.c_void_p) == 8

_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
_SYSTEM_PROCESS_INFORMATION = 5


class _UnicodeString(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                ("Buffer", ctypes.c_void_p)]


class _ProcessInfo(ctypes.Structure):
    """SYSTEM_PROCESS_INFORMATION (64-bit layout, fields up to the I/O counters)."""
    _fields_ = [
        ("NextEntryOffset", wintypes.ULONG),
        ("NumberOfThreads", wintypes.ULONG),
        ("WorkingSetPrivateSize", ctypes.c_longlong),
        ("HardFaultCount", wintypes.ULONG),
        ("NumberOfThreadsHighWatermark", wintypes.ULONG),
        ("CycleTime", ctypes.c_ulonglong),
        ("CreateTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("KernelTime", ctypes.c_longlong),
        ("ImageName", _UnicodeString),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ("HandleCount", wintypes.ULONG),
        ("SessionId", wintypes.ULONG),
        ("UniqueProcessKey", ctypes.c_size_t),
        ("PeakVirtualSize", ctypes.c_size_t),
        ("VirtualSize", ctypes.c_size_t),
        ("PageFaultCount", wintypes.ULONG),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivatePageCount", ctypes.c_size_t),
        ("ReadOperationCount", ctypes.c_longlong),
        ("WriteOperationCount", ctypes.c_longlong),
        ("OtherOperationCount", ctypes.c_longlong),
        ("ReadTransferCount", ctypes.c_longlong),
        ("WriteTransferCount", ctypes.c_longlong),
        ("OtherTransferCount", ctypes.c_longlong),
    ]


_buffer_size = 1 << 20


def snapshot() -> dict[int, tuple[str, float, int, int, int]]:
    """{pid: (name, cpu_seconds, rss_bytes, read_bytes, write_bytes)} for every process."""
    global _buffer_size
    ntdll = ctypes.WinDLL("ntdll")
    query = ntdll.NtQuerySystemInformation
    query.restype = ctypes.c_ulong
    query.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    for _ in range(8):
        buf = ctypes.create_string_buffer(_buffer_size)
        needed = ctypes.c_ulong(0)
        status = query(_SYSTEM_PROCESS_INFORMATION, buf, _buffer_size, ctypes.byref(needed))
        if status == _STATUS_INFO_LENGTH_MISMATCH:
            _buffer_size = max(_buffer_size * 2, needed.value + (1 << 16))
            continue
        if status != 0:
            raise OSError(f"NtQuerySystemInformation failed: 0x{status:08X}")
        break
    else:
        raise OSError("NtQuerySystemInformation: buffer kept growing")

    base = ctypes.addressof(buf)
    result: dict[int, tuple[str, float, int, int, int]] = {}
    offset = 0
    while True:
        info = _ProcessInfo.from_address(base + offset)
        pid = int(info.UniqueProcessId or 0)
        if info.ImageName.Buffer and info.ImageName.Length:
            name = ctypes.wstring_at(info.ImageName.Buffer, info.ImageName.Length // 2)
        else:
            name = "System Idle Process" if pid == 0 else f"pid-{pid}"
        cpu = (info.UserTime + info.KernelTime) / 1e7          # 100 ns units -> seconds
        result[pid] = (name, cpu, int(info.WorkingSetSize),
                       int(info.ReadTransferCount), int(info.WriteTransferCount))
        if not info.NextEntryOffset:
            break
        offset += info.NextEntryOffset
    return result
