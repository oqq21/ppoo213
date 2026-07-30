import os
import re
import sys
import html
import json
import tempfile
import importlib
from typing import Optional
from pathlib import Path
from datetime import datetime
import streamlit as st
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

APP_BUILD = "2026-07-30-packet-table-v2"

from your_app.common.data_loader import load_item_data
from your_app.common import query_utils as _query_utils
from your_app.domain.grouping import group_by_sgr
from your_app.api.client import (
    build_params,
    fetch_json_with_retries,
    option_has_any_component,
    parse_trade_json,
    parse_zero_option_token,
)
from your_app.processing.legacy_processor import process_items
from your_app.processing import sales_store
from your_app.processing import packet_store as _packet_store
from your_app.common import remote_data as _remote_data

# Streamlit Cloud는 진입 파일만 다시 실행하면서 이미 import한 하위 모듈을
# 이전 프로세스 메모리에 남길 수 있다. 빌드가 바뀐 경우에만 명시적으로
# 다시 읽어 화면 변환 규칙과 원격 데이터 로더가 즉시 적용되게 한다.
if getattr(_packet_store, "_PP213_APP_BUILD", "") != APP_BUILD:
    _packet_store = importlib.reload(_packet_store)
    _packet_store._PP213_APP_BUILD = APP_BUILD
if getattr(_remote_data, "_PP213_APP_BUILD", "") != APP_BUILD:
    _remote_data = importlib.reload(_remote_data)
    _remote_data._PP213_APP_BUILD = APP_BUILD
if getattr(_query_utils, "_PP213_APP_BUILD", "") != APP_BUILD:
    _query_utils = importlib.reload(_query_utils)
    _query_utils._PP213_APP_BUILD = APP_BUILD

packet_view = _packet_store.packet_view
search_packet_data = _packet_store.search_packet_data
item_color_key = _packet_store.item_color_key
ensure_data_snapshot = _remote_data.ensure_data_snapshot
mask_for_query = _query_utils.mask_for_query

PREFERRED_PARQUET = ["요약본.parquet"]
DATA_CACHE_DIR = Path(tempfile.gettempdir()) / "ppoo213-data"
PACKET_ACTIVE_FILE = DATA_CACHE_DIR / "packet_active.parquet"
PACKET_COMPLETED_FILE = DATA_CACHE_DIR / "packet_completed.parquet"


def _find_sales_file(base_dir: Path) -> Optional[Path]:
    for name in PREFERRED_PARQUET:
        p = base_dir / name
        if p.exists():
            return p
    return None


@st.cache_resource
def _load_items(excel_path: str) -> pd.DataFrame:
    return load_item_data(excel_path)


@st.cache_resource
def _init_sales_backend(sales_path: str, data_version: str) -> str:
    if sales_store.duckdb_available():
        sales_store.preload_sales_duckdb(sales_path)
        return "duckdb"
    sales_store.preload_sales(sales_path, columns=sales_store.MIN_COLUMNS_FOR_PROCESSOR)
    return "pandas"


def _read_tokens(stat1: str, stat2: str, stat3: str) -> list[str]:
    tokens = [stat1, stat2, stat3]
    return [t.strip() for t in tokens if t and t.strip()]


def _format_group_label(row: pd.Series) -> str:
    rep = str(row.get("대표아이템명", ""))
    sheet = str(row.get("sheet", ""))
    gender = str(row.get("gender", ""))
    req = str(row.get("reqLevel", ""))
    cnt = str(row.get("개수", ""))
    return f"{rep} | {sheet}/{gender}/{req} | {cnt}"


def _selected_items_from_group(df_src: pd.DataFrame, row: pd.Series) -> list[str]:
    rep = str(row.get("대표아이템명", "")).strip()
    sheet = str(row.get("sheet", ""))
    gender = str(row.get("gender", ""))
    req = str(row.get("reqLevel", ""))

    if df_src is None or df_src.empty:
        return [rep] if rep else []

    m = (
        (df_src["sheet"].astype(str) == sheet)
        & (df_src["gender"].astype(str) == gender)
        & (df_src["reqLevel"].astype(str) == req)
    )
    names = df_src.loc[m, "itemName"].astype(str).tolist()
    if rep:
        return [rep] + [x for x in names if x != rep]
    return names


def _selected_item_codes_from_group(df_src: pd.DataFrame, row: pd.Series) -> list[int]:
    if df_src is None or df_src.empty or "itemCode" not in df_src.columns:
        return []
    mask = pd.Series(True, index=df_src.index)
    for column in ("sheet", "gender", "reqLevel"):
        if column in df_src.columns and column in row.index:
            mask &= df_src[column].astype(str).eq(str(row.get(column, "")))
    codes = pd.to_numeric(df_src.loc[mask, "itemCode"], errors="coerce").dropna().astype(int)
    return list(dict.fromkeys(codes.tolist()))


BASE_STAT_COLS = ["공", "마", "힘", "덱", "인", "럭", "명", "물방", "마방", "이속", "점프", "회피"]


def _format_base_stats_from_row(row: pd.Series, cols: list[str] = BASE_STAT_COLS) -> str:
    parts: list[str] = []
    for c in cols:
        if c in row.index:
            try:
                v = int(row[c])
            except Exception:
                try:
                    v = int(float(row[c]))
                except Exception:
                    continue
            if v != 0:
                parts.append(f"{c} {v}")
    return "\n".join(parts) if parts else "(모든 기본 스탯 = 0)"


def _format_mtime(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "알 수 없음"


def _format_ctime(path: Path) -> str:
    try:
        ts = path.stat().st_ctime
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "알 수 없음"


def _relative_time(ts) -> str:
    try:
        t = pd.to_datetime(ts, utc=True, errors="coerce")
        if pd.isna(t):
            return ""
        now = pd.Timestamp.now(tz="UTC")
        sec = int(max(0, (now - t).total_seconds()))
        if sec < 60:
            return "방금 전"
        if sec < 3600:
            return f"{sec // 60}분 전"
        if sec < 86400:
            return f"{max(1, sec // 3600)}시간 전"
        return f"{max(1, sec // 86400)}일 전"
    except Exception:
        return ""


def _format_price_man(v) -> str:
    try:
        from your_app.processing.legacy_processor import _format_price_man as _fmt
        return _fmt(v)
    except Exception:
        try:
            return f"{int(float(v)):,}"
        except Exception:
            return ""


def _emph_price(v) -> str:
    s = str(v or "").strip()
    if s:
        if "만" in s or "메소" in s:
            return s
    s = _format_price_man(v)
    return s


ITEM_NAME_COLORS = {
    "gray": "#6b7280",
    "white": "#ffffff",
    "blue": "#1677ff",
    "purple": "#9333ea",
    "yellow": "#e3ad00",
    "lime": "#57b51b",
    "red": "#e11d48",
}


def _item_name_css(color_key: str) -> str:
    key = str(color_key or "").lower()
    key = {"green": "lime", "lightgreen": "lime"}.get(key, key)
    color = ITEM_NAME_COLORS.get(key, ITEM_NAME_COLORS["white"])
    outline = ""
    if key not in ITEM_NAME_COLORS or key == "white":
        outline = (
            "text-shadow: -1px -1px 0 #4b5563, 1px -1px 0 #4b5563, "
            "-1px 1px 0 #4b5563, 1px 1px 0 #4b5563;"
        )
    return f"color: {color}; font-weight: 800; {outline}"


def _style_status_rows(frame: pd.DataFrame):
    """Apply every semantic color to its own cell, never to the whole row."""
    if frame is None or frame.empty:
        return frame
    display = frame.copy()
    item_colors = (
        display.pop("_아이템색")
        if "_아이템색" in display.columns
        else None
    )
    gem_styles = (
        display.pop("_보석셀")
        if "_보석셀" in display.columns
        else None
    )

    styled = display.style.set_properties(
        **{
            "font-size": "16px",
            "font-weight": "700",
        }
    ).set_table_styles([
        {
            "selector": "th",
            "props": [
                ("font-size", "15px"),
                ("font-weight", "800"),
            ],
        },
    ])
    if "상태" in display.columns:
        status_styles = []
        for value in display["상태"]:
            text = str(value)
            if "Active" in text or "판매중" in text:
                status_styles.append("background-color: #e8f2ff; color: #123b67; font-weight: 800;")
            elif "Completed" in text or "판매완료" in text:
                status_styles.append("background-color: #f3e9ff; color: #51247a; font-weight: 800;")
            else:
                status_styles.append("")
        styled = styled.apply(lambda _column: status_styles, axis=0, subset=["상태"])
    if item_colors is not None and "아이템" in display.columns:
        name_styles = [_item_name_css(value) for value in item_colors]
        styled = styled.apply(
            lambda _column: name_styles,
            axis=0,
            subset=["아이템"],
        )
    if gem_styles is not None and "보석" in display.columns:
        colors = {
            "red": "background-color: #ff9b9b; color: #111; font-weight: 800;",
            "green": "background-color: #b9efae; color: #111; font-weight: 800;",
            "white": "background-color: #ffffff; color: #111; font-weight: 800;",
        }
        styles = [colors.get(str(value), colors["white"]) for value in gem_styles]
        styled = styled.apply(lambda _column: styles, axis=0, subset=["보석"])
    return styled


MY_PROFILE_ORDER = {
    "58656317-8ca3-4bde-be58-8052dd6870a8": 1,
    "b7b9d9a7-ba69-487a-8647-9b8324941767": 2,
    "5365ac8a-f25e-4c58-a223-f1beada61727": 3,
    "6ddf963d-186c-45a9-9562-ff01ba0b13c4": 4,
    "64091862-a78f-4627-8c8e-6dcef0e17a5f": 5,
    "d1c30b32-e211-4a4a-b23a-455a61c93370": 6,
    "4a0243f2-b679-44b7-91a1-efc4a9e1e83a": 7,
    "abbf6597-5ed8-4016-99ed-7225ee81b779": 8,
    "c9c67a40-603e-4d96-a013-7a454bedb11b": 9,
    "4406877c-c9e5-4d5b-85a9-4e5c8261d611": 10,
    "e44af2c0-de74-4635-8376-74de8a701fcf": 11,
    "612c4dd5-4813-4080-9133-db980a727188": 12,
}
MY_ACCOUNT_DOT = "🔴"


def _profile_id_from_url(v) -> str:
    s = str(v or "").strip().rstrip("/")
    if not s:
        return ""
    m = re.search(r"/profile/([0-9a-fA-F-]{36})", s)
    if m:
        return m.group(1).lower()
    if re.fullmatch(r"[0-9a-fA-F-]{36}", s):
        return s.lower()
    return ""


def _is_my_profile_url(v) -> bool:
    return _profile_id_from_url(v) in MY_PROFILE_ORDER


def _my_account_mark(v) -> str:
    n = MY_PROFILE_ORDER.get(_profile_id_from_url(v))
    return f"{MY_ACCOUNT_DOT}{n}{MY_ACCOUNT_DOT}" if n else ""


def _normalize_option(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        parts = [str(x).strip() for x in v if str(x).strip()]
        return " ".join(parts)
    s = str(v).strip()
    if s.startswith("[") and s.endswith("]"):
        s = s.strip("[]")
        s = s.replace("'", "").replace('"', "")
        s = s.replace(",", " ")
    s = s.replace("+", " ")
    return " ".join(s.split())


def _offer_label(v) -> str:
    try:
        iv = int(v)
        if iv == 0:
            return "흥정불가"
        if iv == 1:
            return "흥정가능"
        if iv == 2:
            return ""
    except Exception:
        pass
    s = str(v or "")
    if "불가" in s:
        return "흥정불가"
    if "가능" in s:
        return "흥정가능"
    return ""


def _status_label(v) -> str:
    try:
        return "판매중" if bool(v) else "판매완료"
    except Exception:
        return ""


def _format_date_key(v) -> str:
    s = str(v or "")
    if len(s) == 6 and s.isdigit():
        return f"20{s[0:2]}-{s[2:4]}-{s[4:6]}"
    return s


def _price_to_number(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    s = s.replace(",", "").replace(" ", "")
    # Handle Korean units like "1억6500만"
    try:
        if "억" in s:
            parts = s.split("억", 1)
            eok = int(re.findall(r"\d+", parts[0])[0]) if re.findall(r"\d+", parts[0]) else 0
            man = 0
            if "만" in parts[1]:
                man_digits = re.findall(r"\d+", parts[1])
                man = int(man_digits[0]) if man_digits else 0
            return float(eok * 10000 + man)
        if "만" in s:
            man_digits = re.findall(r"\d+", s)
            if man_digits:
                return float(int(man_digits[0]))
    except Exception:
        pass
    digits = re.findall(r"\d+", s)
    if not digits:
        return None
    try:
        return float("".join(digits))
    except Exception:
        return None


def _api_view_df(df: pd.DataFrame, sort_key: str = "time") -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    d = df.copy()
    d["_load_order"] = range(len(d))
    d["_is_my_account"] = d.get("profileUrl", "").map(_is_my_profile_url) if "profileUrl" in d.columns else False
    d["_price_sort"] = d.get("itemPrice", "").map(_price_to_number).fillna(10**18) if "itemPrice" in d.columns else 10**18
    if sort_key == "price" and "itemPrice" in d.columns:
        d["_sort"] = d["_price_sort"]
        d = d.sort_values(
            ["_is_my_account", "_sort", "_load_order"],
            ascending=[False, True, True],
            kind="mergesort",
        ).reset_index(drop=True)
    elif "updated_at" in d.columns:
        d["_sort"] = pd.to_datetime(d["updated_at"], errors="coerce")
        d = d.sort_values(
            ["_is_my_account", "_price_sort", "_sort", "_load_order"],
            ascending=[False, True, False, True],
            kind="mergesort",
        ).reset_index(drop=True)
    d = d.drop(columns=["_sort"], errors="ignore")

    opt_col = "optionSummarize" if "optionSummarize" in d.columns else "option"
    def _color_dot(v):
        m = {"purple": "🟣", "yellow": "🟡", "blue": "🔵", "gray": "⚪", "white": "⚪"}
        return m.get(str(v or "").lower(), "⚪")

    def _api_color_cell(row):
        base = _color_dot(row.get("color", ""))
        mark = _my_account_mark(row.get("profileUrl", ""))
        return f"{base} {mark}" if mark else base

    out = pd.DataFrame({
        "색": d.apply(_api_color_cell, axis=1),
        "상태": d.get("tradeStatus", "").map(lambda x: "🟢 판매중" if bool(x) else "⚫ 판매완료") if "tradeStatus" in d.columns else "",
        "아이템": d.get("itemName", ""),
        "_아이템색": d.get("color", "white"),
        "가격(만)": d.get("itemPrice", "").map(_emph_price) if "itemPrice" in d.columns else "",
        "스탯": d.get(opt_col, "").map(_normalize_option) if opt_col in d.columns else "",
        "코멘트": d.get("comment", "") if "comment" in d.columns else d.get("comment", ""),
        "판매자": d.get("global_name", ""),
        "흥정": d.get("offer_raw", "").map(_offer_label) if "offer_raw" in d.columns else "",
        "경신": d.get("updated_at", "").map(_relative_time) if "updated_at" in d.columns else "",
        "등록": d.get("created_at", "").map(_relative_time) if "created_at" in d.columns else "",
        "서버": d.get("server", ""),
        "프로필": d.get("profileUrl", ""),
    })
    return out


def _proc_view_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    status = (
        df.get("highlight", "").map(lambda x: "🟢 판매중" if bool(x) else "⚫ 판매완료")
        if "highlight" in df.columns
        else pd.Series([""] * len(df), index=df.index)
    )
    status = [
        f"{s} {mark}" if mark else s
        for s, mark in zip(status, df.get("url", "").map(_my_account_mark) if "url" in df.columns else [""] * len(df))
    ]
    color_keys = df.get("stats", "").map(_item_color_from_stat_text)
    out = pd.DataFrame({
        "일전": df.get("days_ago", ""),
        "상태": status,
        "아이템": df.get("item", ""),
        "_아이템색": color_keys,
        "가격(만)": df.get("price", "").map(_emph_price) if "price" in df.columns else "",
        "판매자": df.get("seller", ""),
        "스탯": df.get("stats", ""),
        "비고": df.get("comment", ""),
        "링크": df.get("url", ""),
    })
    return out


_STAT_LABEL_ORDER = ["힘", "덱", "인", "럭", "공", "마", "명", "회피", "이속", "점프", "물방", "마방", "HP", "MP"]
_STAT_LABEL_ALIASES = {"회": "회피", "피": "HP", "hp": "HP", "mp": "MP"}


def _parse_stat_text(value) -> dict[str, int]:
    if isinstance(value, (list, tuple)):
        text = " ".join(str(part) for part in value)
    else:
        text = str(value or "")
    result: dict[str, int] = {}
    pattern = r"(물방|마방|회피|이속|점프|힘|덱|인|럭|공|마|명|회|HP|MP|hp|mp|피)\s*\+?\s*(-?\d+)"
    for name, raw_number in re.findall(pattern, text):
        canonical = _STAT_LABEL_ALIASES.get(name, name)
        result[canonical] = result.get(canonical, 0) + int(raw_number)
    return result


def _item_color_from_stat_text(value) -> str:
    stats = _parse_stat_text(value)
    regular = (
        "힘", "덱", "인", "럭", "공", "명", "회피",
        "이속", "점프", "물방", "마방",
    )
    score = sum(int(stats.get(name, 0)) for name in regular)
    score += int(int(stats.get("HP", 0)) / 10)
    score += int(int(stats.get("MP", 0)) / 10)
    return item_color_key(score)


def _base_stats_for_item(df_items: pd.DataFrame, item_name: str) -> dict[str, int]:
    if df_items is None or df_items.empty:
        return {}
    match = df_items[df_items["itemName"].astype(str).eq(str(item_name))]
    if match.empty:
        return {}
    row = match.iloc[0]
    result = {}
    for label in _STAT_LABEL_ORDER:
        column = "회피" if label == "회피" else label
        if column in row.index:
            result[label] = int(pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").fillna(0).iloc[0])
    return result


def _canonical_total_stats(value, item_name: str, df_items: pd.DataFrame, is_additional: bool) -> tuple[int, ...]:
    stats = _parse_stat_text(value)
    if is_additional:
        base = _base_stats_for_item(df_items, item_name)
        stats = {label: int(base.get(label, 0)) + int(stats.get(label, 0)) for label in _STAT_LABEL_ORDER}
    return tuple(int(stats.get(label, 0)) for label in _STAT_LABEL_ORDER)


def _normalize_key_text(value) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", str(value or "")).lower()


def _combined_market_rows(
    api_sell: pd.DataFrame,
    proc_rows: list[dict],
    df_items: pd.DataFrame,
) -> pd.DataFrame:
    records: list[dict] = []
    api_keys: set[tuple] = set()

    if isinstance(api_sell, pd.DataFrame) and not api_sell.empty:
        for _, row in api_sell.iterrows():
            item = str(row.get("itemName", ""))
            price = int(_price_to_number(row.get("itemPrice")) or 0)
            seller = str(row.get("global_name", ""))
            stats_value = row.get("optionSummarize", "")
            key = (
                _normalize_key_text(item),
                price,
                _canonical_total_stats(stats_value, item, df_items, is_additional=False),
                _normalize_key_text(seller),
            )
            api_keys.add(key)
            records.append({
                "_source_order": 0,
                "_time": pd.to_datetime(row.get("updated_at"), errors="coerce"),
                "출처": "API",
                "상태": "판매중" if bool(row.get("tradeStatus", True)) else "판매완료",
                "아이템": item,
                "가격": price,
                "스탯": _normalize_option(stats_value),
                "판매자": seller,
                "날짜": _relative_time(row.get("updated_at") or row.get("created_at")),
                "비고": str(row.get("comment", "")),
                "링크": str(row.get("profileUrl", "")),
            })

    for row in proc_rows or []:
        item = str(row.get("item", ""))
        price = int(row.get("price_num") or 0)
        seller = str(row.get("seller", ""))
        stats_value = row.get("stats", "")
        key = (
            _normalize_key_text(item),
            price,
            _canonical_total_stats(stats_value, item, df_items, is_additional=True),
            _normalize_key_text(seller),
        )
        if key in api_keys:
            continue
        records.append({
            "_source_order": 1,
            "_time": pd.to_datetime(row.get("date_raw"), errors="coerce"),
            "출처": "Parquet",
            "상태": "판매중" if bool(row.get("highlight", True)) else "판매완료",
            "아이템": item,
            "가격": price,
            "스탯": str(stats_value),
            "판매자": seller,
            "날짜": str(row.get("days_ago", "")),
            "비고": str(row.get("comment", "")),
            "링크": str(row.get("url", "")),
        })

    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame(records)
    frame = frame.sort_values(
        ["_source_order", "_time"],
        ascending=[True, False],
        kind="mergesort",
    )
    return frame.drop(columns=["_source_order", "_time"]).reset_index(drop=True)


def _build_site_search_url(sheet: str, gender: str, reqlevel: int, item_name: str, stat_tokens: list[str]) -> str:
    try:
        from your_app.api.client import build_params
        from your_app.common.config import ORDER
        from urllib.parse import urlencode, quote
    except Exception:
        return ""

    p = build_params(sheet, gender, reqlevel, item_name=item_name, stat_tokens=stat_tokens)
    if not p:
        return ""

    item_code = (p.get("itemCode") or "").strip()
    if item_code:
        base = f"https://mapleland.gg/item/{quote(item_code)}"
        allow = [
            "lowPrice", "highPrice", "lowincPAD", "highincPAD", "lowincMAD", "highincMAD",
            "lowHapma", "highHapma", "lowUpgrade", "highUpgrade", "lowTuc", "highTuc",
        ]
        have = set(k for k in p.keys() if p.get(k) not in (None, ""))
        q = [(k, p[k]) for k in allow if k in have]
        if "lowPrice" not in have:
            q.append(("lowPrice", ""))
        if "highPrice" not in have:
            q.append(("highPrice", "9999999999"))
        return base + "?" + urlencode(q, doseq=True)

    item_type = (p.get("itemType") or "").strip()
    if not item_type:
        return ""
    base = f"https://mapleland.gg/items/{quote(item_type)}"
    q = []
    for k in ORDER:
        if k == "itemType":
            continue
        v = p.get(k, "")
        if v in (None, ""):
            continue
        q.append((k, v))
    if "lowPrice" not in p:
        q.append(("lowPrice", ""))
    if "highPrice" not in p:
        q.append(("highPrice", "9999999999"))
    if "lowLevel" in p and "highLevel" not in p:
        q.append(("highLevel", p.get("lowLevel", "")))
    return base + "?" + urlencode(q, doseq=True)


def _run_search(df_items: pd.DataFrame, query: str) -> pd.DataFrame:
    q = (query or "").strip()
    if q:
        m = mask_for_query(df_items, q)
        df_filtered = df_items[m].reset_index(drop=True)
    else:
        df_filtered = df_items.copy()
    groups = group_by_sgr(df_filtered)
    st.session_state["df_filtered"] = df_filtered
    st.session_state["groups"] = groups
    st.session_state["last_query"] = q
    return groups


def _paginate_df(df: pd.DataFrame, page: int, page_size: int) -> tuple[pd.DataFrame, int, int, int]:
    total = len(df)
    pages = (total + page_size - 1) // page_size if total > 0 else 0
    if pages == 0:
        return df, 0, 0, 0
    page = max(1, min(page, pages))
    start = (page - 1) * page_size
    end = start + page_size
    return df.iloc[start:end].reset_index(drop=True), page, pages, total


def _paginate_list(rows: list[dict], page: int, page_size: int) -> tuple[list[dict], int, int, int]:
    total = len(rows)
    pages = (total + page_size - 1) // page_size if total > 0 else 0
    if pages == 0:
        return rows, 0, 0, 0
    page = max(1, min(page, pages))
    start = (page - 1) * page_size
    end = start + page_size
    return rows[start:end], page, pages, total


def _save_history_snapshot(
    query: str,
    tokens: list[str],
    api_sell: pd.DataFrame,
    api_buy: pd.DataFrame,
    proc_rows: list[dict],
    packet_rows: pd.DataFrame,
) -> None:
    q = (query or "").strip()
    stat_str = " ".join(tokens)
    if not q and not stat_str:
        return
    hist = st.session_state.get("history", [])
    item = {
        "검색어": q,
        "스탯": stat_str,
        "tokens": list(tokens),
        "api_sell": api_sell.copy() if isinstance(api_sell, pd.DataFrame) else pd.DataFrame(),
        "api_buy": api_buy.copy() if isinstance(api_buy, pd.DataFrame) else pd.DataFrame(),
        "proc_rows": list(proc_rows) if isinstance(proc_rows, list) else [],
        "packet_rows": packet_rows.copy() if isinstance(packet_rows, pd.DataFrame) else pd.DataFrame(),
    }
    def _same(a, b):
        return a.get("검색어") == b.get("검색어") and a.get("스탯") == b.get("스탯")
    hist = [item] + [h for h in hist if not _same(h, item)]
    st.session_state["history"] = hist[:10]


def _on_history_change() -> None:
    history = st.session_state.get("history", [])
    idx = st.session_state.get("hist_idx")
    if history and idx is not None:
        try:
            idx = int(idx)
        except Exception:
            return
        if idx < 0 or idx >= len(history):
            st.session_state["hist_idx"] = 0
            return
        h = history[idx]
        _queue_apply_history(h)


def _apply_history_idx(idx) -> None:
    history = st.session_state.get("history", [])
    if not history or idx is None:
        return
    try:
        idx = int(idx)
    except Exception:
        return
    if idx < 0 or idx >= len(history):
        return
    h = history[idx]
    _queue_apply_history(h)


def _queue_apply_history(h: dict) -> None:
    tokens = h.get("tokens", [])
    pending = {
        "query": h.get("검색어", ""),
        "stat1": tokens[0] if len(tokens) > 0 else "",
        "stat2": tokens[1] if len(tokens) > 1 else "",
        "stat3": tokens[2] if len(tokens) > 2 else "",
        "reset_pages": True,
    }
    if "api_sell" in h or "proc_rows" in h or "packet_rows" in h:
        pending["api_sell"] = h.get("api_sell", pd.DataFrame())
        pending["api_buy"] = h.get("api_buy", pd.DataFrame())
        pending["proc_rows"] = h.get("proc_rows", [])
        pending["packet_rows"] = h.get("packet_rows", pd.DataFrame())
        pending["use_cached"] = True
    st.session_state["pending_apply"] = pending
    st.session_state["auto_search"] = True
    st.session_state["run_now"] = False


def _trigger_search() -> None:
    st.session_state["auto_search"] = True
    st.session_state["run_now"] = True


def _ensure_sales_loaded(sales_path: str) -> None:
    if sales_store.is_duckdb():
        return
    try:
        sales_store.get_sales()
    except Exception:
        sales_store.preload_sales(sales_path, columns=sales_store.MIN_COLUMNS_FOR_PROCESSOR)


def _base_stats_from_query(df_items: pd.DataFrame, query: str) -> tuple[str, str]:
    q = (query or "").strip()
    if not q:
        return "", "검색어를 입력하세요."
    try:
        m = mask_for_query(df_items, q)
    except Exception:
        m = df_items["itemName"].astype(str).str.contains(q, regex=False)
    df_filtered = df_items[m]
    if df_filtered.empty:
        return "", f"대표아이템을 찾지 못했습니다: {q}"
    groups = group_by_sgr(df_filtered)
    if groups.empty:
        return "", f"대표아이템을 찾지 못했습니다: {q}"
    if len(groups) != 1:
        cand = ", ".join(map(str, groups["대표아이템명"].head(5).tolist()))
        return "", f"여러 개가 일치합니다: {cand}"
    row_g = groups.iloc[0]
    item_name = str(row_g.get("대표아이템명", ""))
    cond = df_filtered["itemName"].astype(str).eq(item_name)
    for col in ("sheet", "gender", "reqLevel"):
        if col in df_filtered.columns and col in groups.columns:
            cond &= (df_filtered[col] == row_g[col])
    df_one = df_filtered[cond]
    if df_one.empty:
        df_one = df_filtered.iloc[[0]]
    row = df_one.iloc[0]
    text = _format_base_stats_from_row(row)
    title = f"{item_name} ({row_g.get('sheet','')})"
    return text, title


def _apply_api_post_filters(df: pd.DataFrame, tokens: list[str], current_query: str, df_items: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    d = df.copy()

    q = (current_query or "").strip()
    if q:
        def _norm_name(s: str) -> str:
            raw = str(s or "").lower().replace("@", "")
            paren_parts = re.findall(r"\(([^)]*)\)", raw)
            main = re.sub(r"\([^)]*\)", "", raw)

            def _strip(x: str) -> str:
                x = (
                    x.replace("남자", "")
                    .replace("여자", "")
                    .replace("공용", "")
                    .replace("남", "")
                    .replace("여", "")
                )
                return re.sub(r"[^0-9a-zA-Z가-힣]+", "", x)

            return _strip(main) + _strip("".join(paren_parts))

        q_norm = _norm_name(q)
        try:
            query_names = pd.DataFrame(
                {"itemName": d["itemName"].astype(str)},
                index=d.index,
            )
            keep_name = mask_for_query(query_names, q)
        except Exception:
            keep_name = d["itemName"].astype(str).str.contains(
                q_norm,
                regex=False,
            )
        d = d[keep_name]
        if d.empty:
            return pd.DataFrame()

    try:
        base_df = df_items.copy()
        rename_map = {
            "힘": "baseSTR", "덱": "baseDEX", "인": "baseINT", "럭": "baseLUK", "명": "baseACC", "마": "baseMAD",
            "STR": "baseSTR", "DEX": "baseDEX", "INT": "baseINT", "LUK": "baseLUK", "ACC": "baseACC", "MAD": "baseMAD",
        }
        for k, v in list(rename_map.items()):
            if k in base_df.columns:
                base_df = base_df.rename(columns={k: v})
        left_on = ["sheet", "repName"]
        right_on = ["sheet", "itemName"]
        need_cols = set(right_on + ["baseSTR", "baseDEX", "baseINT", "baseLUK", "baseACC", "baseMAD"])
        base_mini = base_df[[c for c in need_cols if c in base_df.columns]].drop_duplicates()
        d = d.merge(base_mini, left_on=left_on, right_on=right_on, how="left")
        if "itemName_x" in d.columns:
            d.rename(columns={"itemName_x": "itemName"}, inplace=True)
        if "itemName_y" in d.columns:
            d.drop(columns=["itemName_y"], errors="ignore", inplace=True)
    except Exception:
        pass

    for c in ["baseSTR", "baseDEX", "baseINT", "baseLUK", "baseACC", "baseMAD"]:
        if c not in d.columns:
            d[c] = 0
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0).astype(int)

    for col in ("incDEX", "incACC", "incINT", "incLUK", "incMAD"):
        if col not in d.columns:
            d[col] = 0
        d[col] = pd.to_numeric(d[col], errors="coerce").fillna(0).astype(int)

    d["addDEX"] = d["incDEX"] - d["baseDEX"]
    d["addACC"] = d["incACC"] - d["baseACC"]
    d["addINT"] = d["incINT"] - d["baseINT"]
    d["addLUK"] = d["incLUK"] - d["baseLUK"]
    d["addMAD"] = d["incMAD"] - d["baseMAD"]

    keep = pd.Series(True, index=d.index)
    for t in (tokens or []):
        t = str(t or "").strip()

        zero_components = parse_zero_option_token(t)
        if zero_components:
            opt_col = "optionSummarize" if "optionSummarize" in d.columns else "option"
            if opt_col in d.columns:
                keep &= ~d[opt_col].map(lambda x: option_has_any_component(x, zero_components))
            continue

        m = re.fullmatch(r"신점\s*(\d+)", t)
        if m:
            keep &= (d["addDEX"] >= d["addACC"])
            continue

        m = re.fullmatch(r"신민\s*(\d+)", t)
        if m:
            keep &= (d["addACC"] >= d["addDEX"])
            continue

        m = re.fullmatch(r"법지\s*(\d+)", t)
        if m:
            keep &= (d["addINT"] >= d["addLUK"])
            continue

        m = re.fullmatch(r"법행\s*(\d+)", t)
        if m:
            keep &= (d["addLUK"] >= d["addINT"])
            continue

        m = re.fullmatch(r"법신\s*(\d+)", t)
        if m:
            v = int(m.group(1))
            keep &= ((d["addINT"] + d["addLUK"] + d["addMAD"]) == v)
            continue

    return d[keep].reset_index(drop=True)


def _fetch_api_for_groups(df_groups_sel: pd.DataFrame, tokens: list[str], df_items: pd.DataFrame) -> pd.DataFrame:
    out_records = []

    for _, row in df_groups_sel.iterrows():
        job_val = ""
        try:
            rep_name = str(row.get("대표아이템명", "")).strip()
            cand = df_items[df_items["itemName"].astype(str) == rep_name]
            if "직업" in cand.columns and not cand["직업"].dropna().empty:
                j = str(cand["직업"].dropna().iloc[0]).strip()
                if j in {"전사", "법사", "궁수", "도적"}:
                    job_val = j
        except Exception:
            job_val = ""

        params = build_params(
            row.get("sheet", ""),
            row.get("gender", ""),
            int(float(row.get("reqLevel", 0) or 0)),
            item_name=row.get("대표아이템명", ""),
            stat_tokens=tokens,
            job=job_val,
        )
        if not params:
            continue

        data = fetch_json_with_retries(params, retries=2, delay=0.6)
        if not data:
            continue

        entries = []
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            for k in ("data", "results", "items", "list", "trades"):
                v = data.get(k)
                if isinstance(v, list):
                    entries = v
                    break
            else:
                entries = [data]

        for d in entries:
            if isinstance(d, dict):
                rec = parse_trade_json(d)
                rec["sheet"] = row.get("sheet", "")
                rec["repName"] = row.get("대표아이템명", "")
                rec["gender"] = row.get("gender", "")
                rec["reqLevel"] = int(float(row.get("reqLevel", 0) or 0))
                out_records.append(rec)

    return pd.DataFrame(out_records)


st.set_page_config(page_title="아이템 검색 웹", layout="wide")
st.markdown(
    """
    <style>
      body, .stApp { background-color: #f2f2f2; color: #111; }
      .block-container { max-width: 100%; padding-top: 0.2rem; padding-bottom: 0.4rem; }
      div[data-testid="stDataFrame"] { font-size: 11px; }
      .stMarkdown p { margin-bottom: 0.1rem; }
      div[data-testid="stMarkdownContainer"] h2 { margin-bottom: 0.1rem; }
      div[data-testid="stToolbar"] { visibility: hidden; height: 0px; }
      .stButton button { padding: 2px 4px; font-size: 6px; white-space: nowrap; }
      button[kind="primary"] { padding: 5px 14px; font-size: 11px; }
      .stRadio label { font-size: 9px; }
      .link-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 6px 16px;
        min-height: 36px;
        background: #e6e6e6;
        border: 1px solid #cfcfcf;
        border-radius: 6px;
        color: #111;
        text-decoration: none;
        font-size: 12px;
        white-space: nowrap;
        line-height: 1.1;
      }
      .link-btn.disabled { color: #888; background: #f2f2f2; border-color: #ddd; }
      .base-stat { font-size: 15px; font-weight: 700; color: #000; line-height: 1.3; }
      .base-stat-empty { font-size: 11px; font-weight: 700; color: #000; }
      .stTabs [data-baseweb="tab-list"] { gap: 6px; }
      .stTabs [data-baseweb="tab"] {
        background: #e7e9f7;
        border-radius: 7px 7px 0 0;
        padding: 7px 18px;
        min-height: 38px;
        line-height: 1.25;
        font-weight: 700;
      }
      .stTabs [data-baseweb="tab"] p {
        margin: 0;
        line-height: 1.25;
      }
      .stTabs [aria-selected="true"] {
        background: #5b6fe5 !important;
        color: white !important;
      }
      div[data-testid="stDataFrame"] table tbody tr td:nth-child(4) { font-weight: 700; }
      div[data-testid="stDataFrame"] { font-size: 15px; font-weight: 650; }
      div[data-testid="stDataFrame"] thead tr th { pointer-events: none; }
      div[data-testid="stDataFrame"] thead { cursor: default; }
    </style>
    """,
    unsafe_allow_html=True,
)

excel_file = BASE_DIR / "item.xlsx"
try:
    data_snapshot = ensure_data_snapshot(DATA_CACHE_DIR, check_interval=60 * 60)
except Exception as exc:
    st.error(f"최신 데이터 스냅샷을 받지 못했습니다: {exc}")
    st.stop()

sales_file = _find_sales_file(data_snapshot.directory)
PACKET_ACTIVE_FILE = data_snapshot.path("packet_active.parquet")
PACKET_COMPLETED_FILE = data_snapshot.path("packet_completed.parquet")
GEM_PRICES_FILE = data_snapshot.path("gem_prices.json")

if not excel_file.exists():
    st.error("item.xlsx 파일이 없습니다. 먼저 업로드/배치해주세요.")
    st.stop()
if not sales_file:
    st.error("data-latest에서 요약본.parquet를 찾을 수 없습니다.")
    st.stop()

mode = _init_sales_backend(str(sales_file), data_snapshot.version)
df_items = _load_items(str(excel_file))


def _watch_remote_data() -> None:
    try:
        latest = ensure_data_snapshot(DATA_CACHE_DIR, check_interval=60 * 60)
    except Exception:
        return
    if latest.version == data_snapshot.version:
        return
    st.session_state["run_now"] = True
    st.session_state["auto_search"] = True
    st.rerun()


if hasattr(st, "fragment"):
    st.fragment(run_every="120s")(_watch_remote_data)()


def _on_query_change() -> None:
    _trigger_search()
    try:
        text, title = _base_stats_from_query(df_items, st.session_state.get("query", ""))
    except Exception:
        return
    if text:
        st.session_state["base_stat_text"] = text
        st.session_state["base_stat_title"] = title
    else:
        st.session_state["base_stat_text"] = ""
        st.session_state["base_stat_title"] = ""

mtime_text = _format_mtime(sales_file)
ctime_text = _format_ctime(sales_file)
st.markdown(
    f"<div style='text-align:right;font-size:11px;color:#555;'>"
    f"build: {APP_BUILD} · data: {data_snapshot.version[:10]} · "
    f"parquet 생성일: {ctime_text} · 최종수정: {mtime_text} · backend: {mode}"
    f"</div>",
    unsafe_allow_html=True,
)

pending = st.session_state.pop("pending_apply", None)
if isinstance(pending, dict) and pending:
    for k, v in pending.items():
        if k in ("api_sell", "api_buy", "proc_rows", "packet_rows", "reset_pages"):
            continue
        st.session_state[k] = v
    if pending.get("use_cached"):
        st.session_state["api_sell"] = pending.get("api_sell", pd.DataFrame())
        st.session_state["api_buy"] = pending.get("api_buy", pd.DataFrame())
        st.session_state["proc_rows"] = pending.get("proc_rows", [])
        st.session_state["packet_rows"] = pending.get("packet_rows", pd.DataFrame())
        st.session_state["run_now"] = False
    if pending.get("reset_pages"):
        st.session_state["proc_page"] = 1
        st.session_state["page_sell"] = 1
        st.session_state["page_buy"] = 1
        st.session_state["page_parquet"] = 1
    try:
        text, title = _base_stats_from_query(df_items, st.session_state.get("query", ""))
    except Exception:
        text, title = "", ""
    st.session_state["base_stat_text"] = text or ""
    st.session_state["base_stat_title"] = title or ""
    st.session_state["auto_search"] = True

if st.session_state.get("auto_search"):
    _run_search(df_items, st.session_state.get("query", ""))
    st.session_state["auto_search"] = False

col_left, col_right = st.columns([1.0, 2.6], gap="small")

with col_left:
    base_text = st.session_state.get("base_stat_text", "")
    base_title = st.session_state.get("base_stat_title", "")

    col_q, col_s1, col_s2, col_s3 = st.columns([3, 1, 1, 1])
    with col_q:
        query = st.text_input(
            "검색어",
            key="query",
            on_change=_on_query_change,
            help=(
                "검색어@: 해당 글자로 끝나는 이름 · "
                "@검색어: 해당 글자로 시작하는 이름 · "
                "@검색어@: 이름 전체가 정확히 일치"
            ),
        )
    with col_s1:
        stat1 = st.text_input("스탯1", key="stat1", on_change=_trigger_search)
    with col_s2:
        stat2 = st.text_input("스탯2", key="stat2", on_change=_trigger_search)
    with col_s3:
        stat3 = st.text_input("스탯3", key="stat3", on_change=_trigger_search)

    tokens_for_link = _read_tokens(stat1, stat2, stat3)
    groups_for_link = st.session_state.get("groups")
    open_url = ""
    if groups_for_link is not None and not groups_for_link.empty:
        row = groups_for_link.iloc[0]
        open_url = _build_site_search_url(
            str(row.get("sheet", "")),
            str(row.get("gender", "")),
            int(float(row.get("reqLevel", 0) or 0)),
            str(row.get("대표아이템명", "")),
            tokens_for_link,
        )

    btn_col2, btn_col3, btn_stat_col = st.columns([1.5, 1.8, 3.5], gap="small")
    btn_base = btn_col2.button("대표아이템 스탯", type="primary", use_container_width=True)
    if open_url:
        safe_url = html.escape(open_url, quote=True)
        btn_col3.markdown(
            f"<a class='link-btn' href='{safe_url}' target='_blank'>검색 페이지 열기</a>",
            unsafe_allow_html=True,
        )
    else:
        btn_col3.markdown("<span class='link-btn disabled'>검색 페이지 열기</span>", unsafe_allow_html=True)
    if base_text:
        one_line = base_text.replace("\n", " / ")
        btn_stat_col.markdown(f"<div class='base-stat'>{one_line}</div>", unsafe_allow_html=True)
    else:
        btn_stat_col.markdown("<div class='base-stat-empty'>대표아이템 스탯 없음</div>", unsafe_allow_html=True)

    include_api = True
    st.toggle(
        "보석 적용 스탯으로 검색",
        key="packet_include_gems",
        on_change=_trigger_search,
        help="끄면 원래 추가스탯, 켜면 추가스탯+보석스탯을 정확히 검색합니다.",
    )

    if btn_base:
        text, title = _base_stats_from_query(df_items, st.session_state.get("query", ""))
        st.session_state["base_stat_text"] = text
        st.session_state["base_stat_title"] = title

    groups = st.session_state.get("groups")
    history = st.session_state.get("history", [])

    col_hist_title, col_hist_info = st.columns([1, 1])
    col_hist_title.markdown("<div style='font-weight:700;color:#000;font-size:12px;'>검색 기록</div>", unsafe_allow_html=True)
    if history:
        col_hist_info.markdown("<div style='color:#000;font-weight:700;font-size:11px;'>최근 9개</div>", unsafe_allow_html=True)
    else:
        col_hist_info.markdown("<div style='color:#000;font-weight:700;font-size:11px;'>검색 기록 없음</div>", unsafe_allow_html=True)

    if history:
        hist = history[:9]
        for r in range(3):
            cols = st.columns(3, gap="small")
            for c in range(3):
                i = r * 3 + c
                if i >= len(hist):
                    continue
                h = hist[i]
                label = f"{h.get('검색어','')} | {h.get('스탯','')}"
                if cols[c].button(label, key=f"hist_btn_{i}", use_container_width=True):
                    _apply_history_idx(i)
                    st.rerun()

with col_right:
    # 상단 탭이 화면 위쪽에 붙어 글자가 잘리지 않도록 검색 입력줄과 높이를 맞춘다.
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    sell_tab, buy_tab, parquet_tab = st.tabs(["🟢 판매 API", "🔵 구매 API", "🟣 Parquet"])

    market_specs = [
        (sell_tab, _api_view_df(st.session_state.get("api_sell", pd.DataFrame()), "time"), "page_sell", "sell"),
        (buy_tab, _api_view_df(st.session_state.get("api_buy", pd.DataFrame()), "time"), "page_buy", "buy"),
        (parquet_tab, _proc_view_df(st.session_state.get("proc_rows", [])), "page_parquet", "parquet"),
    ]
    for tab, market_view, page_key, key_prefix in market_specs:
        with tab:
            page = st.session_state.get(page_key, 1)
            page_view, page, pages, total = _paginate_df(market_view, page, 10)
            st.session_state[page_key] = page
            nav = st.columns([0.7, 0.7, 2, 7])
            if nav[0].button("◀", key=f"{key_prefix}_prev", type="primary") and page > 1:
                st.session_state[page_key] = page - 1
                st.rerun()
            if nav[1].button("▶", key=f"{key_prefix}_next", type="primary") and pages and page < pages:
                st.session_state[page_key] = page + 1
                st.rerun()
            nav[2].caption(f"{page}/{pages} · 총 {total}")
            st.dataframe(
                _style_status_rows(page_view),
                use_container_width=True,
                hide_index=True,
                height=385,
                key=f"market_df_{key_prefix}_{st.session_state.get('api_render_id', 0)}",
                column_config={
                    "색": st.column_config.Column("색", width="small"),
                    "상태": st.column_config.Column("상태", width="small"),
                    "아이템": st.column_config.Column("아이템", width="medium"),
                    "스탯": st.column_config.Column("스탯", width="medium"),
                    "가격(만)": st.column_config.Column("가격(만)", width="small"),
                    "코멘트": st.column_config.Column("코멘트", width="small"),
                    "판매자": st.column_config.Column("판매자", width="small"),
                    "흥정": st.column_config.Column("흥정", width="small"),
                    "경신": st.column_config.Column("경신", width="small"),
                    "등록": st.column_config.Column("등록", width="small"),
                    "서버": st.column_config.Column("서버", width="small"),
                    "프로필": st.column_config.LinkColumn("프로필", display_text="열기", width="small"),
                    "링크": st.column_config.LinkColumn("링크", display_text="열기", width="small"),
                },
            )

groups = st.session_state.get("groups")
if groups is not None and not groups.empty:
    tokens = _read_tokens(
        st.session_state.get("stat1", ""),
        st.session_state.get("stat2", ""),
        st.session_state.get("stat3", ""),
    )
    df_filtered = st.session_state.get("df_filtered", df_items)
    run_now = st.session_state.get("run_now", False)

    if run_now:
        row = groups.iloc[0]
        target_groups = pd.DataFrame([row])[["대표아이템명", "sheet", "gender", "reqLevel"]]

        with st.spinner("계산 중..."):
            api_sell = pd.DataFrame()
            api_buy = pd.DataFrame()
            if include_api:
                df_api = _fetch_api_for_groups(target_groups, tokens, df_items)
                if not df_api.empty:
                    df_api = _apply_api_post_filters(df_api, tokens, st.session_state.get("query", ""), df_items)
                    sell_mask = df_api.get("tradeType", pd.Series(["sell"] * len(df_api))).astype(str).str.lower().eq("sell")
                    buy_mask = df_api.get("tradeType", pd.Series(["sell"] * len(df_api))).astype(str).str.lower().eq("buy")
                    api_sell = df_api[sell_mask].reset_index(drop=True)
                    api_buy = df_api[buy_mask].reset_index(drop=True)

            _ensure_sales_loaded(str(sales_file))
            proc_rows = []
            packet_codes: list[int] = []
            for _, row in target_groups.iterrows():
                selected_items = _selected_items_from_group(df_filtered, row)
                if not selected_items:
                    continue
                result = process_items(tokens, row.get("sheet", ""), selected_items, str(excel_file), str(sales_file))
                proc_rows.extend(result.get(4, []) or [])
                packet_codes.extend(_selected_item_codes_from_group(df_filtered, row))

            packet_rows = pd.DataFrame()
            packet_dedup_count = 0
            if PACKET_ACTIVE_FILE.exists() and PACKET_COMPLETED_FILE.exists() and packet_codes:
                packet_rows, packet_dedup_count = search_packet_data(
                    PACKET_ACTIVE_FILE,
                    PACKET_COMPLETED_FILE,
                    packet_codes,
                    tokens,
                    include_gems=bool(
                        st.session_state.get("packet_include_gems", False)
                    ),
                )

        st.session_state["api_sell"] = api_sell
        st.session_state["api_buy"] = api_buy
        st.session_state["proc_rows"] = proc_rows
        st.session_state["packet_rows"] = packet_rows
        st.session_state["packet_dedup_count"] = packet_dedup_count
        st.session_state["proc_page"] = 1
        st.session_state["page_sell"] = 1
        st.session_state["page_buy"] = 1
        st.session_state["page_parquet"] = 1
        _save_history_snapshot(
            st.session_state.get("query", ""),
            tokens,
            api_sell,
            api_buy,
            proc_rows,
            packet_rows,
        )
        st.session_state["run_now"] = False
        st.rerun()

packet_header = st.columns([1.4, 1.6, 1.8, 2.6, 4])
packet_header[0].markdown("### 패킷 매물")
completed_only = packet_header[1].toggle("Completed만 보기", key="packet_completed_only")
if packet_header[2].button("보석 시세 보기", use_container_width=True):
    st.session_state["show_gem_prices"] = not bool(
        st.session_state.get("show_gem_prices", False)
    )
packet_rows = st.session_state.get("packet_rows", pd.DataFrame())
packet_dedup = int(st.session_state.get("packet_dedup_count", 0) or 0)
packet_header[3].caption(f"중복 Active 제외 {packet_dedup:,}건")
packet_header[4].caption("표시·정렬: packet_time · 3일 중복 판정: 패킷 내부시간")

if st.session_state.get("show_gem_prices"):
    try:
        raw_gem_prices = json.loads(GEM_PRICES_FILE.read_text(encoding="utf-8"))
        grade_names = {6000: "하급", 16000: "중급", 26000: "상급"}
        gem_rows = []
        for raw_code, price in sorted(raw_gem_prices.items(), key=lambda pair: int(pair[0])):
            code = int(raw_code)
            base = max(value for value in grade_names if value <= code)
            suffix = code - base
            label, values = _packet_store.GEM_OPTION_LABELS[suffix]
            grade_index = (6000, 16000, 26000).index(base)
            gem_rows.append({
                "등급": grade_names[base],
                "옵션": f"{label}+{values[grade_index]}",
                "시세(만)": int(round(int(price) / 10_000)),
            })
        st.dataframe(
            pd.DataFrame(gem_rows),
            use_container_width=True,
            hide_index=True,
            height=260,
        )
    except Exception as exc:
        st.warning(f"보석 시세를 읽지 못했습니다: {exc}")

if isinstance(packet_rows, pd.DataFrame) and not packet_rows.empty:
    visible_packet = packet_rows.copy()
    if completed_only:
        visible_packet = visible_packet[visible_packet["status"].eq("completed")].copy()
    visible_packet["_sort_time"] = pd.to_datetime(visible_packet["captured_at"], errors="coerce")
    visible_packet = visible_packet.sort_values("_sort_time", ascending=False).drop(columns="_sort_time")
    view = packet_view(
        visible_packet,
        include_gems=bool(st.session_state.get("packet_include_gems", False)),
    )
    st.dataframe(
        _style_status_rows(view),
        use_container_width=True,
        hide_index=True,
        height=520,
        row_height=44,
        column_order=[
            "상태",
            "패킷시간",
            "판매소요",
            "아이템",
            "추가스탯",
            "판매가(만)",
            "대략시세(만)",
            "보석",
            "보석비(원가, 만)",
            "인정보석가치(90%, 만)",
            "찐판매가(만)",
            "업횟",
            "작횟",
        ],
        column_config={
            "상태": st.column_config.Column("상태", width="small"),
            "패킷시간": st.column_config.Column("패킷시간", width="small"),
            "판매소요": st.column_config.Column("판매소요", width="small"),
            "아이템": st.column_config.Column("아이템", width="medium"),
            "추가스탯": st.column_config.Column("추가스탯", width="medium"),
            "판매가(만)": st.column_config.NumberColumn(
                "판매가(만)", format="localized", width="small"
            ),
            "대략시세(만)": st.column_config.NumberColumn(
                "대략시세(만)", format="localized", width="small"
            ),
            "보석": st.column_config.Column("보석", width="medium"),
            "보석비(원가, 만)": st.column_config.NumberColumn(
                "보석원가(만)", format="localized", width="small"
            ),
            "인정보석가치(90%, 만)": st.column_config.NumberColumn(
                "보석인정가(90%)", format="localized", width="small"
            ),
            "찐판매가(만)": st.column_config.NumberColumn(
                "찐판매가(만)", format="localized", width="small"
            ),
            "업횟": st.column_config.NumberColumn("업횟", width="small"),
            "작횟": st.column_config.NumberColumn("작횟", width="small"),
        },
    )
elif PACKET_ACTIVE_FILE.exists() or PACKET_COMPLETED_FILE.exists():
    st.info("조건에 맞는 패킷 장비가 없습니다.")
else:
    st.info("packet_active.parquet / packet_completed.parquet를 생성하면 패킷 매물이 표시됩니다.")

if groups is None:
    st.info("검색어를 입력하고 검색 버튼을 눌러주세요.")
