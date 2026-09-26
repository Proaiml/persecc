"""
Lissozis Comparison & Ranking Module
Author: İlhan Koçaslan (Proaiml)

Compares all active system processes by their instantaneous resource consumption
(CPU %, RAM %, Disk Read KB/s, Disk Write KB/s) and ranks them to isolate top consumers.
"""
from __future__ import annotations

from typing import Iterable


def _numeric(value) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError):
        return 0.0
    return number if number == number else 0.0  # NaN -> 0


def shower(data_dict):
    """
    Ranks every process in ``data_dict`` from the highest consumer to the lowest.

    Parameters:
        data_dict (dict): process name -> metric value
                          (e.g., {'chrome.exe': 15.4, 'python.exe': 8.2, ...})

    Returns:
        dict: insertion-ordered by value, descending, so iterating yields the
              highest resource-consuming processes first.
    """
    if not isinstance(data_dict, dict):
        return {}
    return dict(sorted(data_dict.items(), key=lambda item: _numeric(item[1]), reverse=True))


def ranked(data_dict, n: int = 6, exclude: Iterable[str] = (), min_value: float = 0.0,
           max_ties: int | None = None) -> list[tuple[int, str, float]]:
    """
    Returns the ``n`` highest consumers as ``[(rank, name, value), ...]``.

    Equal values are common (Windows counts CPU time in whole clock ticks, so
    small processes often show exactly the same %), therefore ties are handled
    explicitly instead of being left to the order the OS listed the processes:

    * equal values share a rank (1, 2, 2, 4 ...), so no process is shown as
      "ahead" of another that consumed exactly as much;
    * processes tied with the n-th one are all included - none is dropped at
      random - up to ``max_ties`` extra entries (default ``n``, i.e. at most
      2n in total) so a large tie can never flood the output;
    * within a tie the order is alphabetical, so the result is deterministic.

    ``exclude`` is matched case-insensitively (e.g. "System Idle Process",
    which would otherwise always rank first on Windows). Values not greater
    than ``min_value`` are skipped so idle processes do not fill the list.
    """
    n = max(0, int(n))
    extra = n if max_ties is None else max(0, int(max_ties))
    skip = {str(x).lower() for x in exclude}
    items = []
    for name, value in (data_dict.items() if isinstance(data_dict, dict) else ()):
        number = _numeric(value)
        if number > min_value and str(name).lower() not in skip:
            items.append((str(name), number))
    items.sort(key=lambda item: (-item[1], item[0].lower(), item[0]))
    result: list[tuple[int, str, float]] = []
    for position, (name, number) in enumerate(items, 1):
        tied_with_previous = bool(result) and number == result[-1][2]
        if position > n and not (tied_with_previous and position <= n + extra):
            break
        result.append((result[-1][0] if tied_with_previous else position, name, number))
    return result


def top(data_dict, n: int = 6, exclude: Iterable[str] = (), min_value: float = 0.0) -> list[tuple[str, float]]:
    """``ranked()`` without the rank: ``[(name, value), ...]``."""
    return [(name, value) for _, name, value in ranked(data_dict, n, exclude, min_value)]
