# your_app/processing/sales_store.py
from __future__ import annotations
import time
import os
from typing import Optional
import pandas as pd
try:
    from your_app.common import debugger
except Exception:  # pragma: no cover
    class _Dummy:
        def log(self, *a, **k): pass
    debugger = _Dummy()

# Columns that legacy_processor needs for Parquet branch
# 변경: add 스탯 컬럼 11개 추가 (parquet에 이미 계산되어 있음)
MIN_COLUMNS_FOR_PROCESSOR = [
    "시트명", "A", "B", "C", "D", "E", "F", "I", "날짜(파일명)",
    "힘_add", "덱_add", "럭_add", "공_add", "마_add", "피_add",
    "명_add", "회_add", "이속_add", "점프_add", "물방_add", "마방_add",
    "price_history_json",
]

_cache_df: Optional[pd.DataFrame] = None
_cache_path: Optional[str] = None
_cache_info = {}
_cache_mtime_ns: int | None = None
_cache_columns: set | None = None
_duckdb_con = None
_duckdb_columns: set | None = None
_duckdb_path: str | None = None

def duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
        return True
    except Exception:
        return False

def is_duckdb() -> bool:
    return _duckdb_con is not None

def preload_sales_duckdb(path: str) -> None:
    """Register parquet file in DuckDB without loading full data into memory."""
    global _duckdb_con, _duckdb_columns, _duckdb_path, _cache_df, _cache_path, _cache_info, _cache_mtime_ns, _cache_columns
    import duckdb
    ap = os.path.abspath(path)
    # reset pandas cache
    _cache_df = None
    _cache_columns = None
    _cache_mtime_ns = None
    # reuse connection if same path
    if _duckdb_con is not None and _duckdb_path == ap:
        return
    con = duckdb.connect(database=":memory:")
    _duckdb_path = ap
    # Fetch schema without loading full data.
    try:
        cur = con.execute("SELECT * FROM read_parquet(?) LIMIT 0", [ap])
        _duckdb_columns = {d[0] for d in (cur.description or [])}
    except Exception:
        _duckdb_columns = None
    _duckdb_con = con
    _cache_path = ap
    _cache_info = {
        "rows": None,
        "cols": None,
        "cols_loaded": None,
        "mem_bytes": 0,
        "load_sec": 0.0,
        "mode": "duckdb",
    }

def duckdb_query(sql: str, params: list | None = None) -> pd.DataFrame:
    if _duckdb_con is None:
        raise RuntimeError("sales_store: duckdb backend is not initialized")
    return _duckdb_con.execute(sql, params or []).df()

def duckdb_columns() -> set | None:
    return _duckdb_columns

def duckdb_path() -> str | None:
    return _duckdb_path

def preload_sales(path: str, force: bool = False, columns: list[str] | None = None) -> None:
    global _cache_df, _cache_path, _cache_info, _duckdb_con, _duckdb_columns, _duckdb_path
    t0 = time.time()
    global _cache_mtime_ns
    ap = os.path.abspath(path)
    mt = None
    try:
        mt = os.stat(ap).st_mtime_ns
    except Exception:
        mt = None
    # Skip reload if same file and mtime unchanged unless force
    if (not force) and (_cache_df is not None) and (_cache_path == ap) and (_cache_mtime_ns == mt):
        return
    # reset duckdb backend
    _duckdb_con = None
    _duckdb_columns = None
    _duckdb_path = None
    ext = os.path.splitext(ap)[1].lower()
    if ext in (".pkl", ".pickle"):
        df = pd.read_pickle(ap)
    else:
        try:
            df = pd.read_parquet(ap, engine="pyarrow", columns=columns)
        except Exception:
            # 컬럼 누락 등으로 실패하면 전체 로드로 폴백하여 구버전 parquet도 처리
            df = pd.read_parquet(ap, engine="pyarrow")
    _cache_mtime_ns = mt
    path = ap
    _cache_df = df
    global _cache_columns
    _cache_columns = set(df.columns)
    _cache_path = path
    _cache_info = {
        "rows": int(len(df)),
        "cols": int(len(df.columns)),
        "cols_loaded": list(df.columns),
        "mem_bytes": int(df.memory_usage(deep=True).sum() if hasattr(df, "memory_usage") else 0),
        "load_sec": float(time.time() - t0),
    }
    try:
        debugger.log("sales_cache_preload", path=path, **_cache_info)
    except Exception:
        pass


def ensure_min_columns(path: str) -> None:
    """Ensure cache is loaded with at least MIN_COLUMNS_FOR_PROCESSOR.
    If cache is empty or missing some, reload just those columns.
    """
    global _cache_df, _cache_columns
    if is_duckdb():
        return
    need = set(MIN_COLUMNS_FOR_PROCESSOR)
    if (_cache_df is None) or (_cache_columns is None) or (not need.issubset(_cache_columns)):
        preload_sales(path, force=True, columns=MIN_COLUMNS_FOR_PROCESSOR)

def get_sales() -> pd.DataFrame:
    if _cache_df is None:
        raise RuntimeError("sales_store: preload_sales()가 호출되지 않았습니다.")
    return _cache_df

def info() -> dict:
    info = dict(path=_cache_path, **_cache_info)
    if is_duckdb():
        info["mode"] = "duckdb"
    else:
        info.setdefault("mode", "pandas")
    return info
