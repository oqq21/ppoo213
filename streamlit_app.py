import os
import re
import sys
import html
from typing import Optional
from pathlib import Path
from datetime import datetime
import streamlit as st
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from your_app.common.data_loader import load_item_data
from your_app.common.query_utils import mask_for_query
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

PREFERRED_PARQUET = ["요약본.parquet"]


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
def _init_sales_backend(sales_path: str) -> str:
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
        if sec < 3600:
            return f"{max(1, sec // 60)}분 전"
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


MY_PROFILE_ORDER = {
    "58656317-8ca3-4bde-be58-8052dd6870a8": 1,
    "b7b9d9a7-ba69-487a-8647-9b8324941767": 2,
    "b86be19e-fee1-46c8-93c4-1d59e436b7c3": 3,
    "6ddf963d-186c-45a9-9562-ff01ba0b13c4": 4,
    "64091862-a78f-4627-8c8e-6dcef0e17a5f": 5,
    "d1c30b32-e211-4a4a-b23a-455a61c93370": 6,
    "4a0243f2-b679-44b7-91a1-efc4a9e1e83a": 7,
    "abbf6597-5ed8-4016-99ed-7225ee81b779": 8,
    "4d275574-92ae-4241-8746-a0372e9b7ad8": 9,
    "4406877c-c9e5-4d5b-85a9-4e5c8261d611": 10,
    "6415c3f1-d86c-494b-985f-2930c4ea7728": 11,
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
    out = pd.DataFrame({
        "일전": df.get("days_ago", ""),
        "상태": status,
        "아이템": df.get("item", ""),
        "가격(만)": df.get("price", "").map(_emph_price) if "price" in df.columns else "",
        "판매자": df.get("seller", ""),
        "스탯": df.get("stats", ""),
        "비고": df.get("comment", ""),
        "링크": df.get("url", ""),
    })
    return out


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


def _save_history_snapshot(query: str, tokens: list[str], api_sell: pd.DataFrame, api_buy: pd.DataFrame, proc_rows: list[dict]) -> None:
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
    if "api_sell" in h or "proc_rows" in h:
        pending["api_sell"] = h.get("api_sell", pd.DataFrame())
        pending["api_buy"] = h.get("api_buy", pd.DataFrame())
        pending["proc_rows"] = h.get("proc_rows", [])
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
            raw = str(s or "").lower()
            paren_parts = re.findall(r"\(([^)]*)\)", raw)
            main = re.sub(r"\([^)]*\)", "", raw)
            def _strip(x: str) -> str:
                x = x.replace("남자", "").replace("여자", "").replace("공용", "").replace("남", "").replace("여", "")
                return re.sub(r"[^0-9a-zA-Z가-힣]+", "", x)
            base = _strip(main)
            extra = _strip("".join(paren_parts))
            return base + extra
        q_norm = _norm_name(q)
        try:
            name_norm = d["itemName"].astype(str).map(_norm_name)
            keep_name = name_norm.str.contains(q_norm, regex=False)
        except Exception:
            keep_name = d["itemName"].astype(str).str.contains(q, regex=False)
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
      div[data-testid="stDataFrame"] table tbody tr td:nth-child(4) { font-weight: 700; }
      div[data-testid="stDataFrame"] thead tr th { pointer-events: none; }
      div[data-testid="stDataFrame"] thead { cursor: default; }
    </style>
    """,
    unsafe_allow_html=True,
)

excel_file = BASE_DIR / "item.xlsx"
sales_file = _find_sales_file(BASE_DIR)

if not excel_file.exists():
    st.error("item.xlsx 파일이 없습니다. 먼저 업로드/배치해주세요.")
    st.stop()
if not sales_file:
    st.error("parquet 파일을 찾을 수 없습니다. (sales_10000.parquet / 요약본.parquet / data.parquet)")
    st.stop()

mode = _init_sales_backend(str(sales_file))
df_items = _load_items(str(excel_file))


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
    f"parquet 생성일: {ctime_text} · 최종수정: {mtime_text} · backend: {mode}"
    f"</div>",
    unsafe_allow_html=True,
)

pending = st.session_state.pop("pending_apply", None)
if isinstance(pending, dict) and pending:
    for k, v in pending.items():
        if k in ("api_sell", "api_buy", "proc_rows", "reset_pages"):
            continue
        st.session_state[k] = v
    if pending.get("use_cached"):
        st.session_state["api_sell"] = pending.get("api_sell", pd.DataFrame())
        st.session_state["api_buy"] = pending.get("api_buy", pd.DataFrame())
        st.session_state["proc_rows"] = pending.get("proc_rows", [])
        st.session_state["run_now"] = False
    if pending.get("reset_pages"):
        st.session_state["proc_page"] = 1
        st.session_state["page_sell"] = 1
        st.session_state["page_buy"] = 1
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

col_left, col_right = st.columns([1.2, 2.1], gap="large")

with col_left:
    base_text = st.session_state.get("base_stat_text", "")
    base_title = st.session_state.get("base_stat_title", "")

    col_q, col_s1, col_s2, col_s3 = st.columns([3, 1, 1, 1])
    with col_q:
        query = st.text_input("검색어", key="query", on_change=_on_query_change)
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
    st.caption("가공 결과")
    proc_rows = st.session_state.get("proc_rows", [])
    page_size_proc = 10
    proc_page = st.session_state.get("proc_page", 1)
    page_rows, proc_page, proc_pages, proc_total = _paginate_list(proc_rows, proc_page, page_size_proc)
    st.session_state["proc_page"] = proc_page

    nav = st.columns([0.6, 0.6, 2, 8])
    prev_p = nav[0].button("◀", key="proc_prev", type="primary")
    next_p = nav[1].button("▶", key="proc_next", type="primary")
    nav[2].caption(f"{proc_page}/{proc_pages} · 총 {proc_total}")
    if prev_p and proc_page > 1:
        st.session_state["proc_page"] = proc_page - 1
        st.rerun()
    if next_p and proc_pages and proc_page < proc_pages:
        st.session_state["proc_page"] = proc_page + 1
        st.rerun()

    if page_rows:
        view = _proc_view_df(page_rows)
        st.dataframe(
            view,
            use_container_width=True,
            hide_index=True,
            height=420,
            column_config={"링크": st.column_config.LinkColumn("링크", display_text="열기")},
        )
    else:
        st.info("가공 결과가 없습니다.")

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
            for _, row in target_groups.iterrows():
                selected_items = _selected_items_from_group(df_filtered, row)
                if not selected_items:
                    continue
                result = process_items(tokens, row.get("sheet", ""), selected_items, str(excel_file), str(sales_file))
                proc_rows.extend(result.get(4, []) or [])

        st.session_state["api_sell"] = api_sell
        st.session_state["api_buy"] = api_buy
        st.session_state["proc_rows"] = proc_rows
        st.session_state["proc_page"] = 1
        st.session_state["page_sell"] = 1
        st.session_state["page_buy"] = 1
        _save_history_snapshot(st.session_state.get("query", ""), tokens, api_sell, api_buy, proc_rows)
        st.session_state["run_now"] = False
        st.rerun()

api_sell = st.session_state.get("api_sell", pd.DataFrame())
api_buy = st.session_state.get("api_buy", pd.DataFrame())

if include_api:
    if "api_sort" not in st.session_state:
        st.session_state["api_sort"] = "가격순"
    top = st.columns([1.2, 1, 6])
    with top[0]:
        api_kind = st.radio(
            "구분",
            ["판매", "구매"],
            horizontal=True,
            key="api_kind",
            label_visibility="collapsed",
        )
    with top[1]:
        api_sort = st.radio(
            "정렬",
            ["최신순", "가격순"],
            horizontal=True,
            key="api_sort",
            label_visibility="collapsed",
        )

    src = api_sell if api_kind == "판매" else api_buy
    view = _api_view_df(src, "price" if api_sort == "가격순" else "time")
    if api_sort == "가격순" and "가격(만)" in view.columns:
        view["_sort_price"] = view["가격(만)"].map(_price_to_number).fillna(0)
        view["_sort_my"] = view.get("색", "").astype(str).str.contains(MY_ACCOUNT_DOT, regex=False)
        view = view.sort_values(
            ["_sort_my", "_sort_price"],
            ascending=[False, True],
            kind="mergesort",
        ).drop(columns=["_sort_my", "_sort_price"], errors="ignore").reset_index(drop=True)
        st.session_state["api_render_id"] = st.session_state.get("api_render_id", 0) + 1
    page_key = "page_sell" if api_kind == "판매" else "page_buy"
    page = st.session_state.get(page_key, 1)
    page_view, page, pages, total = _paginate_df(view, page, 7)
    st.session_state[page_key] = page

    nav = st.columns([0.6, 0.6, 2, 8])
    prev_key = "sell_prev" if api_kind == "판매" else "buy_prev"
    next_key = "sell_next" if api_kind == "판매" else "buy_next"
    if nav[0].button("◀", key=prev_key, type="primary") and page > 1:
        st.session_state[page_key] = page - 1
        st.rerun()
    if nav[1].button("▶", key=next_key, type="primary") and pages and page < pages:
        st.session_state[page_key] = page + 1
        st.rerun()
    nav[2].caption(f"{page}/{pages} · 총 {total}")

    st.dataframe(
        page_view,
        use_container_width=True,
        hide_index=True,
        height=300,
        key=f"api_df_{api_kind}_{api_sort}_{st.session_state.get('api_render_id', 0)}",
        column_config={
            "색": st.column_config.Column("색", width="small"),
            "상태": st.column_config.Column("상태", width="small"),
            "스탯": st.column_config.Column("스탯", width="large"),
            "코멘트": st.column_config.Column("코멘트", width="medium"),
            "프로필": st.column_config.LinkColumn("프로필", display_text="열기", width="small"),
        },
    )

if groups is None:
    st.info("검색어를 입력하고 검색 버튼을 눌러주세요.")
