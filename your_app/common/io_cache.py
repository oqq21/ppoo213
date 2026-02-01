# your_app/common/io_cache.py
from __future__ import annotations
from functools import lru_cache
import os, threading, time
from typing import Tuple
try:
    import openpyxl
except Exception:  # pragma: no cover
    openpyxl = None

_lock = threading.Lock()
# Simple cache keyed by (abspath, mtime_ns, data_only)
# We don't use lru_cache directly because we want to invalidate on mtime changes.
_cache = {}

def _key(path: str, data_only: bool) -> Tuple[str, int, bool]:
    ap = os.path.abspath(path)
    try:
        mt = os.stat(ap).st_mtime_ns
    except Exception:
        mt = -1
    return (ap, mt, bool(data_only))

def open_workbook_cached(path: str, data_only: bool = True):
    """Open an Excel workbook with a tiny cache. Reloads when the file mtime changes.
    Keeps up to ~4 recent workbooks to avoid unbounded memory growth.
    """
    if openpyxl is None:
        raise RuntimeError("openpyxl is not available")
    k = _key(path, data_only)
    with _lock:
        hit = _cache.get(k)
        if hit is not None:
            return hit
        # purge stale entries for same path with older mtimes
        ap = os.path.abspath(path)
        to_del = [kk for kk in _cache.keys() if kk[0] == ap and kk != k]
        for kk in to_del:
            try:
                _cache.pop(kk, None)
            except Exception:
                pass
        wb = openpyxl.load_workbook(ap, data_only=data_only)
        _cache[k] = wb
        # bound cache size
        if len(_cache) > 4:
            # drop oldest by insertion order
            try:
                oldest = next(iter(_cache.keys()))
                _cache.pop(oldest, None)
            except Exception:
                pass
        return wb
