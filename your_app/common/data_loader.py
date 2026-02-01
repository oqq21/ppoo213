"""data_loader.py — 엑셀 전체 시트를 결합해 단일 DataFrame 생성"""
from __future__ import annotations
import pandas as pd
from your_app.common.config import dbg

def load_item_data(excel_file: str) -> pd.DataFrame:
    sheets = pd.read_excel(excel_file, sheet_name=None)
    frames = []
    for sheet, df in sheets.items():
        t = df.copy(); t["sheet"] = sheet; frames.append(t)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    required = {"itemName","gender","reqLevel"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"엑셀 필수 컬럼 누락: {sorted(missing)}")
    dbg("loaded:", len(out), "rows")
    return _add_search_cache_columns(out)

# ─────────────────────────────────────────────────────────────
# 검색 캐시 컬럼 추가: itemName_nospace / itemName_choseong
#  - 앱 시작 시 1회 생성하여 검색 시 벡터화된 contains로 사용
#  - 기존 컬럼/기능에는 영향 없음
# ─────────────────────────────────────────────────────────────
def _add_search_cache_columns(df: pd.DataFrame) -> pd.DataFrame:
    try:
        from your_app.common.hangul_utils import to_choseong
        t = df.copy()
        # 공백 제거본
        if "itemName_nospace" not in t.columns:
            t["itemName_nospace"] = t["itemName"].astype(str).str.replace(" ", "", regex=False)
        # 초성 변환본 (to_choseong 내부에서 공백 제거 수행)
        if "itemName_choseong" not in t.columns:
            t["itemName_choseong"] = t["itemName"].map(lambda s: to_choseong(s))
        return t
    except Exception:
        # 캐시 생성 실패해도 원본 반환 (기능 폴백)
        return df

# 기존 로더 반환 직전에 캐시 컬럼을 추가
def load_item_data_with_cache(excel_file: str) -> pd.DataFrame:
    df = load_item_data(excel_file)
    return _add_search_cache_columns(df)
