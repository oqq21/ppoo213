import os
import re
from typing import Optional
import streamlit as st
import pandas as pd

from your_app.common.data_loader import load_item_data
from your_app.common.query_utils import mask_for_query
from your_app.domain.grouping import group_by_sgr
from your_app.api.client import build_params, fetch_json_with_retries, parse_trade_json
from your_app.processing.legacy_processor import process_items
from your_app.processing import sales_store

PREFERRED_PARQUET = ["sales_10000.parquet", "요약본.parquet", "data.parquet"]


def _find_sales_file() -> Optional[str]:
    for name in PREFERRED_PARQUET:
        if os.path.exists(name):
            return name
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
st.title("아이템 검색/가공 (웹)")

excel_file = "item.xlsx"
sales_file = _find_sales_file()

with st.sidebar:
    st.header("파일 상태")
    st.write(f"item.xlsx: {'OK' if os.path.exists(excel_file) else 'MISSING'}")
    st.write(f"parquet: {sales_file if sales_file else 'MISSING'}")

if not os.path.exists(excel_file):
    st.error("item.xlsx 파일이 없습니다. 먼저 업로드/배치해주세요.")
    st.stop()
if not sales_file:
    st.error("parquet 파일을 찾을 수 없습니다. (sales_10000.parquet / 요약본.parquet / data.parquet)")
    st.stop()

mode = _init_sales_backend(sales_file)

df_items = _load_items(excel_file)

query = st.text_input("아이템 검색어", key="query")
col1, col2, col3 = st.columns(3)
with col1:
    stat1 = st.text_input("스탯1", key="stat1")
with col2:
    stat2 = st.text_input("스탯2", key="stat2")
with col3:
    stat3 = st.text_input("스탯3", key="stat3")

include_api = st.checkbox("API 조회 포함", value=True)

if st.button("검색", type="primary"):
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


groups = st.session_state.get("groups")
if groups is not None:
    st.subheader("그룹 목록")
    st.dataframe(groups, use_container_width=True)

    if not groups.empty:
        labels = [(_format_group_label(row)) for _, row in groups.iterrows()]
        sel_idx = st.selectbox("그룹 선택", options=list(range(len(labels))), format_func=lambda i: labels[i])
        run_all = st.button("전체 조회", key="run_all")
        run_one = st.button("선택 조회", key="run_one")

        if run_all or run_one:
            tokens = _read_tokens(stat1, stat2, stat3)
            df_filtered = st.session_state.get("df_filtered", df_items)
            if run_all:
                target_groups = groups[["대표아이템명", "sheet", "gender", "reqLevel"]]
            else:
                row = groups.iloc[int(sel_idx)]
                target_groups = pd.DataFrame([row])[ ["대표아이템명", "sheet", "gender", "reqLevel"] ]

            with st.spinner("계산 중..."):
                api_sell = pd.DataFrame()
                api_buy = pd.DataFrame()
                if include_api:
                    df_api = _fetch_api_for_groups(target_groups, tokens, df_items)
                    if not df_api.empty:
                        df_api = _apply_api_post_filters(df_api, tokens, query, df_items)
                        sell_mask = df_api.get("tradeType", pd.Series(["sell"] * len(df_api))).astype(str).str.lower().eq("sell")
                        buy_mask = df_api.get("tradeType", pd.Series(["sell"] * len(df_api))).astype(str).str.lower().eq("buy")
                        api_sell = df_api[sell_mask].reset_index(drop=True)
                        api_buy = df_api[buy_mask].reset_index(drop=True)

                # 가공 결과 (slot4)
                proc_rows = []
                for _, row in target_groups.iterrows():
                    selected_items = _selected_items_from_group(df_filtered, row)
                    if not selected_items:
                        continue
                    result = process_items(tokens, row.get("sheet", ""), selected_items, excel_file, sales_file)
                    proc_rows.extend(result.get(4, []) or [])

            if include_api:
                st.subheader("API 결과 - 판매")
                st.dataframe(api_sell, use_container_width=True)
                st.subheader("API 결과 - 구매")
                st.dataframe(api_buy, use_container_width=True)

            st.subheader("가공 결과 (slot4)")
            if proc_rows:
                df_proc = pd.DataFrame(proc_rows)
                # 기본 정렬: 최신 날짜 우선
                if "date_raw" in df_proc.columns:
                    df_proc = df_proc.sort_values("date_raw", ascending=False)
                st.dataframe(df_proc, use_container_width=True)
            else:
                st.info("가공 결과가 없습니다.")

else:
    st.info("검색어를 입력하고 검색 버튼을 눌러주세요.")
