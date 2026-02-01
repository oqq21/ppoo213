"""grouping.py — (sheet, gender, reqLevel) 기준 그룹 요약"""
from __future__ import annotations
import pandas as pd
from your_app.common.config import dbg

def group_by_sgr(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["대표아이템명","sheet","gender","reqLevel","개수"])
    g = df.copy()
    g["gender"] = g["gender"].fillna("").astype(str).str.strip()
    keys = ["sheet","gender","reqLevel"]
    gb = g.groupby(keys, dropna=False, as_index=False)
    def first_name(s: pd.Series): s = s.dropna(); return s.iloc[0] if not s.empty else ""
    out = gb.agg(대표아이템명=("itemName", first_name), 개수=("itemName","count")).reset_index(drop=True)
    out = out.sort_values(["개수","대표아이템명"], ascending=[False,True])
    out = out[["대표아이템명","sheet","gender","reqLevel","개수"]]
    dbg("groups:", len(out))
    return out
