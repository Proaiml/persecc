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


def top(data_dict, n: int = 6, exclude: Iterable[str] = (), min_value: float = 0.0) -> list[tuple[str, float]]:
    """
    Returns the ``n`` highest consumers as ``[(name, value), ...]``.

    ``exclude`` is matched case-insensitively (e.g. "System Idle Process",
    which would otherwise always rank first on Windows). Values not greater
    than ``min_value`` are skipped so idle processes do not fill the list.
    """
    skip = {str(x).lower() for x in exclude}
    ranked: list[tuple[str, float]] = []
    for name, value in shower(data_dict).items():
        if str(name).lower() in skip:
            continue
        number = _numeric(value)
        if number <= min_value:
            break
        ranked.append((str(name), number))
        if len(ranked) >= max(0, int(n)):
            break
    return ranked
